from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path

import sports as _s


def _record_scan(
    db_path: Path | str,
    *,
    started_at: str,
    status: str,
    message: str,
    event_count: int,
    channel_count: int,
    target_date: str,
    trigger: str,
) -> None:
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO sports_scan_runs
                (started_at, finished_at, status, message,
                 event_count, channel_count, target_date, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                _s._now_iso(),
                status,
                message,
                event_count,
                channel_count,
                target_date,
                trigger,
            ),
        )
        conn.commit()


def begin_scan_state(
    db_path: Path | str,
    *,
    trigger: str = "manual",
    started_at: str | None = None,
    stage: str = "Starting sports update",
) -> dict:
    _s.init_db(db_path)
    started = started_at or _s._now_iso()
    updated = _s._now_iso()
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO sports_scan_state
                (id, running, started_at, updated_at, stage, trigger)
            VALUES (1, 1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                running = 1,
                started_at = excluded.started_at,
                updated_at = excluded.updated_at,
                stage = excluded.stage,
                trigger = excluded.trigger
            """,
            (started, updated, str(stage).strip()[:160], str(trigger).strip()[:40] or "manual"),
        )
        conn.commit()
    return scan_state(db_path)


def update_scan_stage(db_path: Path | str, stage: str) -> dict:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE sports_scan_state
            SET updated_at = ?, stage = ?
            WHERE id = 1 AND running = 1
            """,
            (_s._now_iso(), str(stage).strip()[:160]),
        )
        conn.commit()
    return scan_state(db_path)


def finish_scan_state(db_path: Path | str) -> None:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE sports_scan_state
            SET running = 0, updated_at = ?, stage = ''
            WHERE id = 1
            """,
            (_s._now_iso(),),
        )
        conn.commit()


def scan_state(db_path: Path | str, now: datetime | None = None) -> dict:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT running, started_at, updated_at, stage, trigger
            FROM sports_scan_state
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return {
            "running": False,
            "started_at": None,
            "updated_at": None,
            "stage": "",
            "trigger": "manual",
            "elapsed_seconds": 0,
        }

    payload = dict(row)
    payload["running"] = bool(payload.get("running"))
    elapsed = 0
    if payload["running"] and payload.get("started_at"):
        try:
            started = datetime.fromisoformat(str(payload["started_at"]))
            current = now or datetime.now().astimezone()
            if started.tzinfo is None:
                started = started.replace(tzinfo=current.tzinfo)
            elapsed = max(0, int((current - started).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            elapsed = 0
    payload["elapsed_seconds"] = elapsed
    return payload


def recover_interrupted_scan(db_path: Path | str) -> bool:
    state = scan_state(db_path)
    if not state.get("running"):
        return False
    record_scan_failure(
        db_path,
        "The previous sports update was interrupted by an app restart.",
        trigger=str(state.get("trigger") or "manual"),
        started_at=str(state.get("started_at") or _s._now_iso()),
    )
    finish_scan_state(db_path)
    return True


def record_scan_cancelled(
    db_path: Path | str,
    trigger: str = "manual",
    *,
    started_at: str | None = None,
) -> None:
    settings = _s.get_settings(db_path)
    now = datetime.now().astimezone()
    _record_scan(
        db_path,
        started_at=started_at or _s._now_iso(),
        status="cancelled",
        message="Sports update cancelled. Existing sports channels were kept.",
        event_count=0,
        channel_count=len(_s.generated_rows(db_path, include_cached=True)),
        target_date=_s._sports_day(now, settings).isoformat(),
        trigger=trigger,
    )


def record_scan_failure(
    db_path: Path | str,
    message: str,
    trigger: str = "scheduled",
    *,
    started_at: str | None = None,
) -> None:
    settings = _s.get_settings(db_path)
    now = datetime.now().astimezone()
    _record_scan(
        db_path,
        started_at=started_at or _s._now_iso(),
        status="failed",
        message=message,
        event_count=0,
        channel_count=0,
        target_date=_s._sports_day(now, settings).isoformat(),
        trigger=trigger,
    )


def last_scan(db_path: Path | str) -> dict | None:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, started_at, finished_at, status, message,
                   event_count, channel_count, target_date, trigger
            FROM sports_scan_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None
