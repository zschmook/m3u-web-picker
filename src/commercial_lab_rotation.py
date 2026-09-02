from __future__ import annotations

import random
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from database import connect


TITLE_PREFIX = "Commercial Lab · "
EXCLUDED_CHANNEL_TERMS = (
    "hbo", "showtime", "tmc", "the movie channel", "starz",
    "amc",
    "pbs", "public television",
)
_ROTATION_LOCK = threading.Lock()


def control(db_path: Path | str) -> dict[str, Any]:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT enabled, slots, sample_minutes, updated_at FROM dvr_commercial_lab_control WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"enabled": False, "slots": 4, "sample_minutes": 20, "updated_at": ""}
    return {
        "enabled": bool(row["enabled"]),
        "slots": max(1, min(4, int(row["slots"]))),
        "sample_minutes": max(5, min(120, int(row["sample_minutes"]))),
        "updated_at": str(row["updated_at"] or ""),
    }


def set_control(
    db_path: Path | str,
    *,
    enabled: bool,
    slots: int = 4,
    sample_minutes: int = 20,
) -> dict[str, Any]:
    slots_value = max(1, min(4, int(slots)))
    minutes_value = max(5, min(120, int(sample_minutes)))
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO dvr_commercial_lab_control (id, enabled, slots, sample_minutes, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                slots = excluded.slots,
                sample_minutes = excluded.sample_minutes,
                updated_at = excluded.updated_at
            """,
            (1 if enabled else 0, slots_value, minutes_value, now),
        )
        conn.commit()
    finally:
        conn.close()
    return control(db_path)


def eligible_channel(item: dict[str, Any]) -> bool:
    name = str(item.get("name") or "").casefold()
    play_url = str(item.get("play_url") or "").strip()
    return bool(
        play_url
        and str(item.get("tvg_id") or "").strip()
        and not play_url.startswith("/guide/play/sports/")
        and "4k" not in name
        and "news now" not in name
        and not any(term in name for term in EXCLUDED_CHANNEL_TERMS)
    )


def _rotation_rows(db_path: Path | str) -> tuple[list[sqlite3.Row], set[str]]:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        active = conn.execute(
            """
            SELECT id, tvg_id FROM dvr_recordings
            WHERE title LIKE ? AND status IN ('scheduled', 'recording', 'processing')
            """,
            (f"{TITLE_PREFIX}%",),
        ).fetchall()
        recent = {
            str(row[0]) for row in conn.execute(
                """
                SELECT r.tvg_id FROM dvr_commercial_lab_runs l
                JOIN dvr_recordings r ON r.id = l.recording_id
                ORDER BY l.processed_at DESC LIMIT 32
                """
            ).fetchall()
        }
    finally:
        conn.close()
    return active, recent


def next_completed_recording(
    db_path: Path | str,
    *,
    excluded_ids: set[int] | None = None,
) -> int | None:
    """Return the oldest unprocessed lab capture ready for immediate analysis."""
    excluded = {int(value) for value in (excluded_ids or set())}
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT r.id
            FROM dvr_recordings r
            LEFT JOIN dvr_commercial_lab_runs l ON l.recording_id = r.id
            WHERE r.title LIKE ?
              AND r.status = 'completed'
              AND lower(r.output_name) LIKE '%.ts'
              AND l.id IS NULL
            ORDER BY r.completed_at ASC, r.id ASC
            """,
            (f"{TITLE_PREFIX}%",),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        recording_id = int(row[0])
        if recording_id not in excluded:
            return recording_id
    return None


def ensure_capacity(
    db_path: Path | str,
    channels: Iterable[dict[str, Any]],
    schedule: Callable[..., dict[str, Any]],
    *,
    current: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = current or control(db_path)
    if not current.get("enabled"):
        return []
    if not _ROTATION_LOCK.acquire(blocking=False):
        return []
    try:
        active, recent_ids = _rotation_rows(db_path)
        needed = max(0, int(current["slots"]) - len(active))
        if needed == 0:
            return []
        active_ids = {str(row["tvg_id"] or "") for row in active}
        candidates = [
            item for item in channels
            if eligible_channel(item)
            and str(item.get("tvg_id") or "") not in active_ids
        ]
        fresh = [item for item in candidates if str(item.get("tvg_id") or "") not in recent_ids]
        pool = fresh if len(fresh) >= needed else candidates
        random.SystemRandom().shuffle(pool)
        selected = pool[:needed]
        start = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        stop = start + timedelta(minutes=int(current["sample_minutes"]))
        stamp = start.strftime("%Y-%m-%d %H:%M UTC")
        created = []
        for channel in selected:
            created.append(schedule(
                db_path,
                play_url=str(channel["play_url"]),
                tvg_id=str(channel["tvg_id"]),
                channel_name=str(channel.get("name") or channel["tvg_id"]),
                title=f"{TITLE_PREFIX}{channel.get('name') or channel['tvg_id']} · {stamp}",
                subtitle="Automated commercial fingerprint sample",
                description="Twenty-minute rotating commercial-learning capture.",
                start_at=start,
                stop_at=stop,
            ))
        return created
    finally:
        _ROTATION_LOCK.release()
