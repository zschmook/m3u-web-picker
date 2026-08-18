from __future__ import annotations

import re
from datetime import datetime, timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from flask import Flask, Response

import core
import sports
import sports.guide as sports_guide
from media import browser
from settings import load_settings
from sports import game_alert_demo
from sports import mlb_fake_stats
from sports import mlb_stats_carousel
from sports import mlb_stats_companions
from sports import nfl_demo_stats
from . import guide as guide_api


_DEMO_PLAY_URL = "/guide/play/stats-demo/1"
_FAKE_PLAY_URL = "/guide/play/stats-fake/1.2"
_MLB_PLAY_RE = re.compile(r"^/guide/play/stats/(\d+)$")
_installed = False
_original_curated_channels = None
_original_play_target_resolver = None
_original_build_sports_xmltv = None


def _internal_demo_hls_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats-demo/1/stream.m3u8"


def _internal_fake_hls_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats-fake/stream.m3u8"


def _internal_alert_demo_hls_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}{game_alert_demo.STREAM_PATH}"


def _internal_mlb_hls_url(assigned_number: int) -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats/{int(assigned_number)}/stream.m3u8"


def _internal_mlb_carousel_hls_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}{mlb_stats_carousel.STREAM_PATH}"


def _demo_channel() -> dict:
    return {
        "number": nfl_demo_stats.DEMO_GUIDE_NUMBER,
        "name": "NFL Stats Demo · Eagles at Ravens",
        "group": "Sports Stats Lab",
        "logo": "",
        "tvg_id": "m3u-picker-sports-stats-demo-1",
        "subtitle": "Completed ESPN game stats · Experimental",
        "generated": True,
        "play_url": _DEMO_PLAY_URL,
        "stats_demo": True,
    }


def _current_mlb_rows() -> list[dict]:
    return mlb_stats_companions.primary_mlb_rows(sports.generated_rows(core.DB_PATH))


def _current_mlb_row(assigned_number: int) -> dict | None:
    return mlb_stats_companions.primary_mlb_row_for_number(
        sports.generated_rows(core.DB_PATH),
        assigned_number,
    )


def _inject_guide_companions(items: list[dict]) -> list[dict]:
    rows = _current_mlb_rows()
    by_parent = {
        int(row.get("assigned_number") or 0): row
        for row in rows
    }
    output: list[dict] = []
    inserted: set[int] = set()

    for item in items:
        output.append(item)
        match = re.fullmatch(
            r"/guide/play/sports/(\d+)",
            str(item.get("play_url", "") or "").split("?", 1)[0],
        )
        if not match:
            continue
        number = int(match.group(1))
        row = by_parent.get(number)
        if row is None:
            continue
        output.append(mlb_stats_companions.guide_item(row))
        inserted.add(number)

    # Generated rows should already be present in the curated guide, but keep a
    # safe fallback so a temporary parent-row filtering quirk cannot hide stats.
    for number, row in sorted(by_parent.items()):
        if number not in inserted:
            output.append(mlb_stats_companions.guide_item(row))

    # 0.1 is a permanent MLB scoreboard carousel whenever an enabled Sports
    # Automation rule can produce MLB games. The rotation itself only consumes
    # generated MLB rows, so a Phillies-only rule stays Phillies-only.
    if mlb_stats_carousel.is_enabled(core.DB_PATH) and not any(
        str(item.get("play_url", "") or "") == mlb_stats_carousel.PLAY_URL
        for item in output
    ):
        output.insert(0, mlb_stats_carousel.guide_item())

    # 0.10 is a permanent experiment that wraps saved channel 1 with a rotating
    # set of simulated alerts. It is deliberately dumb: no score APIs, no rules,
    # just proof that a mostly-transparent alert surface can be burned into an
    # otherwise ordinary live channel.
    if not any(
        str(item.get("play_url", "") or "") == game_alert_demo.PLAY_URL
        for item in output
    ):
        insert_at = 1 if output and str(output[0].get("number", "") or "") == "0.1" else 0
        output.insert(insert_at, game_alert_demo.guide_item())

    # Permanent experiment channels: 1.1 is completed ESPN data; 1.2 is a fake
    # live MLB state machine that changes continuously without external input.
    if not any(str(item.get("play_url", "") or "") == _DEMO_PLAY_URL for item in output):
        insert_at = 0
        for index, item in enumerate(output):
            if str(item.get("number", "") or "") == "1":
                insert_at = index + 1
                break
        output.insert(insert_at, _demo_channel())

    if not any(str(item.get("play_url", "") or "") == _FAKE_PLAY_URL for item in output):
        insert_at = 0
        for index, item in enumerate(output):
            if str(item.get("play_url", "") or "") == _DEMO_PLAY_URL:
                insert_at = index + 1
                break
        output.insert(insert_at, mlb_fake_stats.guide_item())
    return output


