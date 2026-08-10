from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s
from . import migrations


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _canonical_refresh_time(value, *, fallback: str | None = None) -> str:
    text = str(value or "").strip().upper()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        except ValueError:
            pass
    if fallback is not None:
        return fallback
    raise ValueError("Refresh time must be a valid time such as 03:00.")


def _refresh_time_parts(settings: dict) -> tuple[int, int]:
    value = settings.get("refresh_time")
    if value not in (None, ""):
        canonical = _canonical_refresh_time(value, fallback="03:00")
        hour, minute = canonical.split(":", 1)
        return int(hour), int(minute)
    try:
        hour = int(settings.get("refresh_hour", 3))
        minute = int(settings.get("refresh_minute", 0))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return 3, 0


def init_db(db_path: Path | str) -> None:
    """Initialize sports persistence without mixing migration concerns."""
    with closing(connect(db_path)) as conn:
        migrations.initialize(conn)


def get_settings(db_path: Path | str) -> dict:
    init_db(db_path)
    output = dict(_s.DEFAULT_SETTINGS)
    with closing(connect(db_path)) as conn:
        for row in conn.execute("SELECT key, value FROM sports_settings"):
            if str(row["key"]).startswith("__"):
                continue
            output[row["key"]] = _s._json_load(row["value"], row["value"])

    hour, minute = _refresh_time_parts(output)
    output["refresh_time"] = f"{hour:02d}:{minute:02d}"
    mode = str(output.get("schedule_mode", "daily") or "daily").strip().lower()
    output["schedule_mode"] = mode if mode in _s.SCHEDULE_MODES else "daily"
    try:
        interval_hours = int(output.get("interval_hours", 2))
    except (TypeError, ValueError):
        interval_hours = 2
    output["interval_hours"] = min(
        _s.MAX_INTERVAL_HOURS,
        max(_s.MIN_INTERVAL_HOURS, interval_hours),
    )
    output.pop("refresh_hour", None)
    output.pop("refresh_minute", None)
    return output


def _disabled_at_from_conn(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT value FROM sports_settings WHERE key = ?",
        (_s.SPORTS_DISABLED_AT_KEY,),
    ).fetchone()
    if not row:
        return None
    value = _s._json_load(row["value"], row["value"])
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def disabled_cache_status(db_path: Path | str, now: datetime | None = None) -> dict:
    init_db(db_path)
    current = (now or datetime.now().astimezone()).astimezone()
    with closing(connect(db_path)) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM sports_generated").fetchone()[0])
        disabled_at = _disabled_at_from_conn(conn)
    settings = get_settings(db_path)
    if settings.get("enabled") or not disabled_at:
        return {
            "count": count,
            "disabled_at": None,
            "expires_at": None,
            "expired": False,
        }
    expires_at = disabled_at + timedelta(hours=_s.SPORTS_DISABLED_CACHE_HOURS)
    return {
        "count": count,
        "disabled_at": disabled_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "expired": current >= expires_at,
    }


def purge_expired_disabled_cache(db_path: Path | str, now: datetime | None = None) -> bool:
    init_db(db_path)
    current = (now or datetime.now().astimezone()).astimezone()
    settings = get_settings(db_path)
    if settings.get("enabled"):
        return False
    with closing(connect(db_path)) as conn:
        disabled_at = _disabled_at_from_conn(conn)
        if (
            not disabled_at
            or current < disabled_at + timedelta(hours=_s.SPORTS_DISABLED_CACHE_HOURS)
        ):
            return False
        deleted = conn.execute("DELETE FROM sports_generated").rowcount
        conn.commit()
    return bool(deleted)


def clear_generated_channels(db_path: Path | str) -> int:
    init_db(db_path)
    with closing(connect(db_path)) as conn:
        deleted = conn.execute("DELETE FROM sports_generated").rowcount
        conn.commit()
    return int(deleted or 0)


