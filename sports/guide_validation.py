from __future__ import annotations

import gzip
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import sports as _s


def _local_xml_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xmltv_index(path: Path | None, fallback_tz: ZoneInfo) -> dict:
    result = {
        "exists": bool(path and path.exists()),
        "channels": set(),
        "programmes": defaultdict(list),
        "error": "",
        "size": 0,
        "modified": "",
    }
    if not path or not path.exists():
        return result
    try:
        result["size"] = path.stat().st_size
        result["modified"] = datetime.fromtimestamp(
            path.stat().st_mtime
        ).astimezone().isoformat(timespec="seconds")
        if path.suffix.lower() == ".gz":
            with gzip.open(path, "rb") as handle:
                root = ElementTree.parse(handle).getroot()
        else:
            root = ElementTree.parse(path).getroot()
        for child in root:
            tag = _local_xml_name(child.tag)
            if tag == "channel":
                channel_id = str(child.attrib.get("id", "") or "").strip()
                if channel_id:
                    result["channels"].add(channel_id)
            elif tag == "programme":
                channel_id = str(child.attrib.get("channel", "") or "").strip()
                if not channel_id:
                    continue
                start = _s._parse_xmltv_time(
                    str(child.attrib.get("start", "") or ""),
                    fallback_tz,
                )
                stop = _s._parse_xmltv_time(
                    str(child.attrib.get("stop", "") or ""),
                    fallback_tz,
                )
                if start and stop and stop > start:
                    result["programmes"][channel_id].append((start, stop))
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def _playlist_tvg_ids(path: Path | None) -> tuple[set[str], str]:
    if not path or not path.exists():
        return set(), "playlist file is missing"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return {
            match.group(1).strip()
            for match in re.finditer(r'\btvg-id="([^"]+)"', text, flags=re.I)
            if match.group(1).strip()
        }, ""
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"


def validate_guide_exports(
    db_path: Path | str,
    *,
    playlist_path: Path | None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
) -> dict:
    """Validate the exact files served to Jellyfin without exposing stream URLs."""
    settings = _s.get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    rows = _s.generated_rows(db_path)
    expected_ids = {
        str(row.get("tvg_id", "") or "").strip() for row in rows
    }
    expected_ids.discard("")

    playlist_ids, playlist_error = _playlist_tvg_ids(playlist_path)
    sports_index = _xmltv_index(sports_epg_path, timezone)
    combined_index = _xmltv_index(combined_epg_path, timezone)

    missing_playlist = sorted(expected_ids - playlist_ids)
    missing_sports_channels = sorted(expected_ids - sports_index["channels"])
    missing_sports_programmes = sorted(
        channel_id
        for channel_id in expected_ids
        if not sports_index["programmes"].get(channel_id)
    )
    missing_combined_channels = sorted(expected_ids - combined_index["channels"])
    missing_combined_programmes = sorted(
        channel_id
        for channel_id in expected_ids
        if not combined_index["programmes"].get(channel_id)
    )

    uncovered_event_starts = []
    for row in rows:
        channel_id = str(row.get("tvg_id", "") or "").strip()
        event_start = _s._parse_iso_datetime(row.get("event_start"), timezone)
        if not channel_id or not event_start:
            continue
        event_start = event_start.astimezone(timezone)
        intervals = sports_index["programmes"].get(channel_id, [])
        if not any(start <= event_start < stop for start, stop in intervals):
            uncovered_event_starts.append(channel_id)

    errors = [
        value
        for value in (
            playlist_error,
            sports_index["error"],
            combined_index["error"],
        )
        if value
    ]
    ok = not any(
        (
            errors,
            missing_playlist,
            missing_sports_channels,
            missing_sports_programmes,
            missing_combined_channels,
            missing_combined_programmes,
            uncovered_event_starts,
        )
    )
    return {
        "ok": ok,
        "generated_channels": len(expected_ids),
        "playlist_sports_ids": len(expected_ids & playlist_ids),
        "sports_xml_channels": len(expected_ids & sports_index["channels"]),
        "sports_xml_programme_channels": sum(
            1
            for channel_id in expected_ids
            if sports_index["programmes"].get(channel_id)
        ),
        "combined_xml_channels": len(expected_ids & combined_index["channels"]),
        "combined_xml_programme_channels": sum(
            1
            for channel_id in expected_ids
            if combined_index["programmes"].get(channel_id)
        ),
        "missing_playlist_ids": missing_playlist,
        "missing_sports_channels": missing_sports_channels,
        "missing_sports_programmes": missing_sports_programmes,
        "missing_combined_channels": missing_combined_channels,
        "missing_combined_programmes": missing_combined_programmes,
        "uncovered_event_starts": sorted(uncovered_event_starts),
        "errors": errors,
        "sports_xml_size": sports_index["size"],
        "sports_xml_modified": sports_index["modified"],
        "combined_xml_size": combined_index["size"],
        "combined_xml_modified": combined_index["modified"],
    }