def _xmltv_time(value: datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S %z")


def _append_mlb_carousel_xmltv(
    root: ElementTree.Element,
    timezone_name: str,
    *,
    generated_at: datetime | None = None,
) -> None:
    if not mlb_stats_carousel.is_enabled(core.DB_PATH):
        return
    if any(
        child.tag.rsplit("}", 1)[-1] == "channel"
        and child.attrib.get("id") == mlb_stats_carousel.TVG_ID
        for child in root
    ):
        return

    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    anchor = generated_at if isinstance(generated_at, datetime) else datetime.now(timezone)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone)
    else:
        anchor = anchor.astimezone(timezone)
    start = anchor - timedelta(hours=12)
    stop = anchor + timedelta(days=7)

    channel = ElementTree.Element("channel", {"id": mlb_stats_carousel.TVG_ID})
    ElementTree.SubElement(channel, "display-name", {"lang": "en"}).text = mlb_stats_carousel.DISPLAY_NAME
    ElementTree.SubElement(channel, "display-name", {"lang": "en"}).text = mlb_stats_carousel.CHANNEL_NUMBER

    channel_insert_at = len(root)
    for index, child in enumerate(list(root)):
        if child.tag.rsplit("}", 1)[-1] == "programme":
            channel_insert_at = index
            break
    root.insert(channel_insert_at, channel)

    programme = ElementTree.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(start),
            "stop": _xmltv_time(stop),
            "channel": mlb_stats_carousel.TVG_ID,
        },
    )
    ElementTree.SubElement(programme, "title", {"lang": "en"}).text = mlb_stats_carousel.DISPLAY_NAME
    ElementTree.SubElement(programme, "sub-title", {"lang": "en"}).text = "Rotating Live Scores"
    ElementTree.SubElement(programme, "desc", {"lang": "en"}).text = (
        "Rotating live MLB scoreboards for games admitted by the enabled Sports Automation rules."
    )
    for category in ("Sports", "Baseball", "MLB", "Live Scores"):
        ElementTree.SubElement(programme, "category", {"lang": "en"}).text = category


def _append_fake_mlb_xmltv(
    root: ElementTree.Element,
    timezone_name: str,
    *,
    generated_at: datetime | None = None,
) -> None:
    """Give lab channel 1.2 deterministic guide data for the experiment."""
    if any(
        child.tag.rsplit("}", 1)[-1] == "channel"
        and child.attrib.get("id") == mlb_fake_stats.TVG_ID
        for child in root
    ):
        return

    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    anchor = generated_at if isinstance(generated_at, datetime) else datetime.now(timezone)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone)
    else:
        anchor = anchor.astimezone(timezone)

    # The simulator is intentionally always available. Give it a broad lab-only
    # programme window so it cannot sit in the Picker/Jellyfin guide as an empty
    # channel while the fake stream itself remains playable.
    start = anchor - timedelta(hours=12)
    stop = anchor + timedelta(days=7)

    channel = ElementTree.Element("channel", {"id": mlb_fake_stats.TVG_ID})
    ElementTree.SubElement(channel, "display-name", {"lang": "en"}).text = mlb_fake_stats.DISPLAY_NAME
    ElementTree.SubElement(channel, "display-name", {"lang": "en"}).text = mlb_fake_stats.GUIDE_NUMBER

    channel_insert_at = len(root)
    for index, child in enumerate(list(root)):
        if child.tag.rsplit("}", 1)[-1] == "programme":
            channel_insert_at = index
            break
    root.insert(channel_insert_at, channel)

    programme = ElementTree.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(start),
            "stop": _xmltv_time(stop),
            "channel": mlb_fake_stats.TVG_ID,
        },
    )
    ElementTree.SubElement(programme, "title", {"lang": "en"}).text = (
        f"{mlb_fake_stats.DISPLAY_NAME} — Live Stats"
    )
    ElementTree.SubElement(programme, "sub-title", {"lang": "en"}).text = "Simulated Live Stats"
    ElementTree.SubElement(programme, "desc", {"lang": "en"}).text = (
        "Continuously changing simulated MLB statistics for testing the companion-channel guide and playback pipeline."
    )
    for category in ("Sports", "Baseball", "MLB", "Live Stats", "Simulation"):
        ElementTree.SubElement(programme, "category", {"lang": "en"}).text = category


