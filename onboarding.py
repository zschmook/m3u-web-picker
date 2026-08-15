from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
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


def dev_onboarding_enabled() -> bool:
    return str(os.environ.get("M3U_DEV_ONBOARDING", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_TABLE_SQL)
    conn.commit()
    return conn


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
    # A dev install remains "fresh" until the user has configured a provider,
    # saved manual channels, or added sports rules.
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
            dev_onboarding_enabled()
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
    try:
        answers = json.loads(row["answers_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        answers = {}
    if not isinstance(answers, dict):
        answers = {}
    return {
        "required": bool(row["required"]),
        "completed": bool(row["completed"]),
        "current_step": min(7, max(1, int(row["current_step"] or 1))),
        "answers": answers,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def setup_required(
    db_path: Path | str,
    *,
    provider_configured: bool,
) -> bool:
    state = get_state(db_path, provider_configured=provider_configured)
    return bool(
        dev_onboarding_enabled()
        and state.get("required")
        and not state.get("completed")
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
        try:
            merged = json.loads(row["answers_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            merged = {}
        if not isinstance(merged, dict):
            merged = {}
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
        conn.execute(
            """
            UPDATE app_onboarding
            SET completed = 1, current_step = 7, updated_at = ?, completed_at = ?
            WHERE id = 1
            """,
            (now, now),
        )
        conn.commit()
    return get_state(db_path, provider_configured=provider_configured)
