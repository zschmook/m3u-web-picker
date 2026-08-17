from __future__ import annotations

import re
from xml.etree import ElementTree

from flask import Flask, Response

import core
import sports
import sports.guide as sports_guide
from media import browser
from settings import load_settings
from sports import mlb_fake_stats
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


def _internal_mlb_hls_url(assigned_number: int) -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats/{int(assigned_number)}/stream.m3u8"


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
        mlb_stats_companions.append_xmltv(
            root,
            generated,
            str(settings.get("timezone", "America/New_York")),
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
    # was installed during app startup. Rebuild once so the .1 programme rows
    # are immediately visible without waiting for the next Sports Update.
    try:
        core.ensure_epg_exports_current(force=True)
    except Exception:
        # Route registration must remain available even if no guide exists yet.
        pass


def register_stats_guide_demo_routes(app: Flask) -> None:
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
