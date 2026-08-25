from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

import public_epg_logos
import sports


_cache_lock = threading.Lock()
_cache_signature: tuple | None = None
_cache_index: dict[str, list[dict]] = {}
_cache_programme_count = 0
_cache_updated_at: str | None = None
GUIDE_UPCOMING_PROGRAMME_LIMIT = 16


def _local_tag(element) -> str:
    return str(element.tag or "").rsplit("}", 1)[-1]


def _child_text(element, name: str) -> str:
    for child in list(element):
        if _local_tag(child) == name:
            return str(child.text or "").strip()
    return ""


def _child_texts(element, name: str) -> list[str]:
    values = []
    for child in list(element):
        if _local_tag(child) != name:
            continue
        value = str(child.text or "").strip()
        if value:
            values.append(value)
    return values


def _epg_signature(path: Path, timezone_name: str) -> tuple | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        str(path.resolve()),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        str(timezone_name or "America/New_York"),
    )


def _parse_programme_index(path: Path, timezone_name: str) -> tuple[dict[str, list[dict]], int]:
    default_tz = ZoneInfo(str(timezone_name or "America/New_York"))
    index: dict[str, list[dict]] = {}
    programme_count = 0

    for _event, element in sports._iterparse_xmltv(path):
        tag = _local_tag(element)
        if tag == "channel":
            element.clear()
            continue
        if tag != "programme":
            continue

        channel_id = str(element.attrib.get("channel", "") or "").strip()
        start_value = str(element.attrib.get("start", "") or "").strip()
        stop_value = str(element.attrib.get("stop", "") or "").strip()
        try:
            start = sports._parse_xmltv_time(start_value, default_tz)
            stop = sports._parse_xmltv_time(stop_value, default_tz) if stop_value else None
        except sports.MalformedSportsEntry:
            element.clear()
            continue

        title = _child_text(element, "title")
        if channel_id and start is not None and title:
            record = {
                "title": title,
                "subtitle": _child_text(element, "sub-title"),
                "description": _child_text(element, "desc"),
                "categories": _child_texts(element, "category"),
                "_start": start,
                "_stop": stop,
            }
            index.setdefault(channel_id, []).append(record)
            programme_count += 1
        element.clear()

    for records in index.values():
        records.sort(key=lambda item: item["_start"])
    return index, programme_count


def _cached_programme_index(path: Path, timezone_name: str) -> tuple[dict[str, list[dict]], dict]:
    global _cache_signature, _cache_index, _cache_programme_count, _cache_updated_at

    signature = _epg_signature(path, timezone_name)
    if signature is None:
        return {}, {
            "available": False,
            "updated_at": None,
            "programme_count": 0,
            "error": "The served EPG file is not available yet.",
        }

    with _cache_lock:
        if signature != _cache_signature:
            try:
                index, count = _parse_programme_index(path, timezone_name)
            except Exception as exc:
                return {}, {
                    "available": False,
                    "updated_at": None,
                    "programme_count": 0,
                    "error": f"Could not read the served EPG ({type(exc).__name__}).",
                }
            _cache_signature = signature
            _cache_index = index
            _cache_programme_count = count
            _cache_updated_at = datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()

        return _cache_index, {
            "available": True,
            "updated_at": _cache_updated_at,
            "programme_count": _cache_programme_count,
            "error": "",
        }


def _serialize_programme(record: dict | None) -> dict | None:
    if not record:
        return None
    start = record.get("_start")
    stop = record.get("_stop")
    return {
        "title": str(record.get("title") or ""),
        "subtitle": str(record.get("subtitle") or ""),
        "description": str(record.get("description") or ""),
        "categories": list(record.get("categories") or []),
        "start": start.isoformat() if start else None,
        "stop": stop.isoformat() if stop else None,
    }


def _now_and_next(records: list[dict], now: datetime) -> tuple[dict | None, dict | None]:
    current = None
    for record in records:
        start = record.get("_start")
        stop = record.get("_stop")
        if start is None or stop is None:
            continue
        if start <= now < stop:
            if current is None or start > current["_start"]:
                current = record

    threshold = current.get("_stop") if current else now
    upcoming = None
    for record in records:
        if record is current:
            continue
        start = record.get("_start")
        if start is None or start < threshold:
            continue
        upcoming = record
        break

    return current, upcoming


def programme_window(
    epg_path: Path,
    tvg_id: str,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> dict:
    """Return the current programme boundary for one exact XMLTV channel ID."""
    identity = str(tvg_id or "").strip()
    if not identity:
        return {}
    local_tz = ZoneInfo(str(timezone_name or "America/New_York"))
    anchor = (now or datetime.now().astimezone()).astimezone(local_tz)
    index, _metadata = _cached_programme_index(Path(epg_path), str(local_tz))
    current, upcoming = _now_and_next(index.get(identity, []), anchor)
    return {
        "current": _serialize_programme(current),
        "next": _serialize_programme(upcoming),
    }


def _upcoming_programmes(
    records: list[dict],
    now: datetime,
    current: dict | None,
    *,
    limit: int = GUIDE_UPCOMING_PROGRAMME_LIMIT,
) -> list[dict]:
    threshold = current.get("_stop") if current else now
    upcoming = []
    for record in records:
        if record is current:
            continue
        start = record.get("_start")
        if start is None or start < threshold:
            continue
        upcoming.append(record)
        if len(upcoming) >= limit:
            break
    return upcoming


def enrich_guide_channels(
    channels: list[dict],
    epg_path: Path,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> tuple[list[dict], dict]:
    timezone_value = str(timezone_name or "America/New_York")
    local_tz = ZoneInfo(timezone_value)
    anchor = now or datetime.now().astimezone()
    anchor = anchor.astimezone(local_tz)

    # Logo selection is intentionally independent from programme matching.
    # Manual channels prefer an exact public-EPG channel icon when one exists;
    # sports-generated rows retain their sports/API artwork policy.
    try:
        import core

        channels = public_epg_logos.apply_to_guide_items(
            channels,
            core.active_public_epg_paths(),
        )
    except Exception:
        pass

    index, metadata = _cached_programme_index(Path(epg_path), timezone_value)
    enriched = []
    matched_channels = 0
    current_channels = 0

    for raw in channels:
        channel = dict(raw)
        tvg_id = str(channel.get("tvg_id", "") or "").strip()
        records = index.get(tvg_id, []) if tvg_id else []
        current, upcoming = _now_and_next(records, anchor)
        schedule = _upcoming_programmes(records, anchor, current)
        channel["now"] = _serialize_programme(current)
        channel["next"] = _serialize_programme(upcoming)
        channel["upcoming"] = [
            serialized
            for record in schedule
            if (serialized := _serialize_programme(record)) is not None
        ]
        if current or schedule:
            matched_channels += 1
        if current:
            current_channels += 1
        enriched.append(channel)

    metadata = dict(metadata)
    metadata.update(
        {
            "matched_channels": matched_channels,
            "current_channels": current_channels,
            "channel_count": len(enriched),
            "as_of": anchor.isoformat(),
            "timezone": timezone_value,
        }
    )
    return enriched, metadata
