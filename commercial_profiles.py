from __future__ import annotations

import math
import json
import re
import sqlite3
import threading
import time
import hashlib
import hmac
import lzma
import pickle
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


RETENTION_DAYS = 14
USER_SAMPLE_WEIGHT = 12
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
MAX_CHANNEL_PROFILE_BYTES = 50 * 1024 * 1024
_ESTIMATED_BYTES_PER_OBSERVATION = 300
MAX_OBSERVATIONS_PER_CHANNEL = max(
    1, MAX_CHANNEL_PROFILE_BYTES // _ESTIMATED_BYTES_PER_OBSERVATION
)
FEATURE_NAMES = (
    "cut_density",
    "mean_color_change",
    "color_volatility",
    "edge_density",
    "mean_brightness",
    "mean_saturation",
    "program_graphics_confidence",
    "bug_identity_confidence",
)
HISTORY_SIGNAL_NAMES = (*FEATURE_NAMES, "commercial_confidence")
DEFAULT_FEATURE_WEIGHTS = {
    "cut_density": 0.25,
    "mean_color_change": 0.10,
    "color_volatility": 0.15,
    "edge_density": 0.15,
    "mean_brightness": 0.05,
    "mean_saturation": 0.05,
    "program_graphics_confidence": 0.25,
    # This is retained for diagnostics. The live detector already uses the
    # trusted-bug score directly, so counting it here would double its weight.
    "bug_identity_confidence": 0.0,
}
MAX_TRUSTED_BUGS_PER_CHANNEL = 12
TRUSTED_BUG_MATCH_THRESHOLD = 0.72
BUG_FINGERPRINT_WIDTH = 48
BUG_FINGERPRINT_HEIGHT = 24
_VALID_LABELS = {"program", "commercial", "uncertain"}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE: dict[str, float] = {}
BLOB_FORMAT_VERSION = 1
BLOB_FORMAT_NAME = "m3u-commercial-profile-v1"
BLOB_PBKDF2_ITERATIONS = 220_000
BLOB_PAYLOAD_SALT_BYTES = 16
BLOB_PAYLOAD_NONCE_BYTES = 16
BLOB_HMAC_BYTES = 32


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_identity(value: object) -> str:
    return _CONTROL_CHARS.sub("", str(value or "")).strip()[:240]


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_channel_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_identity TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            label TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'inferred',
            event_id TEXT NOT NULL DEFAULT '',
            detector_state TEXT NOT NULL DEFAULT '',
            commercial_reason TEXT NOT NULL DEFAULT '',
            cut_density REAL NOT NULL DEFAULT 0,
            mean_color_change REAL NOT NULL DEFAULT 0,
            color_volatility REAL NOT NULL DEFAULT 0,
            edge_density REAL NOT NULL DEFAULT 0,
            mean_brightness REAL NOT NULL DEFAULT 0,
            mean_saturation REAL NOT NULL DEFAULT 0,
            program_graphics_confidence REAL NOT NULL DEFAULT 0,
            bug_identity_confidence REAL NOT NULL DEFAULT 0,
            commercial_confidence REAL NOT NULL DEFAULT 0
        )
        """
    )
    observation_columns = {
        str(row[1]) for row in conn.execute(
            "PRAGMA table_info(commercial_channel_observations)"
        ).fetchall()
    }
    if "bug_identity_confidence" not in observation_columns:
        conn.execute(
            "ALTER TABLE commercial_channel_observations "
            "ADD COLUMN bug_identity_confidence REAL NOT NULL DEFAULT 0"
        )
    if "commercial_confidence" not in observation_columns:
        conn.execute(
            "ALTER TABLE commercial_channel_observations "
            "ADD COLUMN commercial_confidence REAL NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_channel_bugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_identity TEXT NOT NULL,
            region TEXT NOT NULL,
            fingerprint BLOB NOT NULL,
            observed_ticks INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_channel_bug_positions (
            bug_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            observed_ticks INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (bug_id, region),
            FOREIGN KEY (bug_id) REFERENCES commercial_channel_bugs(id)
                ON DELETE CASCADE
        )
        """
    )
    # Backfill the position bank for databases created before identity and
    # position were separated. INSERT OR IGNORE makes this migration safe on
    # every startup.
    conn.execute(
        """
        INSERT OR IGNORE INTO commercial_channel_bug_positions (
            bug_id, region, observed_ticks, first_seen, last_seen
        )
        SELECT id, region, observed_ticks, first_seen, last_seen
        FROM commercial_channel_bugs
        WHERE region <> ''
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_bugs_channel "
        "ON commercial_channel_bugs(channel_identity, last_seen)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_bug_positions_region "
        "ON commercial_channel_bug_positions(region, last_seen)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_observations_channel_time "
        "ON commercial_channel_observations(channel_identity, observed_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_observations_time "
        "ON commercial_channel_observations(observed_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_channel_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_identity TEXT NOT NULL,
            event_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds REAL NOT NULL DEFAULT 0,
            entry_reason TEXT NOT NULL DEFAULT '',
            exit_reason TEXT NOT NULL DEFAULT '',
            start_commercial_confidence REAL NOT NULL DEFAULT 0,
            peak_commercial_confidence REAL NOT NULL DEFAULT 0,
            end_commercial_confidence REAL NOT NULL DEFAULT 0,
            start_bug_confidence REAL NOT NULL DEFAULT 0,
            average_bug_confidence REAL NOT NULL DEFAULT 0,
            end_bug_confidence REAL NOT NULL DEFAULT 0,
            start_program_graphics_confidence REAL NOT NULL DEFAULT 0,
            average_program_graphics_confidence REAL NOT NULL DEFAULT 0,
            end_program_graphics_confidence REAL NOT NULL DEFAULT 0,
            signature_ids TEXT NOT NULL DEFAULT '[]',
            signature_windows INTEGER NOT NULL DEFAULT 0,
            user_confirmed INTEGER NOT NULL DEFAULT 0,
            short_false_positive INTEGER NOT NULL DEFAULT 0,
            UNIQUE(channel_identity, event_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_episodes_channel_time "
        "ON commercial_channel_episodes(channel_identity, started_at)"
    )
    conn.commit()


def ensure_schema(db_path: Path | str) -> None:
    with closing(_connect(db_path)):
        pass


def _clean_label(value: object) -> str:
    label = str(value or "uncertain").strip().lower()
    return label if label in _VALID_LABELS else "uncertain"


def _bounded_feature(value: object) -> float:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return max(0.0, min(1.0, numeric))


def record_with_metadata(
    db_path: Path | str,
    channel_identity: object,
    *,
    label: object,
    features: dict[str, object],
    observed_at: datetime | None = None,
    source: object = "inferred",
    event_id: object = "",
    detector_state: object = "",
    commercial_reason: object = "",
) -> dict:
    identity = normalize_identity(channel_identity)
    if not identity:
        return {"recorded": False, "id": 0, "observed_at": ""}
    timestamp = (observed_at or _utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")
    values = [_bounded_feature(features.get(name, 0)) for name in HISTORY_SIGNAL_NAMES]
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            f"""
            INSERT INTO commercial_channel_observations (
                channel_identity, observed_at, label, source, event_id,
                detector_state, commercial_reason, {', '.join(HISTORY_SIGNAL_NAMES)}
            ) VALUES (?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in HISTORY_SIGNAL_NAMES)})
            """,
            (
                identity,
                timestamp,
                _clean_label(label),
                str(source or "inferred")[:40],
                str(event_id or "")[:120],
                str(detector_state or "")[:40],
                str(commercial_reason or "")[:40],
                *values,
            ),
        )
        _compact_channel_if_overfull(conn, identity)
        conn.commit()
    maybe_prune(db_path)
    return {
        "recorded": True,
        "id": int(cursor.lastrowid or 0),
        "observed_at": timestamp,
        "channel_identity": identity,
    }


def record(
    db_path: Path | str,
    channel_identity: object,
    *,
    label: object,
    features: dict[str, object],
    observed_at: datetime | None = None,
    source: object = "inferred",
    event_id: object = "",
    detector_state: object = "",
    commercial_reason: object = "",
) -> bool:
    result = record_with_metadata(
        db_path,
        channel_identity,
        label=label,
        features=features,
        observed_at=observed_at,
        source=source,
        event_id=event_id,
        detector_state=detector_state,
        commercial_reason=commercial_reason,
    )
    return bool(result.get("recorded"))


def record_many(db_path: Path | str, observations: Iterable[dict]) -> int:
    count = 0
    for observation in observations:
        if record(db_path, **dict(observation)):
            count += 1
    return count


def relabel_event_as_false_positive(
    db_path: Path | str,
    channel_identity: object,
    event_id: object,
) -> int:
    """Turn a short inferred commercial episode into program training data."""
    identity = normalize_identity(channel_identity)
    clean_event_id = str(event_id or "").strip()[:120]
    if not identity or not clean_event_id:
        return 0
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            """
            UPDATE commercial_channel_observations
            SET label = 'program', source = 'auto-false-positive',
                commercial_reason = 'short-false-positive'
            WHERE channel_identity = ? AND event_id = ?
              AND label = 'commercial' AND source <> 'user'
            """,
            (identity, clean_event_id),
        )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))


def discard_recent_possible_commercials(
    db_path: Path | str,
    channel_identity: object,
    *,
    seconds: float = 10.0,
    observed_at: datetime | None = None,
) -> int:
    """Discard recent inferred ad/uncertain samples after explicit program feedback."""
    identity = normalize_identity(channel_identity)
    if not identity:
        return 0
    window_seconds = max(0.0, min(60.0, float(seconds or 0)))
    cutoff = (
        (observed_at or _utc_now()).astimezone(timezone.utc)
        - timedelta(seconds=window_seconds)
    ).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            """
            DELETE FROM commercial_channel_observations
            WHERE channel_identity = ? AND observed_at >= ?
              AND label IN ('commercial', 'uncertain')
              AND source <> 'user'
            """,
            (identity, cutoff),
        )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))


def begin_commercial_episode(
    db_path: Path | str,
    channel_identity: object,
    event_id: object,
    *,
    entry_reason: object = "",
    features: dict | None = None,
    observed_at: datetime | None = None,
) -> bool:
    """Open one compact, auditable commercial-decision episode."""
    identity = normalize_identity(channel_identity)
    clean_event_id = str(event_id or "").strip()[:120]
    if not identity or not clean_event_id:
        return False
    values = dict(features or {})
    timestamp = (observed_at or _utc_now()).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    commercial_confidence = max(
        0.0, min(1.0, float(values.get("commercial_confidence") or 0))
    )
    bug_confidence = max(
        0.0, min(1.0, float(values.get("bug_identity_confidence") or 0))
    )
    graphics_confidence = max(
        0.0, min(1.0, float(values.get("program_graphics_confidence") or 0))
    )
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO commercial_channel_episodes (
                channel_identity, event_id, started_at, entry_reason,
                start_commercial_confidence, peak_commercial_confidence,
                start_bug_confidence, average_bug_confidence,
                start_program_graphics_confidence,
                average_program_graphics_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity, clean_event_id, timestamp,
                str(entry_reason or "").strip()[:80],
                commercial_confidence, commercial_confidence,
                bug_confidence, bug_confidence,
                graphics_confidence, graphics_confidence,
            ),
        )
        conn.commit()
        return bool(cursor.rowcount)


def finish_commercial_episode(
    db_path: Path | str,
    channel_identity: object,
    event_id: object,
    *,
    exit_reason: object = "program-return",
    features: dict | None = None,
    signature_ids: Iterable[int] = (),
    signature_windows: int = 0,
    user_confirmed: bool = False,
    short_false_positive: bool = False,
    observed_at: datetime | None = None,
) -> bool:
    """Close an episode and summarize its confidence trajectory compactly."""
    identity = normalize_identity(channel_identity)
    clean_event_id = str(event_id or "").strip()[:120]
    if not identity or not clean_event_id:
        return False
    values = dict(features or {})
    ended = (observed_at or _utc_now()).astimezone(timezone.utc)
    ended_at = ended.isoformat(timespec="seconds")
    clean_ids = sorted({max(0, int(value or 0)) for value in signature_ids if int(value or 0) > 0})
    with closing(_connect(db_path)) as conn:
        episode = conn.execute(
            """
            SELECT started_at, start_commercial_confidence,
                   start_bug_confidence, start_program_graphics_confidence
            FROM commercial_channel_episodes
            WHERE channel_identity = ? AND event_id = ?
            """,
            (identity, clean_event_id),
        ).fetchone()
        if episode is None:
            start_commercial = max(
                0.0, min(1.0, float(values.get("commercial_confidence") or 0))
            )
            start_bug = max(
                0.0, min(1.0, float(values.get("bug_identity_confidence") or 0))
            )
            start_graphics = max(
                0.0,
                min(1.0, float(values.get("program_graphics_confidence") or 0)),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO commercial_channel_episodes (
                    channel_identity, event_id, started_at, entry_reason,
                    start_commercial_confidence, peak_commercial_confidence,
                    start_bug_confidence, average_bug_confidence,
                    start_program_graphics_confidence,
                    average_program_graphics_confidence
                ) VALUES (?, ?, ?, 'unknown', ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity, clean_event_id, ended_at,
                    start_commercial, start_commercial,
                    start_bug, start_bug, start_graphics, start_graphics,
                ),
            )
            episode = conn.execute(
                """
                SELECT started_at, start_commercial_confidence,
                       start_bug_confidence, start_program_graphics_confidence
                FROM commercial_channel_episodes
                WHERE channel_identity = ? AND event_id = ?
                """,
                (identity, clean_event_id),
            ).fetchone()
        started = datetime.fromisoformat(str(episode["started_at"]))
        duration = max(0.0, (ended - started.astimezone(timezone.utc)).total_seconds())
        trajectory = conn.execute(
            """
            SELECT
                MAX(commercial_confidence) peak_commercial,
                AVG(bug_identity_confidence) average_bug,
                AVG(program_graphics_confidence) average_graphics
            FROM commercial_channel_observations
            WHERE channel_identity = ? AND event_id = ?
            """,
            (identity, clean_event_id),
        ).fetchone()
        end_commercial = max(
            0.0, min(1.0, float(values.get("commercial_confidence") or 0))
        )
        end_bug = max(0.0, min(1.0, float(values.get("bug_identity_confidence") or 0)))
        end_graphics = max(
            0.0, min(1.0, float(values.get("program_graphics_confidence") or 0))
        )
        peak = max(
            float(episode["start_commercial_confidence"] or 0),
            float(trajectory["peak_commercial"] or 0),
            end_commercial,
        )
        average_bug = float(
            trajectory["average_bug"]
            if trajectory["average_bug"] is not None
            else episode["start_bug_confidence"] or 0
        )
        average_graphics = float(
            trajectory["average_graphics"]
            if trajectory["average_graphics"] is not None
            else episode["start_program_graphics_confidence"] or 0
        )
        conn.execute(
            """
            UPDATE commercial_channel_episodes
            SET ended_at = ?, duration_seconds = ?, exit_reason = ?,
                peak_commercial_confidence = ?, end_commercial_confidence = ?,
                average_bug_confidence = ?, end_bug_confidence = ?,
                average_program_graphics_confidence = ?,
                end_program_graphics_confidence = ?,
                signature_ids = ?, signature_windows = ?,
                user_confirmed = ?, short_false_positive = ?
            WHERE channel_identity = ? AND event_id = ?
            """,
            (
                ended_at, duration, str(exit_reason or "program-return").strip()[:80],
                peak, end_commercial, average_bug, end_bug,
                average_graphics, end_graphics, json.dumps(clean_ids),
                max(0, int(signature_windows or 0)), int(bool(user_confirmed)),
                int(bool(short_false_positive)), identity, clean_event_id,
            ),
        )
        conn.commit()
    return True


def episodes_between(
    db_path: Path | str,
    channel_identity: object,
    started_at: object,
    ended_at: object,
) -> list[dict]:
    identity = normalize_identity(channel_identity)
    if not identity:
        return []
    def normalized_timestamp(value: object) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat(timespec="seconds")
        return str(value or "")
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM commercial_channel_episodes
            WHERE channel_identity = ? AND started_at >= ? AND started_at <= ?
            ORDER BY started_at, id
            """,
            (
                identity,
                normalized_timestamp(started_at),
                normalized_timestamp(ended_at),
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def prune(db_path: Path | str, *, now: datetime | None = None) -> int:
    cutoff = ((now or _utc_now()).astimezone(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat(
        timespec="seconds"
    )
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            "DELETE FROM commercial_channel_observations WHERE observed_at < ?",
            (cutoff,),
        )
        bug_cursor = conn.execute(
            "DELETE FROM commercial_channel_bugs WHERE last_seen < ?",
            (cutoff,),
        )
        episode_cursor = conn.execute(
            "DELETE FROM commercial_channel_episodes WHERE started_at < ?",
            (cutoff,),
        )
        conn.execute("PRAGMA optimize")
        compacted = _compact_overfull_channels(conn)
        if compacted > 0:
            conn.execute("PRAGMA optimize")
        conn.commit()
        return (
            max(0, int(cursor.rowcount or 0))
            + max(0, int(bug_cursor.rowcount or 0))
            + max(0, int(episode_cursor.rowcount or 0))
            + max(0, int(compacted or 0))
        )


def _compact_overfull_channels(conn: sqlite3.Connection) -> int:
    if MAX_OBSERVATIONS_PER_CHANNEL <= 0:
        return 0
    totals = conn.execute(
        """
        SELECT channel_identity, COUNT(*) AS count
        FROM commercial_channel_observations
        GROUP BY channel_identity
        """
    ).fetchall()
    total_deleted = 0
    for row in totals:
        channel_identity = str(row["channel_identity"] or "").strip()
        count = int(row["count"] or 0)
        if not channel_identity or count <= MAX_OBSERVATIONS_PER_CHANNEL:
            continue
        keep_recent = max(1, MAX_OBSERVATIONS_PER_CHANNEL)
        delete_count = count - keep_recent
        cursor = conn.execute(
            """
            DELETE FROM commercial_channel_observations
            WHERE id IN (
                SELECT id
                FROM commercial_channel_observations
                WHERE channel_identity = ?
                ORDER BY observed_at ASC, id ASC
                LIMIT ?
            )
            """,
            (channel_identity, delete_count),
        )
        total_deleted += int(cursor.rowcount or 0)
    return total_deleted


def _compact_channel_if_overfull(conn: sqlite3.Connection, channel_identity: str) -> int:
    if MAX_OBSERVATIONS_PER_CHANNEL <= 0:
        return 0
    channel_identity = normalize_identity(channel_identity)
    if not channel_identity:
        return 0
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM commercial_channel_observations WHERE channel_identity = ?",
            (channel_identity,),
        ).fetchone()[0]
        or 0
    )
    if count <= MAX_OBSERVATIONS_PER_CHANNEL:
        return 0
    delete_count = count - MAX_OBSERVATIONS_PER_CHANNEL
    cursor = conn.execute(
        """
        DELETE FROM commercial_channel_observations
        WHERE id IN (
            SELECT id
            FROM commercial_channel_observations
            WHERE channel_identity = ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
        )
        """,
        (channel_identity, delete_count),
    )
    return int(cursor.rowcount or 0)


