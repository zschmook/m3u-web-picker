from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the application database and ensure the core schema exists."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_core_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, statement: str) -> None:
    """Run an ALTER TABLE ... ADD COLUMN, tolerating only "already exists"."""
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


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
        _add_column_if_missing(conn, statement)
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
    _add_column_if_missing(conn, "ALTER TABLE group_channels ADD COLUMN tvg_id TEXT NOT NULL DEFAULT ''")
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_series_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            title_key TEXT NOT NULL,
            tvg_id TEXT NOT NULL,
            channel_name TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(title_key, tvg_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER,
            dedupe_key TEXT NOT NULL UNIQUE,
            play_url TEXT NOT NULL,
            tvg_id TEXT NOT NULL,
            channel_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            start_at TEXT NOT NULL,
            stop_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            output_name TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            conversion_status TEXT NOT NULL DEFAULT '',
            conversion_error TEXT NOT NULL DEFAULT '',
            commercial_status TEXT NOT NULL DEFAULT '',
            commercial_error TEXT NOT NULL DEFAULT '',
            commercial_count INTEGER NOT NULL DEFAULT 0,
            commercial_seconds REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (rule_id) REFERENCES dvr_series_rules(id) ON DELETE SET NULL
        )
        """
    )
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN conversion_status TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN conversion_error TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN commercial_status TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN commercial_error TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN commercial_count INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "ALTER TABLE dvr_recordings ADD COLUMN commercial_seconds REAL NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dvr_recordings_status_start ON dvr_recordings(status, start_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dvr_recordings_rule ON dvr_recordings(rule_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_commercial_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER NOT NULL UNIQUE,
            source_path TEXT NOT NULL,
            converted_path TEXT NOT NULL,
            edl_path TEXT NOT NULL DEFAULT '',
            detector TEXT NOT NULL DEFAULT 'comskip',
            source_duration REAL NOT NULL DEFAULT 0,
            converted_duration REAL NOT NULL DEFAULT 0,
            detected_breaks_json TEXT NOT NULL DEFAULT '[]',
            observations_json TEXT NOT NULL DEFAULT '[]',
            review_status TEXT NOT NULL DEFAULT 'reviewing',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (recording_id) REFERENCES dvr_recordings(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_commercial_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER NOT NULL,
            label_kind TEXT NOT NULL,
            segment_index INTEGER NOT NULL,
            start_seconds REAL NOT NULL,
            end_seconds REAL NOT NULL,
            duration_seconds REAL NOT NULL,
            algorithm TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            fingerprint_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(sample_id, label_kind, segment_index, algorithm),
            FOREIGN KEY (sample_id) REFERENCES dvr_commercial_samples(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dvr_commercial_fingerprints_hash "
        "ON dvr_commercial_fingerprints(fingerprint_sha256)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_commercial_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER NOT NULL,
            fingerprint_id INTEGER NOT NULL,
            matched_sample_id INTEGER NOT NULL,
            matched_fingerprint_id INTEGER NOT NULL,
            similarity REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(fingerprint_id, matched_fingerprint_id),
            FOREIGN KEY (sample_id) REFERENCES dvr_commercial_samples(id) ON DELETE CASCADE,
            FOREIGN KEY (fingerprint_id) REFERENCES dvr_commercial_fingerprints(id) ON DELETE CASCADE,
            FOREIGN KEY (matched_sample_id) REFERENCES dvr_commercial_samples(id) ON DELETE CASCADE,
            FOREIGN KEY (matched_fingerprint_id) REFERENCES dvr_commercial_fingerprints(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dvr_commercial_comparisons_samples "
        "ON dvr_commercial_comparisons(sample_id, matched_sample_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_commercial_lab_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER NOT NULL UNIQUE,
            sample_id INTEGER NOT NULL,
            channel_name TEXT NOT NULL DEFAULT '',
            detected_breaks INTEGER NOT NULL DEFAULT 0,
            fingerprints_created INTEGER NOT NULL DEFAULT 0,
            comparison_count INTEGER NOT NULL DEFAULT 0,
            best_similarity REAL NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}',
            source_deleted INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL,
            FOREIGN KEY (recording_id) REFERENCES dvr_recordings(id) ON DELETE CASCADE,
            FOREIGN KEY (sample_id) REFERENCES dvr_commercial_samples(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dvr_commercial_lab_runs_processed "
        "ON dvr_commercial_lab_runs(processed_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dvr_commercial_lab_control (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0,
            slots INTEGER NOT NULL DEFAULT 4,
            sample_minutes INTEGER NOT NULL DEFAULT 20,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
