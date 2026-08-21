from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from database import connect as connect_database


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_onboarding (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    required INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    current_step INTEGER NOT NULL DEFAULT 1,
    answers_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
)
"""

_INITIAL_REFRESH_STALE_GRACE = timedelta(seconds=15)


def _enabled_value(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def onboarding_enabled() -> bool:
    """Return whether first-run onboarding is enabled for this runtime.

    M3U_ONBOARDING_ENABLED is the current public setting. M3U_DEV_ONBOARDING is
    retained as a compatibility fallback so existing dev/test deployments do
    not suddenly lose their setup flow.
    """
    value = os.environ.get("M3U_ONBOARDING_ENABLED")
    if value is None:
        value = os.environ.get("M3U_DEV_ONBOARDING", "")
    return _enabled_value(value)


def dev_onboarding_enabled() -> bool:
    """Backward-compatible alias for older imports/tests."""
    return onboarding_enabled()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_TABLE_SQL)
    conn.commit()
    return conn


def _decode_answers(value) -> dict:
    try:
        answers = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        answers = {}
    return answers if isinstance(answers, dict) else {}


def _safe_count(conn: sqlite3.Connection, table_name: str) -> int:
    if table_name not in {"selections", "sports_rules"}:
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"] if row else 0)
    except sqlite3.OperationalError:
        return 0


def _looks_like_fresh_install(
    conn: sqlite3.Connection,
    *,
    provider_configured: bool,
) -> bool:
    if provider_configured:
        return False
    # Schema/catalog rows and background update history are not user setup.
    # An install remains fresh until the user has configured a provider, saved
    # manual channels, or added sports rules.
    return (
        _safe_count(conn, "selections") == 0
        and _safe_count(conn, "sports_rules") == 0
    )


def _ensure_state(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM app_onboarding WHERE id = 1").fetchone()
        if row:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        required = bool(
            onboarding_enabled()
            and _looks_like_fresh_install(
                conn,
                provider_configured=provider_configured,
            )
        )
        conn.execute(
            """
            INSERT INTO app_onboarding
                (id, required, completed, current_step, answers_json,
                 created_at, updated_at, completed_at)
            VALUES (1, ?, 0, 1, '{}', ?, ?, NULL)
            """,
            (1 if required else 0, now, now),
        )
        conn.commit()


def get_state(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> dict:
    _ensure_state(db_path, provider_configured=provider_configured)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT required, completed, current_step, answers_json,
                   created_at, updated_at, completed_at
            FROM app_onboarding WHERE id = 1
            """
        ).fetchone()
    if not row:
        return {
            "required": False,
            "completed": False,
            "current_step": 1,
            "answers": {},
        }
    return {
        "required": bool(row["required"]),
        "completed": bool(row["completed"]),
        "current_step": min(7, max(1, int(row["current_step"] or 1))),
        "answers": _decode_answers(row["answers_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def initial_refresh_required(state: dict) -> bool:
    answers = dict(state.get("answers") or {})
    return bool(
        state.get("completed")
        and answers.get("initial_refresh_required")
        and not answers.get("initial_refresh_completed_at")
    )


def setup_required(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> bool:
    state = get_state(db_path, provider_configured=provider_configured)
    return bool(
        onboarding_enabled()
        and state.get("required")
        and (not state.get("completed") or initial_refresh_required(state))
    )


def update_state(
    db_path: Path | str,
    *,
    provider_configured: bool,
    current_step: int | None = None,
    answers: dict | None = None,
) -> dict:
    _ensure_state(db_path, provider_configured=provider_configured)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT current_step, answers_json FROM app_onboarding WHERE id = 1"
        ).fetchone()
        if not row:
            raise RuntimeError("Onboarding state could not be created.")
        merged = _decode_answers(row["answers_json"])
        if isinstance(answers, dict):
            merged.update(answers)
        step = int(row["current_step"] or 1)
        if current_step is not None:
            step = min(7, max(1, int(current_step)))
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE app_onboarding
            SET current_step = ?, answers_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (step, json.dumps(merged), now),
        )
        conn.commit()
    return get_state(db_path, provider_configured=provider_configured)


def mark_complete(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> dict:
    _ensure_state(db_path, provider_configured=provider_configured)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT completed, completed_at, answers_json FROM app_onboarding WHERE id = 1"
        ).fetchone()
        answers = _decode_answers(row["answers_json"] if row else "{}")
        already_completed = bool(row and row["completed"])
        # New first-run completions remain behind the setup gate until the
        # one-shot onboarding Master Update has cached the enabled public EPG
        # and published the first Combined XMLTV guide. Older completed installs
        # do not have initial_refresh_required and are left alone.
        if not already_completed:
            answers["initial_refresh_required"] = True
            answers["initial_refresh_pending"] = True
            answers["initial_refresh_in_progress"] = False
            answers.pop("initial_refresh_claimed_at", None)
            answers.pop("initial_refresh_completed_at", None)
            answers.pop("initial_refresh_error", None)
        completed_at = row["completed_at"] if row and row["completed_at"] else now
        conn.execute(
            """
            UPDATE app_onboarding
            SET completed = 1, current_step = 7, answers_json = ?,
                updated_at = ?, completed_at = ?
            WHERE id = 1
            """,
            (json.dumps(answers), now, completed_at),
        )
        conn.commit()
    return get_state(db_path, provider_configured=provider_configured)


def claim_initial_refresh(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> bool:
    """Atomically claim the one-shot post-onboarding Master Update."""
    _ensure_state(db_path, provider_configured=provider_configured)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT completed, answers_json FROM app_onboarding WHERE id = 1"
        ).fetchone()
        if not row or not bool(row["completed"]):
            return False
        answers = _decode_answers(row["answers_json"])
        if not bool(answers.get("initial_refresh_required")):
            return False
        if not bool(answers.get("initial_refresh_pending")):
            return False
        answers["initial_refresh_pending"] = False
        answers["initial_refresh_in_progress"] = True
        answers["initial_refresh_claimed_at"] = now
        answers.pop("initial_refresh_error", None)
        conn.execute(
            """
            UPDATE app_onboarding
            SET answers_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(answers), now),
        )
        conn.commit()
    return True


def recover_stale_initial_refresh(
    db_path: Path | str,
    *,
    provider_configured: bool,
    worker_running: bool,
) -> dict:
    """Re-arm a persisted refresh claim after its worker has disappeared."""
    state = get_state(db_path, provider_configured=provider_configured)
    answers = state.get("answers") or {}
    if worker_running or not bool(answers.get("initial_refresh_in_progress")):
        return state

    claimed_at = str(answers.get("initial_refresh_claimed_at") or "").strip()
    try:
        claimed = datetime.fromisoformat(claimed_at)
        now = datetime.now().astimezone()
        if claimed.tzinfo is None:
            claimed = claimed.astimezone()
        if now - claimed < _INITIAL_REFRESH_STALE_GRACE:
            return state
    except (TypeError, ValueError):
        pass

    return finish_initial_refresh(
        db_path,
        provider_configured=provider_configured,
        success=False,
        error="The first Master Update stopped before setup finished. Retry the first update.",
    )


def finish_initial_refresh(
    db_path: Path | str,
    *,
    provider_configured: bool,
    success: bool,
    error: str = "",
) -> dict:
    """Release or re-arm the first-run guide gate after the onboarding update."""
    _ensure_state(db_path, provider_configured=provider_configured)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT answers_json FROM app_onboarding WHERE id = 1"
        ).fetchone()
        answers = _decode_answers(row["answers_json"] if row else "{}")
        if not bool(answers.get("initial_refresh_required")):
            return get_state(db_path, provider_configured=provider_configured)
        answers["initial_refresh_in_progress"] = False
        if success:
            answers["initial_refresh_pending"] = False
            answers["initial_refresh_completed_at"] = now
            answers.pop("initial_refresh_error", None)
        else:
            answers["initial_refresh_pending"] = True
            answers.pop("initial_refresh_completed_at", None)
            answers["initial_refresh_error"] = str(error or "The first guide update did not complete.")
        conn.execute(
            """
            UPDATE app_onboarding
            SET answers_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(answers), now),
        )
        conn.commit()
    return get_state(db_path, provider_configured=provider_configured)
