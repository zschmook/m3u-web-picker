from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the application database and ensure the core schema exists."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_core_schema(conn)
    return conn


def _ensure_core_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS selections (
            key TEXT PRIMARY KEY,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            tvg_id TEXT NOT NULL DEFAULT '',
            sort_order INTEGER
        )
        """
    )
    for statement in (
        "ALTER TABLE selections ADD COLUMN sort_order INTEGER",
        "ALTER TABLE selections ADD COLUMN tvg_id TEXT NOT NULL DEFAULT ''",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS group_channels (
            group_id INTEGER NOT NULL,
            channel_key TEXT NOT NULL,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            tvg_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (group_id, channel_key),
            FOREIGN KEY (group_id) REFERENCES custom_groups(id) ON DELETE CASCADE
        )
        """
    )
    try:
        conn.execute("ALTER TABLE group_channels ADD COLUMN tvg_id TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roku_devices (
            device_key TEXT PRIMARY KEY,
            device_id TEXT NOT NULL DEFAULT '',
            serial_number TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT 'Roku TV',
            model TEXT NOT NULL DEFAULT '',
            model_number TEXT NOT NULL DEFAULT '',
            software_version TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_roku_devices_host ON roku_devices(host)")
    conn.commit()
