from __future__ import annotations

import sqlite3
from datetime import datetime

import sports as _s


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_schedule_api_cache (
            source TEXT NOT NULL,
            league_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            schedule_date TEXT NOT NULL,
            request_key TEXT NOT NULL DEFAULT '',
            fetched_on TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            remaining_quota INTEGER,
            PRIMARY KEY (source, league_id, season, schedule_date)
        )
        """
    )
    try:
        conn.execute(
            "ALTER TABLE sports_schedule_api_cache "
            "ADD COLUMN request_key TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_schedule_events (
            source TEXT NOT NULL,
            api_event_id TEXT NOT NULL,
            league_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            schedule_date TEXT NOT NULL,
            scheduled_start TEXT NOT NULL,
            status_short TEXT NOT NULL DEFAULT '',
            status_long TEXT NOT NULL DEFAULT '',
            home_api_id TEXT NOT NULL DEFAULT '',
            home_name TEXT NOT NULL DEFAULT '',
            home_logo TEXT NOT NULL DEFAULT '',
            away_api_id TEXT NOT NULL DEFAULT '',
            away_name TEXT NOT NULL DEFAULT '',
            away_logo TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (source, api_event_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_schedule_reference_cache (
            source TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            season INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            remaining_quota INTEGER,
            raw_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (source, cache_key, season)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_catalog (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            league_id TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            logo_url TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'seed',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            feed_preference TEXT NOT NULL DEFAULT 'best',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(scope_type, scope_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_key TEXT NOT NULL UNIQUE,
            source_channel_key TEXT NOT NULL,
            event_key TEXT NOT NULL,
            league_id TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            feed_type TEXT NOT NULL DEFAULT 'event',
            assigned_number INTEGER NOT NULL,
            group_title TEXT NOT NULL,
            url TEXT NOT NULL,
            tvg_id TEXT NOT NULL DEFAULT '',
            source_tvg_id TEXT NOT NULL DEFAULT '',
            tvg_logo TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL,
            event_title TEXT NOT NULL DEFAULT '',
            event_start TEXT,
            event_end TEXT,
            is_replay INTEGER NOT NULL DEFAULT 0,
            epg_programme_json TEXT NOT NULL DEFAULT '{}',
            generated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            event_count INTEGER NOT NULL DEFAULT 0,
            channel_count INTEGER NOT NULL DEFAULT 0,
            target_date TEXT,
            trigger TEXT NOT NULL DEFAULT 'manual'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sports_scan_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            running INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            updated_at TEXT,
            stage TEXT NOT NULL DEFAULT '',
            trigger TEXT NOT NULL DEFAULT 'manual'
        )
        """
    )


