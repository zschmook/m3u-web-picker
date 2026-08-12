from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import sports as _s


def _m3u_events(
    db_path: Path | str,
    channels: Iterable[dict],
    settings: dict,
    scan_anchor: datetime,
    diagnostics: dict,
    cancel_check: _s.CancelCheck = None,
    *,
    team_lookup: dict | None = None,
    team_feed_channel_ids: set[int] | None = None,
) -> list[dict]:
    window_start, window_end, _ = _s._target_window(scan_anchor, settings)
    events = []
    for index, channel in enumerate(channels):
        if index % 100 == 0:
            _s._raise_if_cancelled(cancel_check)
        if (
            id(channel) in team_feed_channel_ids
            if team_feed_channel_ids is not None
            else bool(_s._team_feed_identity(channel))
        ):
            continue
        text = str(channel.get("name", "") or "")
        try:
            event = _s._event_from_text(
                db_path,
                channel,
                text,
                settings,
                scan_anchor,
                team_lookup=team_lookup,
            )
        except _s.MalformedSportsEntry as exc:
            _s._record_malformed_entry(
                diagnostics,
                source="m3u",
                label=text or str(channel.get("tvg_name", "") or ""),
                exc=exc,
            )
            continue
        if not event:
            continue
        if not _s._event_has_usable_timing(event) or _s._event_overlaps_replay_context(
            event,
            window_start,
            window_end,
        ):
            events.append(event)
    return events