def _install_xmltv_companions() -> None:
    global _original_build_sports_xmltv
    if _original_build_sports_xmltv is not None:
        return

    _original_build_sports_xmltv = sports_guide.build_sports_xmltv

    def build_sports_xmltv(generated, settings, *, generated_at=None):
        payload = _original_build_sports_xmltv(
            generated,
            settings,
            generated_at=generated_at,
        )
        root = ElementTree.fromstring(payload)
        timezone_name = str(settings.get("timezone", "America/New_York"))
        mlb_stats_companions.append_xmltv(
            root,
            generated,
            timezone_name,
        )
        _append_mlb_carousel_xmltv(
            root,
            timezone_name,
            generated_at=generated_at,
        )
        _append_fake_mlb_xmltv(
            root,
            timezone_name,
            generated_at=generated_at,
        )
        if hasattr(ElementTree, "indent"):
            ElementTree.indent(root, space="  ")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    # _write_prepared_epg_files resolves build_sports_xmltv in sports.guide's
    # module globals, while tests and external callers may use the facade.
    sports_guide.build_sports_xmltv = build_sports_xmltv
    sports.build_sports_xmltv = build_sports_xmltv


def install() -> None:
    """Install experimental TV Guide + XMLTV companions for MLB stats."""
    global _installed, _original_curated_channels, _original_play_target_resolver
    if _installed:
        return

    _install_xmltv_companions()
    _original_curated_channels = core.curated_channels_for_guide
    _original_play_target_resolver = guide_api._resolve_guide_play_target

    def curated_channels_for_guide() -> list[dict]:
        return _inject_guide_companions(list(_original_curated_channels()))

    def resolve_guide_play_target(play_url: str) -> str:
        value = str(play_url or "").split("?", 1)[0].strip()
        if value == game_alert_demo.PLAY_URL:
            return _internal_alert_demo_hls_url() if game_alert_demo.parent_target() else ""
        if value == mlb_stats_carousel.PLAY_URL:
            if mlb_stats_carousel.is_enabled(core.DB_PATH):
                return _internal_mlb_carousel_hls_url()
            return ""
        if value == _DEMO_PLAY_URL:
            return _internal_demo_hls_url()
        if value == _FAKE_PLAY_URL:
            return _internal_fake_hls_url()
        match = _MLB_PLAY_RE.fullmatch(value)
        if match:
            assigned = int(match.group(1))
            if _current_mlb_row(assigned) is not None:
                return _internal_mlb_hls_url(assigned)
            return ""
        return _original_play_target_resolver(play_url)

    core.curated_channels_for_guide = curated_channels_for_guide
    guide_api._resolve_guide_play_target = resolve_guide_play_target
    _installed = True

    # Existing XMLTV files may have been built before this experimental layer
    # was installed during app startup. Rebuild once so the synthetic stats
    # channels are immediately visible without waiting for the next Sports Update.
    try:
        core.ensure_epg_exports_current(force=True)
    except Exception:
        # Route registration must remain available even if no guide exists yet.
        pass


def register_stats_guide_demo_routes(app: Flask) -> None:
    @app.get(game_alert_demo.PLAY_URL)
    def guide_play_sports_alert_demo():
        if not game_alert_demo.parent_target():
            return Response(
                "Channel 1 is not available for the sports alert demo.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        return browser.response_for(_internal_alert_demo_hls_url())

    @app.get(mlb_stats_carousel.PLAY_URL)
    def guide_play_mlb_stats_carousel():
        if not mlb_stats_carousel.is_enabled(core.DB_PATH):
            return Response(
                "MLB live-score carousel not enabled.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        return browser.response_for(_internal_mlb_carousel_hls_url())

    @app.get(_DEMO_PLAY_URL)
    def guide_play_stats_demo():
        return browser.response_for(_internal_demo_hls_url())

    @app.get(_FAKE_PLAY_URL)
    def guide_play_stats_fake():
        return browser.response_for(_internal_fake_hls_url())

    @app.get("/guide/play/stats/<int:assigned_number>")
    def guide_play_mlb_stats(assigned_number: int):
        if _current_mlb_row(assigned_number) is None:
            return Response(
                "MLB stats companion not found.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        return browser.response_for(_internal_mlb_hls_url(assigned_number))