def migrate_generated_columns(conn: sqlite3.Connection) -> None:
    for column_name, column_sql in (
        ("source_tvg_id", "TEXT NOT NULL DEFAULT ''"),
        ("event_title", "TEXT NOT NULL DEFAULT ''"),
        ("event_end", "TEXT"),
        ("is_replay", "INTEGER NOT NULL DEFAULT 0"),
        ("epg_programme_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE sports_generated ADD COLUMN {column_name} {column_sql}"
            )
        except sqlite3.OperationalError:
            pass


def migrate_generated_xmltv_ids(conn: sqlite3.Connection) -> None:
    migration_key = "migration_generated_xmltv_ids_v20_4"
    if conn.execute(
        "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    rows = conn.execute(
        """
        SELECT id, event_key, feed_type, source_channel_key, display_name,
               assigned_number, tvg_id, source_tvg_id, raw_json, league_id,
               event_start, event_title, event_end
        FROM sports_generated
        """
    ).fetchall()
    for row in rows:
        old_tvg_id = str(row["tvg_id"] or "")
        new_tvg_id = old_tvg_id
        source_tvg_id = str(row["source_tvg_id"] or "")
        if not old_tvg_id.startswith("m3u-picker.sports."):
            source_tvg_id = source_tvg_id or old_tvg_id
            new_tvg_id = _s._generated_tvg_id(int(row["assigned_number"]))
        raw = _s._json_load(row["raw_json"], [])
        if raw and new_tvg_id != old_tvg_id:
            raw[0] = _s._rewrite_extinf(
                raw[0],
                {"tvg-id": new_tvg_id},
                str(row["display_name"] or "Sports event"),
            )
        event_title = str(row["event_title"] or "").strip()
        if not event_title:
            event_title = _s.re.sub(
                r"\s+—\s+[^—]+$",
                "",
                _s.re.sub(
                    r"^[^•]+•\s*",
                    "",
                    str(row["display_name"] or "Sports event"),
                ),
            ).strip()
        event_end = row["event_end"]
        if not event_end and row["event_start"]:
            try:
                event_end = (
                    datetime.fromisoformat(row["event_start"])
                    + _s._event_duration(str(row["league_id"] or ""))
                ).isoformat()
            except (TypeError, ValueError, OverflowError):
                event_end = None
        conn.execute(
            """
            UPDATE sports_generated
            SET tvg_id = ?, source_tvg_id = ?, raw_json = ?,
                event_title = ?, event_end = ?
            WHERE id = ?
            """,
            (
                new_tvg_id,
                source_tvg_id,
                _s.json.dumps(raw),
                event_title,
                event_end,
                row["id"],
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
        (migration_key, _s.json.dumps(True)),
    )


def migrate_generated_slot_ids(conn: sqlite3.Connection) -> None:
    migration_key = "migration_generated_xmltv_slot_ids_v20_7"
    if conn.execute(
        "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    rows = conn.execute(
        """
        SELECT id, assigned_number, display_name, tvg_id, raw_json
        FROM sports_generated
        """
    ).fetchall()
    for row in rows:
        new_tvg_id = _s._generated_tvg_id(int(row["assigned_number"]))
        raw = _s._json_load(row["raw_json"], [])
        if raw:
            raw[0] = _s._rewrite_extinf(
                raw[0],
                {"tvg-id": new_tvg_id},
                str(row["display_name"] or "Sports event"),
            )
        conn.execute(
            """
            UPDATE sports_generated
            SET tvg_id = ?, raw_json = ?
            WHERE id = ?
            """,
            (new_tvg_id, _s.json.dumps(raw), row["id"]),
        )
    conn.execute(
        "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
        (migration_key, _s.json.dumps(True)),
    )


def migrate_refresh_time(conn: sqlite3.Connection) -> None:
    refresh_row = conn.execute(
        "SELECT value FROM sports_settings WHERE key = 'refresh_time'"
    ).fetchone()
    if refresh_row is not None:
        return
    legacy = {
        row["key"]: _s._json_load(row["value"], row["value"])
        for row in conn.execute(
            "SELECT key, value FROM sports_settings "
            "WHERE key IN ('refresh_hour', 'refresh_minute')"
        )
    }
    hour, minute = _s._refresh_time_parts(legacy)
    conn.execute(
        "INSERT INTO sports_settings(key, value) VALUES ('refresh_time', ?)",
        (_s.json.dumps(f"{hour:02d}:{minute:02d}"),),
    )


def insert_default_settings(conn: sqlite3.Connection) -> None:
    for key, value in _s.DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO sports_settings(key, value) VALUES (?, ?)",
            (key, _s.json.dumps(value)),
        )


def ensure_disabled_cache_anchor(conn: sqlite3.Connection) -> None:
    enabled_row = conn.execute(
        "SELECT value FROM sports_settings WHERE key = 'enabled'"
    ).fetchone()
    disabled_at_row = conn.execute(
        "SELECT 1 FROM sports_settings WHERE key = ?",
        (_s.SPORTS_DISABLED_AT_KEY,),
    ).fetchone()
    generated_count = int(
        conn.execute("SELECT COUNT(*) FROM sports_generated").fetchone()[0]
    )
    enabled_value = bool(
        _s._json_load(enabled_row["value"], False) if enabled_row else False
    )
    if not enabled_value and generated_count and not disabled_at_row:
        conn.execute(
            "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
            (_s.SPORTS_DISABLED_AT_KEY, _s.json.dumps(_s._now_iso())),
        )


def seed_catalog(conn: sqlite3.Connection) -> None:
    now = _s._now_iso()
    for scope_type, scope_id, name, subtitle, league_id, aliases, logo, metadata in _s.SEED_CATALOG:
        conn.execute(
            """
            INSERT INTO sports_catalog
                (scope_type, scope_id, display_name, subtitle, league_id,
                 aliases_json, logo_url, metadata_json, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?)
            ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                display_name = excluded.display_name,
                subtitle = excluded.subtitle,
                league_id = excluded.league_id,
                aliases_json = excluded.aliases_json,
                logo_url = CASE
                    WHEN sports_catalog.logo_url = '' THEN excluded.logo_url
                    ELSE sports_catalog.logo_url
                END,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            WHERE sports_catalog.source = 'seed'
            """,
            (
                scope_type,
                scope_id,
                name,
                subtitle,
                league_id,
                _s.json.dumps(aliases),
                logo,
                _s.json.dumps(metadata),
                now,
            ),
        )

    valid_seed_keys = {(row[0], row[1]) for row in _s.SEED_CATALOG}
    stale_rows = conn.execute(
        "SELECT scope_type, scope_id FROM sports_catalog WHERE source = 'seed'"
    ).fetchall()
    for row in stale_rows:
        key = (row["scope_type"], row["scope_id"])
        if key not in valid_seed_keys:
            conn.execute(
                "DELETE FROM sports_catalog "
                "WHERE scope_type = ? AND scope_id = ? AND source = 'seed'",
                key,
            )


def migrate_taxonomy_rules(conn: sqlite3.Connection) -> None:
    migration_key = "migration_sports_taxonomy_v21"
    if conn.execute(
        "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    legacy_rule_moves = {
        ("sport", "formula-1"): ("league", "formula-1"),
        ("sport", "ufc"): ("league", "ufc"),
        ("league", "ncaaf"): ("league", "ncaaf-fbs"),
        ("league", "ncaab"): ("league", "ncaab-men"),
        ("conference", "ncaaf:big-ten"): ("conference", "ncaaf-fbs:big-ten"),
        ("conference", "ncaaf:acc"): ("conference", "ncaaf-fbs:acc"),
        ("conference", "ncaaf:sec"): ("conference", "ncaaf-fbs:sec"),
    }
    for old_key, new_key in legacy_rule_moves.items():
        row = conn.execute(
            """
            SELECT display_name, feed_preference, enabled, created_at, updated_at
            FROM sports_rules WHERE scope_type = ? AND scope_id = ?
            """,
            old_key,
        ).fetchone()
        if not row:
            continue
        catalog_row = conn.execute(
            "SELECT display_name FROM sports_catalog WHERE scope_type = ? AND scope_id = ?",
            new_key,
        ).fetchone()
        display_name = catalog_row["display_name"] if catalog_row else row["display_name"]
        conn.execute(
            """
            INSERT OR IGNORE INTO sports_rules
                (scope_type, scope_id, display_name, feed_preference, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *new_key,
                display_name,
                row["feed_preference"],
                row["enabled"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        conn.execute(
            "DELETE FROM sports_rules WHERE scope_type = ? AND scope_id = ?",
            old_key,
        )
    conn.execute(
        "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
        (migration_key, _s.json.dumps(True)),
    )


def remove_legacy_demo_rules(conn: sqlite3.Connection) -> None:
    migration_key = "migration_removed_v20_1_demo_rules"
    if conn.execute(
        "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    rows = conn.execute(
        """
        SELECT scope_type, scope_id, created_at, updated_at
        FROM sports_rules
        """
    ).fetchall()
    signature = {(row["scope_type"], row["scope_id"]) for row in rows}
    untouched = all(row["created_at"] == row["updated_at"] for row in rows)
    if (
        len(rows) == len(_s.LEGACY_DEMO_RULES)
        and signature == _s.LEGACY_DEMO_RULES
        and untouched
    ):
        conn.execute("DELETE FROM sports_rules")
    conn.execute(
        "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
        (migration_key, _s.json.dumps(True)),
    )


def initialize(conn: sqlite3.Connection) -> None:
    """Create/migrate the sports schema in small, ordered, idempotent steps."""
    ensure_schema(conn)
    migrate_generated_columns(conn)
    migrate_generated_xmltv_ids(conn)
    migrate_generated_slot_ids(conn)
    migrate_refresh_time(conn)
    insert_default_settings(conn)
    ensure_disabled_cache_anchor(conn)
    seed_catalog(conn)
    migrate_taxonomy_rules(conn)
    remove_legacy_demo_rules(conn)
    conn.commit()