def maybe_prune(db_path: Path | str, *, monotonic_now: float | None = None) -> int:
    current = float(monotonic_now if monotonic_now is not None else time.monotonic())
    key = str(Path(db_path).resolve())
    with _PRUNE_LOCK:
        previous = _LAST_PRUNE.get(key)
        if previous is not None and current - previous < PRUNE_INTERVAL_SECONDS:
            return 0
        _LAST_PRUNE[key] = current
    return prune(db_path)


def _bug_fingerprint(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    try:
        return bytes(1 if int(item) else 0 for item in value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return b""


def _bug_similarity(left: bytes, right: bytes) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_count = sum(left)
    right_count = sum(right)
    if not left_count or not right_count:
        return 0.0
    intersection = sum(1 for a, b in zip(left, right) if a and b)
    return intersection / float(max(left_count, right_count))


def _translated_bug_similarity(left: bytes, right: bytes) -> float:
    """Compare bug identity independently of its exact position in a region."""
    expected_size = BUG_FINGERPRINT_WIDTH * BUG_FINGERPRINT_HEIGHT
    if len(left) != expected_size or len(right) != expected_size:
        return _bug_similarity(left, right)
    left_count = sum(left)
    right_count = sum(right)
    if not left_count or not right_count:
        return 0.0
    best = 0.0
    for delta_y in range(-4, 5):
        for delta_x in range(-6, 7):
            intersection = 0
            for index, expected in enumerate(left):
                if not expected:
                    continue
                x = (index % BUG_FINGERPRINT_WIDTH) + delta_x
                y = (index // BUG_FINGERPRINT_WIDTH) + delta_y
                if (
                    0 <= x < BUG_FINGERPRINT_WIDTH
                    and 0 <= y < BUG_FINGERPRINT_HEIGHT
                    and right[(y * BUG_FINGERPRINT_WIDTH) + x]
                ):
                    intersection += 1
            best = max(best, intersection / float(max(left_count, right_count)))
    return best


def trusted_bugs(
    db_path: Path | str,
    channel_identity: object,
    *,
    limit: int = MAX_TRUSTED_BUGS_PER_CHANNEL,
) -> list[dict]:
    """Return the channel's bounded set of learned program-graphic identities."""
    identity = normalize_identity(channel_identity)
    if not identity:
        return []
    safe_limit = max(1, min(MAX_TRUSTED_BUGS_PER_CHANNEL, int(limit or 1)))
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, region, fingerprint, observed_ticks, first_seen, last_seen
            FROM commercial_channel_bugs
            WHERE channel_identity = ?
            ORDER BY observed_ticks DESC, last_seen DESC
            LIMIT ?
            """,
            (identity, safe_limit),
        ).fetchall()
        position_rows = conn.execute(
            """
            SELECT bug_id, region, observed_ticks, first_seen, last_seen
            FROM commercial_channel_bug_positions
            WHERE bug_id IN (
                SELECT id FROM commercial_channel_bugs
                WHERE channel_identity = ?
            )
            ORDER BY observed_ticks DESC, last_seen DESC
            """,
            (identity,),
        ).fetchall()
    positions_by_bug: dict[int, list[dict]] = {}
    for row in position_rows:
        positions_by_bug.setdefault(int(row["bug_id"]), []).append(
            {
                "region": str(row["region"] or ""),
                "observed_ticks": int(row["observed_ticks"] or 0),
                "first_seen": str(row["first_seen"] or ""),
                "last_seen": str(row["last_seen"] or ""),
            }
        )
    return [
        {
            "id": int(row["id"]),
            "region": str(row["region"] or ""),
            "regions": [
                position["region"]
                for position in positions_by_bug.get(int(row["id"]), [])
                if position["region"]
            ] or [str(row["region"] or "")],
            "positions": positions_by_bug.get(int(row["id"]), []),
            "fingerprint": tuple(1 if value else 0 for value in bytes(row["fingerprint"])),
            "observed_ticks": int(row["observed_ticks"] or 0),
            "first_seen": str(row["first_seen"] or ""),
            "last_seen": str(row["last_seen"] or ""),
        }
        for row in rows
    ]


def save_trusted_bug(
    db_path: Path | str,
    channel_identity: object,
    *,
    region: object,
    fingerprint: object,
    observed_ticks: int,
    observed_at: datetime | None = None,
) -> bool:
    """Merge a trusted program graphic into a channel's small prototype bank."""
    identity = normalize_identity(channel_identity)
    region_name = str(region or "").strip()[:40]
    packed = _bug_fingerprint(fingerprint)
    ticks = max(1, int(observed_ticks or 1))
    if not identity or not region_name or not packed or not sum(packed):
        return False
    timestamp = (observed_at or _utc_now()).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    with closing(_connect(db_path)) as conn:
        candidates = conn.execute(
            """
            SELECT id, fingerprint
            FROM commercial_channel_bugs
            WHERE channel_identity = ?
            """,
            (identity,),
        ).fetchall()
        matched_id = None
        matched_score = 0.0
        for row in candidates:
            score = _translated_bug_similarity(bytes(row["fingerprint"]), packed)
            if score > matched_score:
                matched_id = int(row["id"])
                matched_score = score
        if matched_id is not None and matched_score >= TRUSTED_BUG_MATCH_THRESHOLD:
            conn.execute(
                """
                UPDATE commercial_channel_bugs
                SET observed_ticks = observed_ticks + ?, last_seen = ?
                WHERE id = ?
                """,
                (ticks, timestamp, matched_id),
            )
            bug_id = matched_id
        else:
            cursor = conn.execute(
                """
                INSERT INTO commercial_channel_bugs (
                    channel_identity, region, fingerprint, observed_ticks,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identity, region_name, packed, ticks, timestamp, timestamp),
            )
            bug_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO commercial_channel_bug_positions (
                bug_id, region, observed_ticks, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bug_id, region) DO UPDATE SET
                observed_ticks = observed_ticks + excluded.observed_ticks,
                last_seen = excluded.last_seen
            """,
            (bug_id, region_name, ticks, timestamp, timestamp),
        )
        excess = int(
            conn.execute(
                "SELECT COUNT(*) FROM commercial_channel_bugs WHERE channel_identity = ?",
                (identity,),
            ).fetchone()[0]
            or 0
        ) - MAX_TRUSTED_BUGS_PER_CHANNEL
        if excess > 0:
            conn.execute(
                """
                DELETE FROM commercial_channel_bugs
                WHERE id IN (
                    SELECT id FROM commercial_channel_bugs
                    WHERE channel_identity = ?
                    ORDER BY observed_ticks ASC, last_seen ASC
                    LIMIT ?
                )
                """,
                (identity, excess),
            )
        conn.commit()
    return True


def clear_learning_data(db_path: Path | str) -> dict[str, int]:
    """Clear only commercial-learning data, leaving all app settings intact."""
    with closing(_connect(db_path)) as conn:
        observation_count = int(
            conn.execute("SELECT COUNT(*) FROM commercial_channel_observations").fetchone()[0]
            or 0
        )
        bug_count = int(
            conn.execute("SELECT COUNT(*) FROM commercial_channel_bugs").fetchone()[0]
            or 0
        )
        position_count = int(
            conn.execute("SELECT COUNT(*) FROM commercial_channel_bug_positions").fetchone()[0]
            or 0
        )
        episode_count = int(
            conn.execute("SELECT COUNT(*) FROM commercial_channel_episodes").fetchone()[0]
            or 0
        )
        existing_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        signature_count = 0
        if "commercial_ad_signatures" in existing_tables:
            signature_count += int(
                conn.execute("SELECT COUNT(*) FROM commercial_ad_signatures").fetchone()[0]
                or 0
            )
        if "commercial_ad_signatures_v2" in existing_tables:
            signature_count += int(
                conn.execute("SELECT COUNT(*) FROM commercial_ad_signatures_v2").fetchone()[0]
                or 0
            )
        if "commercial_ad_signature_events_v2" in existing_tables:
            conn.execute("DELETE FROM commercial_ad_signature_events_v2")
        if "commercial_ad_signatures_v2" in existing_tables:
            conn.execute("DELETE FROM commercial_ad_signatures_v2")
        if "commercial_ad_signature_channels" in existing_tables:
            conn.execute("DELETE FROM commercial_ad_signature_channels")
        if "commercial_ad_signatures" in existing_tables:
            conn.execute("DELETE FROM commercial_ad_signatures")
        conn.execute("DELETE FROM commercial_channel_bug_positions")
        conn.execute("DELETE FROM commercial_channel_bugs")
        conn.execute("DELETE FROM commercial_channel_observations")
        conn.execute("DELETE FROM commercial_channel_episodes")
        conn.commit()
    import commercial_signatures
    commercial_signatures.invalidate_cache(db_path)
    _LAST_PRUNE.pop(str(Path(db_path).resolve()), None)
    return {
        "observations": observation_count,
        "bugs": bug_count,
        "positions": position_count,
        "signatures": signature_count,
        "episodes": episode_count,
    }


def _row_weight(row: sqlite3.Row) -> int:
    return USER_SAMPLE_WEIGHT if row["source"] == "user" else 1


def _feature_stats(rows: list[sqlite3.Row]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for name in FEATURE_NAMES:
        weighted_values = [
            (float(row[name] or 0), _row_weight(row)) for row in rows
        ]
        total_weight = sum(weight for _value, weight in weighted_values)
        if not total_weight:
            result[name] = {"mean": 0.0, "stddev": 0.0}
            continue
        mean = sum(value * weight for value, weight in weighted_values) / total_weight
        variance = sum(
            weight * (value - mean) ** 2 for value, weight in weighted_values
        ) / total_weight
        result[name] = {"mean": mean, "stddev": math.sqrt(variance)}
    return result


def profile(db_path: Path | str, channel_identity: object) -> dict:
    identity = normalize_identity(channel_identity)
    if not identity:
        return {
            "channel_identity": "",
            "program_samples": 0,
            "commercial_samples": 0,
            "logo_missing_episodes": 0,
            "logo_missing_short_false_positives": 0,
            "logo_missing_false_positive_rate": 0.0,
        }
    episode_cutoff = (
        _utc_now() - timedelta(days=RETENTION_DAYS)
    ).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT label, source, {', '.join(FEATURE_NAMES)}
            FROM commercial_channel_observations
            WHERE channel_identity = ?
            ORDER BY observed_at
            """,
            (identity,),
        ).fetchall()
        episode_row = conn.execute(
            """
            SELECT COUNT(*) AS episode_count,
                   COALESCE(SUM(short_false_positive), 0) AS short_false_positives
            FROM commercial_channel_episodes
            WHERE channel_identity = ? AND entry_reason = 'logo-missing'
              AND ended_at IS NOT NULL AND started_at >= ?
            """,
            (identity, episode_cutoff),
        ).fetchone()
    program = [row for row in rows if row["label"] == "program"]
    commercial = [row for row in rows if row["label"] == "commercial"]
    confirmed = [row for row in commercial if row["source"] == "user"]
    effective_program = sum(_row_weight(row) for row in program)
    effective_commercial = sum(_row_weight(row) for row in commercial)
    logo_missing_episodes = int(episode_row["episode_count"] or 0)
    logo_missing_short_false_positives = int(
        episode_row["short_false_positives"] or 0
    )
    return {
        "channel_identity": identity,
        "program_samples": len(program),
        "commercial_samples": len(commercial),
        "effective_program_samples": effective_program,
        "effective_commercial_samples": effective_commercial,
        "user_confirmed_commercial_samples": len(confirmed),
        "logo_missing_episodes": logo_missing_episodes,
        "logo_missing_short_false_positives": logo_missing_short_false_positives,
        "logo_missing_false_positive_rate": (
            logo_missing_short_false_positives / logo_missing_episodes
            if logo_missing_episodes
            else 0.0
        ),
        "ready": effective_program >= 30 and effective_commercial >= 3,
        "program": _feature_stats(program),
        "commercial": _feature_stats(commercial),
    }


def recent(
    db_path: Path | str,
    channel_identity: object,
    *,
    limit: int = 72,
    minutes: int | None = None,
) -> list[dict]:
    identity = normalize_identity(channel_identity)
    if not identity:
        return []
    safe_limit = max(1, min(288, int(limit or 72)))
    safe_minutes = max(1, min(120, int(minutes or 0))) if minutes else 0
    with closing(_connect(db_path)) as conn:
        if safe_minutes:
            cutoff = (_utc_now() - timedelta(minutes=safe_minutes)).isoformat(timespec="seconds")
            rows = conn.execute(
                f"""
                SELECT observed_at, label, source, {', '.join(HISTORY_SIGNAL_NAMES)}
                FROM commercial_channel_observations
                WHERE channel_identity = ? AND observed_at >= ?
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (identity, cutoff, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT observed_at, label, source, {', '.join(HISTORY_SIGNAL_NAMES)}
                FROM commercial_channel_observations
                WHERE channel_identity = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (identity, safe_limit),
            ).fetchall()
    return [
        {
            "observed_at": row["observed_at"],
            "label": row["label"],
            "source": row["source"],
            "features": {name: float(row[name] or 0) for name in HISTORY_SIGNAL_NAMES},
        }
        for row in reversed(rows)
    ]


def _normalize_passphrase(passphrase: object) -> str:
    return str(passphrase or "").strip()


def _binary_field_from_blob(value: object, *, allow_base64_compat: bool = False) -> bytes:
    if isinstance(value, bytes):
        return value
    if allow_base64_compat and isinstance(value, str):
        import base64

        return base64.urlsafe_b64decode(value.encode("ascii", errors="ignore"))
    if isinstance(value, bytearray):
        return bytes(value)
    raise ValueError("Invalid profile blob field type.")


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    passphrase_bytes = passphrase.encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase_bytes,
        salt,
        BLOB_PBKDF2_ITERATIONS,
        dklen=32,
    )


def _xor_stream(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    key_stream = bytearray()
    counter = 0
    while len(key_stream) < len(payload):
        counter_bytes = counter.to_bytes(8, byteorder="big", signed=False)
        block = hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest()
        key_stream.extend(block)
        counter += 1
    return bytes(
        payload_byte ^ key_stream[index]
        for index, payload_byte in enumerate(payload)
    )


def _pack_blob(payload: dict, passphrase: str) -> bytes:
    raw_payload = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    compressed_payload = lzma.compress(raw_payload, format=lzma.FORMAT_XZ)
    salt = secrets.token_bytes(BLOB_PAYLOAD_SALT_BYTES)
    nonce = secrets.token_bytes(BLOB_PAYLOAD_NONCE_BYTES)
    key = _derive_key(passphrase, salt)
    encrypted = _xor_stream(key, nonce, compressed_payload)
    signature = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    envelope = {
        "format": BLOB_FORMAT_NAME,
        "version": BLOB_FORMAT_VERSION,
        "iterations": BLOB_PBKDF2_ITERATIONS,
        "salt": salt,
        "nonce": nonce,
        "encrypted": encrypted,
        "hmac": signature,
        "encoding": "pickle-xz-xor",
    }
    return lzma.compress(
        pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL),
        format=lzma.FORMAT_XZ,
    )


def _unpack_blob(blob: bytes, passphrase: str) -> dict:
    try:
        envelope = pickle.loads(lzma.decompress(blob))
    except Exception as exc:
        raise ValueError("Could not decode profile blob.") from exc
    if not isinstance(envelope, dict):
        raise ValueError("Invalid profile blob format.")
    if envelope.get("format") != BLOB_FORMAT_NAME:
        raise ValueError("Unknown profile blob format.")
    if int(envelope.get("version", 0) or 0) != BLOB_FORMAT_VERSION:
        raise ValueError("Unsupported profile blob version.")
    try:
        salt = _binary_field_from_blob(
            envelope.get("salt"),
            allow_base64_compat=bool(str(envelope.get("encoding", "")) == "pickle-xz-xor-base64"),
        )
        nonce = _binary_field_from_blob(
            envelope.get("nonce"),
            allow_base64_compat=bool(str(envelope.get("encoding", "")) == "pickle-xz-xor-base64"),
        )
        encrypted = _binary_field_from_blob(
            envelope.get("encrypted"),
            allow_base64_compat=bool(str(envelope.get("encoding", "")) == "pickle-xz-xor-base64"),
        )
        expected_hmac = _binary_field_from_blob(
            envelope.get("hmac"),
            allow_base64_compat=bool(str(envelope.get("encoding", "")) == "pickle-xz-xor-base64"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid profile blob encoding.") from exc
    if len(expected_hmac) != BLOB_HMAC_BYTES:
        raise ValueError("Invalid profile blob signature.")

    key = _derive_key(_normalize_passphrase(passphrase), salt)
    actual_hmac = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_hmac, actual_hmac):
        raise ValueError("Incorrect passphrase or corrupted profile blob.")

    compressed_payload = _xor_stream(key, nonce, encrypted)
    try:
        payload = pickle.loads(lzma.decompress(compressed_payload))
    except Exception as exc:
        raise ValueError("Could not decrypt profile blob.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid profile blob payload.")
    return payload


def dump_profile_blob(
    db_path: Path | str,
    *,
    channel_identities: list[str] | None = None,
    passphrase: str,
) -> bytes:
    """Return a compact encrypted profile blob for import/export."""
    passphrase = _normalize_passphrase(passphrase)
    if not passphrase:
        raise ValueError("Passphrase is required.")
    payload = export_observations(db_path, channel_identities=channel_identities)
    return _pack_blob(payload, passphrase=passphrase)


def load_profile_blob(
    db_path: Path | str,
    blob: bytes,
    *,
    passphrase: str,
    overwrite: bool = False,
) -> dict[str, int]:
    """Load an encrypted profile blob and merge it into a local DB."""
    passphrase = _normalize_passphrase(passphrase)
    if not passphrase:
        raise ValueError("Passphrase is required.")
    profile_payload = _unpack_blob(blob, passphrase)
    observations = profile_payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Profile blob does not contain observations.")
    return import_observations(db_path, observations, overwrite=overwrite)


def export_observations(db_path: Path | str, *, channel_identities: list[str] | None = None) -> dict:
    """Export learned commercial observations for sharing across installs.

    The caller can optionally narrow the export to one or more identities.
    """
    identities = [normalize_identity(identity) for identity in (channel_identities or [])]
    identities = [identity for identity in identities if identity]
    with closing(_connect(db_path)) as conn:
        if identities:
            placeholders = ", ".join(["?"] * len(identities))
            rows = conn.execute(
                f"""
                SELECT id, observed_at, label, source, event_id,
                    detector_state, commercial_reason, cut_density, mean_color_change,
                    color_volatility, edge_density, mean_brightness, mean_saturation,
                    program_graphics_confidence, bug_identity_confidence,
                    commercial_confidence,
                    channel_identity
                FROM commercial_channel_observations
                WHERE channel_identity IN ({placeholders})
                ORDER BY observed_at, id
                """,
                identities,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, observed_at, label, source, event_id,
                    detector_state, commercial_reason, cut_density, mean_color_change,
                    color_volatility, edge_density, mean_brightness, mean_saturation,
                    program_graphics_confidence, bug_identity_confidence,
                    commercial_confidence,
                    channel_identity
                FROM commercial_channel_observations
                ORDER BY observed_at, id
                """
            ).fetchall()

    observations = [
        {
            "channel_identity": row["channel_identity"],
            "observed_at": row["observed_at"],
            "label": row["label"],
            "source": row["source"],
            "event_id": row["event_id"],
            "detector_state": row["detector_state"],
            "commercial_reason": row["commercial_reason"],
            "cut_density": row["cut_density"],
            "mean_color_change": row["mean_color_change"],
            "color_volatility": row["color_volatility"],
            "edge_density": row["edge_density"],
            "mean_brightness": row["mean_brightness"],
            "mean_saturation": row["mean_saturation"],
            "program_graphics_confidence": row["program_graphics_confidence"],
            "bug_identity_confidence": row["bug_identity_confidence"],
            "commercial_confidence": row["commercial_confidence"],
        }
        for row in rows
    ]
    return {
        "version": 1,
        "exported_at": _utc_now().isoformat(timespec="seconds"),
        "retention_days": RETENTION_DAYS,
        "count": len(observations),
        "observations": observations,
    }


def clear_channel_profiles(db_path: Path | str, channel_identities: list[str]) -> int:
    identities = [normalize_identity(identity) for identity in (channel_identities or [])]
    identities = [identity for identity in identities if identity]
    if not identities:
        return 0
    with closing(_connect(db_path)) as conn:
        placeholders = ", ".join(["?"] * len(identities))
        cursor = conn.execute(
            f"""
            DELETE FROM commercial_channel_observations
            WHERE channel_identity IN ({placeholders})
            """,
            identities,
        )
        conn.execute(
            f"""
            DELETE FROM commercial_channel_bugs
            WHERE channel_identity IN ({placeholders})
            """,
            identities,
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def import_observations(
    db_path: Path | str,
    observations: Iterable[dict],
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import observations from an external profile export.

    Returns a summary with inserted/skipped/invalid counts.
    """
    required = set(
        [
            "channel_identity",
            "observed_at",
            "label",
            "source",
            "event_id",
            "detector_state",
            "commercial_reason",
        ]
    )
    rows = list(observations or [])
    identities: list[str] = []
    inserted = 0
    skipped = 0
    invalid = 0

    with closing(_connect(db_path)) as conn:
        if overwrite and rows:
            identities = [normalize_identity(row.get("channel_identity", "")) for row in rows]
            clear_channel_profiles(db_path, identities)
        for row in rows:
            if not isinstance(row, dict):
                invalid += 1
                continue
            if not required.issubset(row.keys()):
                invalid += 1
                continue

            channel_identity = normalize_identity(row.get("channel_identity"))
            if not channel_identity:
                invalid += 1
                continue
            label = _clean_label(row.get("label"))
            if label == "uncertain" and str(row.get("label", "")).strip().lower() not in _VALID_LABELS:
                invalid += 1
                continue

            values = [
                channel_identity,
                str(row.get("observed_at", "") or "").strip(),
                label,
                str(row.get("source", "inferred"))[:40],
                str(row.get("event_id", ""))[:120],
                str(row.get("detector_state", ""))[:40],
                str(row.get("commercial_reason", ""))[:40],
                *[_bounded_feature(row.get(name)) for name in HISTORY_SIGNAL_NAMES],
            ]

            if not values[1]:
                invalid += 1
                continue

            duplicate = conn.execute(
                """
                SELECT 1 FROM commercial_channel_observations
                WHERE channel_identity = ?
                  AND observed_at = ?
                  AND label = ?
                  AND source = ?
                  AND event_id = ?
                  AND detector_state = ?
                  AND commercial_reason = ?
                  AND cut_density = ?
                  AND mean_color_change = ?
                  AND color_volatility = ?
                  AND edge_density = ?
                  AND mean_brightness = ?
                  AND mean_saturation = ?
                  AND program_graphics_confidence = ?
                   AND bug_identity_confidence = ?
                   AND commercial_confidence = ?
                LIMIT 1
                """,
                values,
            ).fetchone()
            if duplicate:
                skipped += 1
                continue

            conn.execute(
                f"""
                INSERT INTO commercial_channel_observations (
                    channel_identity, observed_at, label, source, event_id,
                    detector_state, commercial_reason, {', '.join(HISTORY_SIGNAL_NAMES)}
                ) VALUES (?, ?, ?, ?, ?, ?, ?, {', '.join('?' for _ in HISTORY_SIGNAL_NAMES)})
                """,
                values,
            )
            inserted += 1
        conn.commit()

    maybe_prune(db_path)
    return {"inserted": inserted, "skipped": skipped, "invalid": invalid}


def score_features(channel_profile: dict, features: dict[str, object]) -> dict:
    """Score one frame window against a channel's retained program/ad history."""
    if not channel_profile.get("ready"):
        return {
            "ready": False,
            "score": 0.0,
            "feature_scores": {},
            "weights": {},
        }
    program = dict(channel_profile.get("program") or {})
    commercial = dict(channel_profile.get("commercial") or {})
    feature_scores: dict[str, float] = {}
    weighted_reliability: dict[str, float] = {}
    for name in FEATURE_NAMES:
        current = _bounded_feature(features.get(name, 0))
        program_stats = dict(program.get(name) or {})
        commercial_stats = dict(commercial.get(name) or {})
        program_mean = float(program_stats.get("mean") or 0)
        commercial_mean = float(commercial_stats.get("mean") or 0)
        program_spread = float(program_stats.get("stddev") or 0)
        commercial_spread = float(commercial_stats.get("stddev") or 0)
        program_distance = abs(current - program_mean) / (program_spread + 0.05)
        commercial_distance = abs(current - commercial_mean) / (commercial_spread + 0.05)
        total_distance = program_distance + commercial_distance
        feature_scores[name] = program_distance / total_distance if total_distance else 0.5
        reliability = min(
            1.0,
            abs(commercial_mean - program_mean)
            / (program_spread + commercial_spread + 0.05),
        )
        weighted_reliability[name] = DEFAULT_FEATURE_WEIGHTS[name] * reliability
    total_weight = sum(weighted_reliability.values())
    if total_weight <= 0:
        weights = dict(DEFAULT_FEATURE_WEIGHTS)
    else:
        weights = {
            name: weighted_reliability[name] / total_weight
            for name in FEATURE_NAMES
        }
    score = sum(weights[name] * feature_scores[name] for name in FEATURE_NAMES)
    return {
        "ready": True,
        "score": max(0.0, min(1.0, score)),
        "feature_scores": feature_scores,
        "weights": weights,
    }