def _clean_setting_changes(db_path: Path | str, changes: dict) -> tuple[dict, dict]:
    previous = get_settings(db_path)
    allowed = set(_s.DEFAULT_SETTINGS)
    clean = {key: value for key, value in changes.items() if key in allowed}

    for key in (
        "enabled",
        "auto_update",
        "everything_mode",
        "include_replays",
        "include_pregame",
        "use_backup_feeds",
        "exclude_sd",
        "schedule_api_enabled",
    ):
        if key in clean:
            clean[key] = bool(clean[key])

    if "start_channel" in clean:
        try:
            clean["start_channel"] = max(1, int(clean["start_channel"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Starting channel must be a positive whole number.") from exc
    if "channels_per_event" in clean:
        try:
            clean["channels_per_event"] = min(50, max(1, int(clean["channels_per_event"])))
        except (TypeError, ValueError) as exc:
            raise ValueError("Channels per event must be a whole number from 1 to 50.") from exc
    if "refresh_time" in clean:
        clean["refresh_time"] = _canonical_refresh_time(clean["refresh_time"])
    if "schedule_mode" in clean:
        clean["schedule_mode"] = str(clean["schedule_mode"] or "").strip().lower()
        if clean["schedule_mode"] not in _s.SCHEDULE_MODES:
            raise ValueError("Update schedule must be Daily or Every X hours.")
    if "interval_hours" in clean:
        try:
            clean["interval_hours"] = int(clean["interval_hours"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Update interval must be a whole number from 1 to 24 hours.") from exc
        if not _s.MIN_INTERVAL_HOURS <= clean["interval_hours"] <= _s.MAX_INTERVAL_HOURS:
            raise ValueError("Update interval must be a whole number from 1 to 24 hours.")

    if "refresh_time" not in clean and (
        "refresh_hour" in changes or "refresh_minute" in changes
    ):
        current_hour, current_minute = _refresh_time_parts(previous)
        try:
            hour = int(changes.get("refresh_hour", current_hour))
            minute = int(changes.get("refresh_minute", current_minute))
        except (TypeError, ValueError) as exc:
            raise ValueError("Refresh time must be a valid time such as 03:00.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Refresh time must be a valid time such as 03:00.")
        clean["refresh_time"] = f"{hour:02d}:{minute:02d}"

    if "group_title" in clean:
        clean["group_title"] = str(clean["group_title"]).strip()[:120] or "Sports Today"
    if "timezone" in clean:
        clean["timezone"] = str(clean["timezone"]).strip()
        try:
            ZoneInfo(clean["timezone"])
        except Exception as exc:
            raise ValueError("Choose a valid timezone.") from exc
    if (
        "event_window" in clean
        and clean["event_window"] not in {"today", "today_tomorrow", "next_24_hours"}
    ):
        clean["event_window"] = "today"
    return previous, clean


def _persist_setting_changes(
    db_path: Path | str,
    previous: dict,
    clean: dict,
) -> None:
    previous_enabled = bool(previous.get("enabled"))
    effective = dict(previous)
    effective.update(clean)
    schedule_changed = any(
        key in clean and clean[key] != previous.get(key)
        for key in ("schedule_mode", "interval_hours")
    )

    with closing(connect(db_path)) as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

        if schedule_changed and effective.get("schedule_mode") == "interval":
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (_s.SPORTS_INTERVAL_ANCHOR_KEY, json.dumps(_s._now_iso())),
            )
        elif effective.get("schedule_mode") != "interval":
            conn.execute(
                "DELETE FROM sports_settings WHERE key = ?",
                (_s.SPORTS_INTERVAL_ANCHOR_KEY,),
            )

        if "enabled" in clean and bool(clean["enabled"]) != previous_enabled:
            if clean["enabled"]:
                disabled_at = _disabled_at_from_conn(conn)
                if (
                    disabled_at
                    and datetime.now().astimezone()
                    >= disabled_at + timedelta(hours=_s.SPORTS_DISABLED_CACHE_HOURS)
                ):
                    conn.execute("DELETE FROM sports_generated")
                conn.execute(
                    "DELETE FROM sports_settings WHERE key = ?",
                    (_s.SPORTS_DISABLED_AT_KEY,),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                    (_s.SPORTS_DISABLED_AT_KEY, json.dumps(_s._now_iso())),
                )

        conn.execute(
            "DELETE FROM sports_settings WHERE key IN ('refresh_hour', 'refresh_minute')"
        )
        conn.commit()


def update_settings(db_path: Path | str, changes: dict) -> dict:
    previous, clean = _clean_setting_changes(db_path, changes)
    _persist_setting_changes(db_path, previous, clean)
    return get_settings(db_path)
