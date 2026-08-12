from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


def _sports_day(now: datetime, settings: dict) -> date:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    refresh_hour, refresh_minute = _s._refresh_time_parts(settings)
    refresh = dt_time(refresh_hour, refresh_minute)
    if local_now.time().replace(tzinfo=None) < refresh:
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def _target_window(now: datetime, settings: dict) -> tuple[datetime, datetime, date]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    sports_day = _sports_day(now, settings)
    boundary = datetime.combine(
        sports_day,
        dt_time(*_s._refresh_time_parts(settings)),
        tzinfo=timezone,
    )
    mode = settings.get("event_window", "today")
    if mode == "next_24_hours":
        return local_now, local_now + timedelta(hours=24), sports_day
    if mode == "today_tomorrow":
        return boundary, boundary + timedelta(days=2), sports_day
    return boundary, boundary + timedelta(days=1), sports_day


def _parse_scheduled_datetime(value, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _interval_anchor_at(
    db_path: Path | str,
    *,
    timezone: ZoneInfo,
    fallback: datetime,
) -> datetime:
    last = _s.last_scan(db_path)
    finished = _parse_scheduled_datetime(last.get("finished_at") if last else None, timezone)
    if finished is not None:
        return finished

    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (_s.SPORTS_INTERVAL_ANCHOR_KEY,),
        ).fetchone()
        stored = _s._json_load(row["value"], row["value"]) if row else None
        anchor = _parse_scheduled_datetime(stored, timezone)
        if anchor is None:
            anchor = fallback
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (
                    _s.SPORTS_INTERVAL_ANCHOR_KEY,
                    _s.json.dumps(anchor.isoformat(timespec="seconds")),
                ),
            )
            conn.commit()
    return anchor


def next_update_at(db_path: Path | str, now: datetime | None = None) -> datetime:
    settings = _s.get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)

    if settings.get("schedule_mode") == "interval":
        anchor = _interval_anchor_at(db_path, timezone=timezone, fallback=local_now)
        return anchor + timedelta(hours=int(settings.get("interval_hours", 2)))

    refresh_hour, refresh_minute = _s._refresh_time_parts(settings)
    target = local_now.replace(
        hour=refresh_hour,
        minute=refresh_minute,
        second=0,
        microsecond=0,
    )
    if target <= local_now:
        target += timedelta(days=1)
    return target


def should_run_scheduled(db_path: Path | str, now: datetime | None = None) -> bool:
    settings = _s.get_settings(db_path)
    if not settings.get("enabled") or not settings.get("auto_update"):
        return False
    if _s.scan_state(db_path, now).get("running"):
        return False

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)

    if settings.get("schedule_mode") == "interval":
        return local_now >= next_update_at(db_path, local_now)

    refresh_hour, refresh_minute = _s._refresh_time_parts(settings)
    if (local_now.hour, local_now.minute) != (refresh_hour, refresh_minute):
        return False

    last = _s.last_scan(db_path)
    if not last:
        return True

    target_date = _sports_day(local_now, settings).isoformat()
    if last.get("status") == "success" and last.get("target_date") == target_date:
        return False

    if last.get("trigger") == "scheduled":
        attempted = _parse_scheduled_datetime(last.get("started_at"), timezone)
        if attempted and (
            attempted.date() == local_now.date()
            and (attempted.hour, attempted.minute) == (refresh_hour, refresh_minute)
        ):
            return False
    return True
