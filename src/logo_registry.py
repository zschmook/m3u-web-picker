from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


_MAX_IDENTITY_LENGTH = 240
_MAX_SOURCE_LENGTH = 80
_MAX_URL_LENGTH = 4096
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LOG = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_identity(value: object) -> str:
    text = _CONTROL_CHARS.sub("", str(value or "")).strip()
    return text[:_MAX_IDENTITY_LENGTH]


def _identity_part(value: object, *, limit: int = 90) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:limit]


def channel_identity(channel: dict) -> str:
    tvg_id = str(channel.get("tvg_id", "") or "").strip().casefold()
    if tvg_id:
        return normalize_identity(f"tvg:{tvg_id}")
    name = _identity_part(channel.get("name", ""))
    group = _identity_part(channel.get("group", ""))
    if not name and not group:
        return ""
    return normalize_identity(f"channel:{name}:{group}")


def team_identity(team_id: object) -> str:
    value = str(team_id or "").strip().casefold()
    return normalize_identity(f"team:{value}") if value else ""


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logo_registry (
            identity_key TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            cache_digest TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_success_at TEXT,
            last_failure_at TEXT,
            failure_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_logo_registry_source_url "
        "ON logo_registry(source_url)"
    )
    conn.commit()


def ensure_schema(db_path: Path | str) -> None:
    with closing(_connect(db_path)):
        pass


def _clean_source(value: object) -> str:
    return _CONTROL_CHARS.sub("", str(value or "")).strip()[:_MAX_SOURCE_LENGTH]


def _clean_url(value: object) -> str:
    return _CONTROL_CHARS.sub("", str(value or "")).strip()[:_MAX_URL_LENGTH]


def observe(
    db_path: Path | str,
    identity_key: object,
    source_url: object = "",
    source_kind: object = "",
) -> bool:
    key = normalize_identity(identity_key)
    if not key:
        return False
    url = _clean_url(source_url)
    source = _clean_source(source_kind)
    now = _now_iso()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO logo_registry
                (identity_key, source_kind, source_url, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(identity_key) DO UPDATE SET
                source_kind = CASE
                    WHEN excluded.source_kind <> '' THEN excluded.source_kind
                    ELSE logo_registry.source_kind
                END,
                source_url = CASE
                    WHEN excluded.source_url <> '' THEN excluded.source_url
                    ELSE logo_registry.source_url
                END,
                last_seen_at = excluded.last_seen_at
            """,
            (key, source, url, now, now),
        )
        conn.commit()
    return True


def observe_many(
    db_path: Path | str,
    entries: Iterable[tuple[object, object, object]],
) -> int:
    rows: list[tuple[str, str, str, str, str]] = []
    now = _now_iso()
    seen: set[str] = set()
    for identity_key, source_url, source_kind in entries:
        key = normalize_identity(identity_key)
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(
            (
                key,
                _clean_source(source_kind),
                _clean_url(source_url),
                now,
                now,
            )
        )
    if not rows:
        return 0
    with closing(_connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT INTO logo_registry
                (identity_key, source_kind, source_url, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(identity_key) DO UPDATE SET
                source_kind = CASE
                    WHEN excluded.source_kind <> '' THEN excluded.source_kind
                    ELSE logo_registry.source_kind
                END,
                source_url = CASE
                    WHEN excluded.source_url <> '' THEN excluded.source_url
                    ELSE logo_registry.source_url
                END,
                last_seen_at = excluded.last_seen_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def lookup(db_path: Path | str, identity_key: object) -> dict | None:
    key = normalize_identity(identity_key)
    if not key:
        return None
    try:
        with closing(_connect(db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM logo_registry WHERE identity_key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        # Logo registry metadata must never prevent a cached/upstream image from
        # being used. A transient busy/locked database simply behaves like a
        # cache miss and the image pipeline continues.
        _LOG.warning("Logo registry lookup failed for %s: %s", key, exc)
        return None


def record_success(
    db_path: Path | str,
    identity_key: object,
    *,
    source_url: object,
    source_kind: object,
    cache_digest: object,
    content_type: object,
) -> None:
    key = normalize_identity(identity_key)
    if not key:
        return
    now = _now_iso()
    url = _clean_url(source_url)
    source = _clean_source(source_kind)
    digest = str(cache_digest or "").strip()[:128]
    mime = str(content_type or "").strip()[:160]
    try:
        with closing(_connect(db_path)) as conn:
            conn.execute(
                """
                INSERT INTO logo_registry
                    (identity_key, source_kind, source_url, cache_digest, content_type,
                     first_seen_at, last_seen_at, last_success_at, failure_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(identity_key) DO UPDATE SET
                    source_kind = CASE
                        WHEN excluded.source_kind <> '' THEN excluded.source_kind
                        ELSE logo_registry.source_kind
                    END,
                    source_url = CASE
                        WHEN excluded.source_url <> '' THEN excluded.source_url
                        ELSE logo_registry.source_url
                    END,
                    cache_digest = excluded.cache_digest,
                    content_type = excluded.content_type,
                    last_seen_at = excluded.last_seen_at,
                    last_success_at = excluded.last_success_at,
                    failure_count = 0
                """,
                (key, source, url, digest, mime, now, now, now),
            )
            conn.commit()
    except sqlite3.Error as exc:
        # The bytes have already been fetched/cached when this is called. Do not
        # turn successful image retrieval into a placeholder because optional
        # registry bookkeeping hit a transient SQLite error.
        _LOG.warning("Logo registry success write failed for %s: %s", key, exc)


def record_failure(
    db_path: Path | str,
    identity_key: object,
    *,
    source_url: object = "",
    source_kind: object = "",
) -> None:
    key = normalize_identity(identity_key)
    if not key:
        return
    observe(db_path, key, source_url, source_kind)
    now = _now_iso()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            UPDATE logo_registry
            SET last_seen_at = ?, last_failure_at = ?, failure_count = failure_count + 1
            WHERE identity_key = ?
            """,
            (now, now, key),
        )
        conn.commit()