def _epg_channel_indexes(
    channels: list[dict],
    cancel_check: _s.CancelCheck,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    by_tvg_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for index, channel in enumerate(channels):
        if index % 250 == 0:
            _s._raise_if_cancelled(cancel_check)
        tvg_id = str(channel.get("tvg_id", "") or "").strip()
        if tvg_id:
            by_tvg_id[tvg_id].append(channel)
        for value in (channel.get("tvg_name", ""), channel.get("name", "")):
            normalized = _s._normalize(str(value or ""))
            if normalized:
                by_name[normalized].append(channel)
    return by_tvg_id, by_name


def _source_channels_for_programme(
    channel_id: str,
    xml_names: dict[str, list[str]],
    by_tvg_id: dict[str, list[dict]],
    by_name: dict[str, list[dict]],
) -> list[dict]:
    source_channels = list(by_tvg_id.get(channel_id, []))
    if source_channels:
        return source_channels
    for display_name in xml_names.get(channel_id, []):
        source_channels.extend(by_name.get(_s._normalize(display_name), []))
    return source_channels


def _parse_epg_programme(
    *,
    db_path: Path | str,
    element: ElementTree.Element,
    source_channels: list[dict],
    channel_id: str,
    settings: dict,
    scan_anchor: datetime,
    timezone: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
    diagnostics: dict,
    team_lookup: dict | None,
    source_priority: int,
) -> dict | None:
    raw_start = element.attrib.get("start", "")
    raw_stop = element.attrib.get("stop", "")
    try:
        start = _s._parse_xmltv_time(raw_start, timezone)
        stop = _s._parse_xmltv_time(raw_stop, timezone) if raw_stop else None
    except _s.MalformedSportsEntry as exc:
        _s._record_malformed_entry(
            diagnostics,
            source="epg",
            label=(
                f"programme channel={channel_id or 'unknown'} "
                f"start={raw_start or 'missing'}"
            ),
            exc=exc,
        )
        return None
    if not start:
        return None

    rough_end = stop or (start + _s.MAX_ESTIMATED_EVENT_DURATION)
    try:
        rough_start_local = start.astimezone(window_start.tzinfo)
        rough_end_local = rough_end.astimezone(window_start.tzinfo)
    except Exception:
        return None
    if not (
        rough_start_local < window_end
        and rough_end_local + _s.REPLAY_ATTACH_WINDOW > window_start
    ):
        return None

    fields: dict[str, list[str]] = defaultdict(list)
    programme_markers: set[str] = set()
    for child in element:
        child_tag = child.tag.rsplit("}", 1)[-1]
        if child.text and child_tag in {"title", "sub-title", "desc", "category"}:
            fields[child_tag].append(child.text.strip())
        if child_tag in {"live", "previously-shown", "new"}:
            programme_markers.add(child_tag)
    title = fields["title"][0] if fields["title"] else ""
    extra = " ".join(fields["sub-title"] + fields["desc"] + fields["category"])
    if not title:
        return None

    programme_is_replay = "previously-shown" in programme_markers
    if programme_is_replay and not settings.get("include_replays"):
        return None

    try:
        parsed = _s._event_from_text(
            db_path,
            source_channels[0],
            title,
            settings,
            scan_anchor,
            forced_start=start,
            forced_end=stop,
            extra_text=extra,
            team_lookup=team_lookup,
        )
    except _s.MalformedSportsEntry as exc:
        _s._record_malformed_entry(
            diagnostics,
            source="epg",
            label=title,
            exc=exc,
        )
        return None
    if not parsed or not _s._event_overlaps_replay_context(
        parsed,
        window_start,
        window_end,
    ):
        return None

    parsed["source_channels"] = source_channels
    effective_stop = stop if isinstance(stop, datetime) and stop > start else None
    comparison_stop = effective_stop or _s._event_end(parsed)
    try:
        scan_local = (
            scan_anchor.astimezone(start.tzinfo)
            if start.tzinfo
            else scan_anchor.replace(tzinfo=None)
        )
        current_at_scan = bool(
            comparison_stop and start <= scan_local < comparison_stop
        )
    except Exception:
        current_at_scan = False
    parsed["epg_programme"] = {
        "title": title,
        "subtitle": fields["sub-title"][0] if fields["sub-title"] else "",
        "description": fields["desc"][0] if fields["desc"] else "",
        "categories": list(dict.fromkeys(fields["category"])),
        "start": start,
        "stop": effective_stop,
        "is_live": "live" in programme_markers,
        "is_replay": programme_is_replay,
        "is_new": "new" in programme_markers,
        "current_at_scan": current_at_scan,
        "source_channel_id": channel_id,
        "source_priority": int(source_priority),
    }
    parsed["is_replay"] = bool(
        parsed.get("is_replay") or programme_is_replay
    )
    return parsed


def _epg_events(
    db_path: Path | str,
    epg_path: Path | None,
    channels: list[dict],
    settings: dict,
    scan_anchor: datetime,
    diagnostics: dict,
    cancel_check: _s.CancelCheck = None,
    *,
    team_lookup: dict | None = None,
    source_priority: int = 0,
) -> list[dict]:
    if not epg_path or not epg_path.exists() or epg_path.stat().st_size == 0:
        return []

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    window_start, window_end, _ = _s._target_window(scan_anchor, settings)
    by_tvg_id, by_name = _epg_channel_indexes(channels, cancel_check)
    xml_names: dict[str, list[str]] = defaultdict(list)
    output: list[dict] = []
    try:
        for index, (_event, element) in enumerate(
            _s._iterparse_xmltv(epg_path, events=("end",))
        ):
            if index % 500 == 0:
                _s._raise_if_cancelled(cancel_check)
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                channel_id = element.attrib.get("id", "")
                xml_names[channel_id] = [
                    child.text.strip()
                    for child in element
                    if child.tag.rsplit("}", 1)[-1] == "display-name"
                    and child.text
                ]
                element.clear()
                continue
            if tag != "programme":
                continue

            channel_id = element.attrib.get("channel", "")
            source_channels = _source_channels_for_programme(
                channel_id,
                xml_names,
                by_tvg_id,
                by_name,
            )
            if not source_channels:
                element.clear()
                continue
            parsed = _parse_epg_programme(
                db_path=db_path,
                element=element,
                source_channels=source_channels,
                channel_id=channel_id,
                settings=settings,
                scan_anchor=scan_anchor,
                timezone=timezone,
                window_start=window_start,
                window_end=window_end,
                diagnostics=diagnostics,
                team_lookup=team_lookup,
                source_priority=source_priority,
            )
            if parsed:
                output.append(parsed)
            element.clear()
    except (ElementTree.ParseError, OSError):
        return []
    return output


def _previous_generated_event_anchors(
    db_path: Path | str,
    settings: dict,
    scan_anchor: datetime,
    *,
    team_lookup: dict | None = None,
) -> list[dict]:
    """Rehydrate recent logical games as replay-classification anchors."""
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    window_start, window_end, _sports_date = _s._target_window(
        scan_anchor,
        settings,
    )
    with closing(_s._connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT event_key, league_id, event_title, event_start, event_end,
                   epg_programme_json
            FROM sports_generated
            WHERE event_start IS NOT NULL
            GROUP BY event_key
            ORDER BY event_start
            """
        ).fetchall()

    anchors: list[dict] = []
    for row in rows:
        start = _s._parse_iso_datetime(row["event_start"], timezone)
        end = _s._parse_iso_datetime(row["event_end"], timezone)
        if not isinstance(start, datetime):
            continue
        if not isinstance(end, datetime) or end <= start:
            end = start + _s._event_duration(str(row["league_id"] or "sports"))
        try:
            if not (
                start.astimezone(window_start.tzinfo) < window_end
                and end.astimezone(window_start.tzinfo)
                + _s.REPLAY_ATTACH_WINDOW
                > window_start
            ):
                continue
        except Exception:
            continue

        title = str(row["event_title"] or "").strip()
        if not title:
            continue
        channel_stub = {
            "name": title,
            "tvg_name": str(row["league_id"] or ""),
            "group": str(row["league_id"] or ""),
            "tvg_id": "",
            "url": "",
        }
        parsed = _s._event_from_text(
            db_path,
            channel_stub,
            title,
            settings,
            scan_anchor,
            forced_start=start,
            forced_end=end,
            extra_text=str(row["league_id"] or ""),
            team_lookup=team_lookup,
        )
        if not parsed:
            continue
        parsed["timing_source"] = "embedded"
        parsed["source_kind"] = "history"
        parsed["source_kinds"] = ["history"]
        parsed["has_embedded_anchor"] = True
        parsed["historical_anchor"] = True
        parsed["source_channels"] = []

        programme = _s._json_load(row["epg_programme_json"], {})
        if isinstance(programme, dict) and programme:
            primary = _s._parse_programme_record(programme, timezone)
            primary.pop("airings", None)
            if primary.get("start"):
                parsed["epg_programme"] = primary
        anchors.append(parsed)
    return anchors
