from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from database import connect as connect_database


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS master_update_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
)
"""


def _ensure_table(db_path: Path | str) -> None:
    with connect_database(db_path) as conn:
        conn.execute(_TABLE_SQL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_master_update_runs_finished ON master_update_runs(finished_at DESC)")
        conn.commit()


def record(
    db_path: Path | str,
    *,
    started_at: str,
    finished_at: str,
    trigger: str,
    status: str,
    duration_seconds: float,
    summary: str,
    details: dict | None = None,
) -> None:
    _ensure_table(db_path)
    payload = json.dumps(details or {}, ensure_ascii=False, default=str)
    with connect_database(db_path) as conn:
        conn.execute(
            """
            INSERT INTO master_update_runs
                (started_at, finished_at, trigger, status, duration_seconds, summary, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(started_at),
                str(finished_at),
                str(trigger or "manual"),
                str(status or "success"),
                float(duration_seconds or 0),
                str(summary or ""),
                payload,
            ),
        )
        conn.commit()


def latest(db_path: Path | str) -> dict | None:
    _ensure_table(db_path)
    with connect_database(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, started_at, finished_at, trigger, status,
                   duration_seconds, summary, details_json
            FROM master_update_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    try:
        details = json.loads(row[7] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        details = {}
    return {
        "id": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "trigger": row[3],
        "status": row[4],
        "duration_seconds": float(row[5] or 0),
        "summary": row[6] or "",
        "details": details if isinstance(details, dict) else {},
    }


def _result_status(result: dict) -> tuple[str, str, dict]:
    payload = dict(result or {})
    warnings = list(payload.get("provider_warnings") or [])
    cycle = dict(payload.get("cycle_check") or {})
    guide = dict(payload.get("guide_check") or {})
    schedule = dict(payload.get("schedule_api") or {})

    status = "success"
    if warnings or schedule.get("warning") or cycle.get("ok") is False or guide.get("ok") is False:
        status = "warning"

    summary = str(payload.get("message") or "Master update completed.")
    details = {
        "provider_warnings": warnings,
        "cycle_check": cycle,
        "guide_check": guide,
        "schedule_api_warning": str(schedule.get("warning") or ""),
    }
    return status, summary, details


def install(core_module) -> None:
    current = getattr(core_module, "run_master_update", None)
    if not callable(current) or getattr(current, "_ui_report_wrapped", False):
        return

    original = current
    db_path = core_module.DB_PATH
    _ensure_table(db_path)

    def reported_run_master_update(*, trigger: str = "manual"):
        clean_trigger = str(trigger or "manual").strip() or "manual"
        started = datetime.now().astimezone()
        started_monotonic = time.monotonic()
        try:
            result = original(trigger=clean_trigger)
            finished = datetime.now().astimezone()
            status, summary, details = _result_status(result)
            record(
                db_path,
                started_at=started.isoformat(timespec="seconds"),
                finished_at=finished.isoformat(timespec="seconds"),
                trigger=clean_trigger,
                status=status,
                duration_seconds=max(0.0, time.monotonic() - started_monotonic),
                summary=summary,
                details=details,
            )
            return result
        except Exception as exc:
            finished = datetime.now().astimezone()
            redactor = getattr(core_module, "redact_url_credentials", None)
            message = str(exc) or "Master update failed."
            if callable(redactor):
                try:
                    message = redactor(message)
                except Exception:
                    pass
            record(
                db_path,
                started_at=started.isoformat(timespec="seconds"),
                finished_at=finished.isoformat(timespec="seconds"),
                trigger=clean_trigger,
                status="failed",
                duration_seconds=max(0.0, time.monotonic() - started_monotonic),
                summary=message,
                details={"error": message},
            )
            raise

    reported_run_master_update._ui_report_wrapped = True
    reported_run_master_update._ui_report_original = original
    core_module.run_master_update = reported_run_master_update
