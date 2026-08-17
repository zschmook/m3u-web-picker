from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


LIVE_STATS_SUFFIX = " — Live Stats"


def _value(value: object) -> str:
    return str(value or "").strip()


def logical_event_key(row: dict) -> str:
    event_key = _value(row.get("event_key"))
    if event_key:
        return event_key
    return "|".join(
        (
            _value(row.get("league_id")).lower(),
            _value(row.get("event_title") or row.get("display_name")),
            _value(row.get("event_start")),
        )
    )


def primary_mlb_rows(rows: Iterable[dict]) -> list[dict]:
    """Return one lowest-numbered companion parent per logical MLB event."""
    selected: dict[str, dict] = {}
    for raw in rows:
        row = dict(raw)
        if _value(row.get("league_id")).lower() != "mlb":
            continue
        try:
            assigned = int(row.get("assigned_number") or 0)
        except (TypeError, ValueError):
            continue
        if assigned <= 0:
            continue
        key = logical_event_key(row)
        current = selected.get(key)
        if current is None or assigned < int(current.get("assigned_number") or 0):
            selected[key] = row
    return sorted(selected.values(), key=lambda item: int(item.get("assigned_number") or 0))


def primary_mlb_row_for_number(rows: Iterable[dict], assigned_number: int) -> dict | None:
    number = int(assigned_number)
    for row in primary_mlb_rows(rows):
        if int(row.get("assigned_number") or 0) == number:
            return row
    return None


def stats_number(row: dict) -> str:
    return f"{int(row.get('assigned_number') or 0)}.1"


def stats_tvg_id(row: dict) -> str:
    digest = hashlib.sha256(logical_event_key(row).encode("utf-8")).hexdigest()[:24]
    return f"m3u-picker-sports-stats-{digest}"


def event_title(row: dict) -> str:
    return _value(row.get("event_title") or row.get("display_name") or "MLB Game")


def stats_title(row: dict) -> str:
    title = event_title(row)
    return title if title.endswith(LIVE_STATS_SUFFIX) else f"{title}{LIVE_STATS_SUFFIX}"


def stats_stream_path(row: dict) -> str:
    return f"/sports/stats/{int(row.get('assigned_number') or 0)}/stream.m3u8"


def stats_play_path(row: dict) -> str:
    return f"/guide/play/stats/{int(row.get('assigned_number') or 0)}"


def guide_item(row: dict) -> dict:
    title = stats_title(row)
    return {
        "number": stats_number(row),
        "name": title,
        "group": _value(row.get("group_title")) or "Sports Today",
        "logo": _value(row.get("tvg_logo")),
        "tvg_id": stats_tvg_id(row),
        "subtitle": "MLB live statistics",
        "generated": True,
        "play_url": stats_play_path(row),
        "stats_companion": True,
        "stats_parent": int(row.get("assigned_number") or 0),
        "sports_event_key": logical_event_key(row),
        # The Picker guide should always display the exact same programme window
        # as the parent game. guide_epg uses this explicit relationship instead
        # of depending on the synthetic .1 XMLTV record having been refreshed.
        "epg_mirror_tvg_id": _value(row.get("tvg_id")),
        "epg_mirror_title": title,
        "epg_mirror_subtitle": "Live Stats",
        "epg_mirror_description": f"Live statistics companion for {event_title(row)}.",
    }


def _datetime(value: object, timezone: ZoneInfo) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _value(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed


def event_window(row: dict, timezone: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """Return the same primary game window the parent sports guide prefers."""
    # Parent generated channels prefer a valid provider programme over the
    # logical event estimate. The .1 companion must make the same choice so its
    # guide block lines up exactly with the game feed instead of merely using a
    # nearby schedule/API anchor.
    programme = row.get("epg_programme")
    if isinstance(programme, dict):
        programme_start = _datetime(programme.get("start"), timezone)
        programme_stop = _datetime(programme.get("stop"), timezone)
        if (
            programme_start is not None
            and programme_stop is not None
            and programme_stop > programme_start
        ):
            return programme_start, programme_stop

    start = _datetime(row.get("event_start"), timezone)
    stop = _datetime(row.get("event_end"), timezone)
    if start is not None and stop is not None and stop > start:
        return start, stop

    if start is not None:
        return start, start + timedelta(hours=4)
    return None, None


def _xmltv_time(value: datetime) -> str:
    local = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
    return local.strftime("%Y%m%d%H%M%S %z")


def _add_text(parent: ElementTree.Element, tag: str, text: str, **attrs) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag, {key: str(value) for key, value in attrs.items()})
    element.text = str(text)
    return element


def append_xmltv(root: ElementTree.Element, generated: Iterable[dict], timezone_name: str) -> None:
    """Append one synthetic XMLTV channel/programme per logical MLB game."""
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    rows = primary_mlb_rows(generated)
    if not rows:
        return

    companion_channels: list[ElementTree.Element] = []
    for row in rows:
        channel = ElementTree.Element("channel", {"id": stats_tvg_id(row)})
        _add_text(channel, "display-name", stats_title(row), lang="en")
        _add_text(channel, "display-name", stats_number(row), lang="en")
        _add_text(channel, "display-name", f"CH {stats_number(row)}", lang="en")
        logo = _value(row.get("tvg_logo"))
        if logo:
            ElementTree.SubElement(channel, "icon", {"src": logo})
        companion_channels.append(channel)

    # XMLTV's normal ordering is all <channel> elements before <programme>.
    # Insert the synthetic channels at that boundary instead of appending them
    # after the parent's existing programme records.
    channel_insert_at = len(root)
    for index, child in enumerate(list(root)):
        if str(child.tag or "").rsplit("}", 1)[-1] == "programme":
            channel_insert_at = index
            break
    for offset, channel in enumerate(companion_channels):
        root.insert(channel_insert_at + offset, channel)

    for row in rows:
        start, stop = event_window(row, timezone)
        if start is None or stop is None or stop <= start:
            continue
        programme = ElementTree.SubElement(
            root,
            "programme",
            {
                "start": _xmltv_time(start),
                "stop": _xmltv_time(stop),
                "channel": stats_tvg_id(row),
            },
        )
        _add_text(programme, "title", stats_title(row), lang="en")
        _add_text(programme, "sub-title", "Live MLB statistics", lang="en")
        _add_text(
            programme,
            "desc",
            f"Live statistics companion for {event_title(row)}.",
            lang="en",
        )
        categories = ["Sports", "Baseball", "MLB", "Live Stats"]
        source_programme = row.get("epg_programme")
        if isinstance(source_programme, dict):
            categories = list(
                dict.fromkeys(
                    [
                        *[
                            _value(value)
                            for value in source_programme.get("categories", []) or []
                            if _value(value)
                        ],
                        *categories,
                    ]
                )
            )
        for category in categories:
            _add_text(programme, "category", category, lang="en")
        if not bool(row.get("is_replay")):
            ElementTree.SubElement(programme, "live")
