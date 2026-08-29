from __future__ import annotations

import sqlite3
import struct
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageStat


SIGNATURE_VERSION = 2
SAMPLE_INTERVAL_SECONDS = 0.5
SHINGLE_SECONDS = 3
SHINGLE_POINTS = int(SHINGLE_SECONDS / SAMPLE_INTERVAL_SECONDS)
SHINGLE_STRIDE_SECONDS = 1
SHINGLE_STRIDE_POINTS = int(SHINGLE_STRIDE_SECONDS / SAMPLE_INTERVAL_SECONDS)
LIVE_ANCHOR_GAP_SECONDS = 2
LIVE_ANCHOR_GAP_POINTS = int(LIVE_ANCHOR_GAP_SECONDS / SAMPLE_INTERVAL_SECONDS)
LIVE_MATCH_POINTS = SHINGLE_POINTS + LIVE_ANCHOR_GAP_POINTS
TILE_COLUMNS = 4
TILE_ROWS = 3
TILE_COUNT = TILE_COLUMNS * TILE_ROWS
MATCHED_TILE_COUNT = 8
PROMOTION_OCCURRENCES = 3
PROBABLE_OCCURRENCES = 2
CANDIDATE_RETENTION_DAYS = 3
PROBABLE_RETENTION_DAYS = 14
CLASSIFIED_RETENTION_DAYS = 60
MAX_CANDIDATES = 5_000
MAX_PROBABLE = 5_000
MAX_CLASSIFIED = 10_000
FULL_SEQUENCE_MATCH_THRESHOLD = 0.90
LIVE_SEQUENCE_MATCH_THRESHOLD = 0.92
FULL_SEQUENCE_MAX_SHIFT_POINTS = 2
LIVE_COARSE_MATCH_THRESHOLD = 0.58

# center dHash, twelve tile dHashes, twelve RGB means flattened to 36 bytes
Fingerprint = tuple[int, tuple[int, ...], tuple[int, ...]]

_FRAME_FORMAT = f">Q{TILE_COUNT}Q{TILE_COUNT * 3}B"
_FRAME_SIZE = struct.calcsize(_FRAME_FORMAT)
_CACHE_LOCK = threading.RLock()
_KNOWN_CACHE: dict[tuple[str, str], list[dict]] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _db_key(db_path: Path | str) -> str:
    return str(Path(db_path).resolve())


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
        CREATE TABLE IF NOT EXISTS commercial_ad_signatures_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_identity TEXT NOT NULL DEFAULT '',
            duration_seconds INTEGER NOT NULL DEFAULT 3,
            fingerprints BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            user_confirmations INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_event_id TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_ad_signature_events_v2 (
            signature_id INTEGER NOT NULL,
            channel_identity TEXT NOT NULL,
            event_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            user_confirmed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (signature_id, event_id),
            FOREIGN KEY (signature_id) REFERENCES commercial_ad_signatures_v2(id)
                ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_ad_signatures_v2_channel "
        "ON commercial_ad_signatures_v2(channel_identity, status, last_seen)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_commercial_ad_signature_events_v2_channel "
        "ON commercial_ad_signature_events_v2(channel_identity, observed_at)"
    )
    conn.commit()


def ensure_schema(db_path: Path | str) -> None:
    with closing(_connect(db_path)):
        pass


def invalidate_cache(db_path: Path | str) -> None:
    key = _db_key(db_path)
    with _CACHE_LOCK:
        for cached_key in tuple(_KNOWN_CACHE):
            if cached_key[0] == key:
                _KNOWN_CACHE.pop(cached_key, None)


def _dhash(image: Image.Image) -> int:
    gray = image.convert("L").resize((9, 8))
    pixels = list(gray.getdata())
    value = 0
    for y in range(8):
        row = pixels[y * 9:(y + 1) * 9]
        for x in range(8):
            value = (value << 1) | int(row[x] > row[x + 1])
    return value


def fingerprint_image(
    image: Image.Image,
    color_histogram: Iterable[float] = (),
) -> Fingerprint:
    """Create an overlay-tolerant tiled fingerprint for one analysis frame."""
    del color_histogram  # Kept in the public signature for detector compatibility.
    frame = image.convert("RGB").resize((320, 180))
    center_hash = _dhash(frame.crop((40, 22, 280, 158)))
    tile_hashes: list[int] = []
    tile_colors: list[int] = []
    tile_width = frame.width // TILE_COLUMNS
    tile_height = frame.height // TILE_ROWS
    for row in range(TILE_ROWS):
        for column in range(TILE_COLUMNS):
            left = column * tile_width
            top = row * tile_height
            right = frame.width if column == TILE_COLUMNS - 1 else left + tile_width
            bottom = frame.height if row == TILE_ROWS - 1 else top + tile_height
            tile = frame.crop((left, top, right, bottom))
            tile_hashes.append(_dhash(tile))
            means = ImageStat.Stat(tile).mean[:3]
            tile_colors.extend(max(0, min(255, round(value))) for value in means)
    return center_hash, tuple(tile_hashes), tuple(tile_colors)


def _pack(points: Iterable[Fingerprint]) -> bytes:
    samples = list(points)
    if len(samples) > 255:
        samples = samples[-255:]
    payload = bytearray(struct.pack(">BB", SIGNATURE_VERSION, len(samples)))
    for center_hash, tile_hashes, tile_colors in samples:
        hashes = tuple(int(value) for value in tile_hashes[:TILE_COUNT])
        hashes += tuple([0] * (TILE_COUNT - len(hashes)))
        colors = tuple(
            max(0, min(255, int(value)))
            for value in tile_colors[:TILE_COUNT * 3]
        )
        colors += tuple([0] * ((TILE_COUNT * 3) - len(colors)))
        payload.extend(struct.pack(_FRAME_FORMAT, int(center_hash), *hashes, *colors))
    return bytes(payload)


def _unpack(payload: bytes) -> list[Fingerprint]:
    if len(payload) < 2:
        return []
    version, count = struct.unpack(">BB", payload[:2])
    if version != SIGNATURE_VERSION or len(payload) != 2 + (count * _FRAME_SIZE):
        return []
    result: list[Fingerprint] = []
    offset = 2
    for _index in range(count):
        values = struct.unpack(_FRAME_FORMAT, payload[offset:offset + _FRAME_SIZE])
        result.append((
            int(values[0]),
            tuple(int(value) for value in values[1:1 + TILE_COUNT]),
            tuple(int(value) for value in values[1 + TILE_COUNT:]),
        ))
        offset += _FRAME_SIZE
    return result


def _aggregate_histogram(points: Iterable[Fingerprint]) -> bytes:
    samples = list(points)
    if not samples:
        return bytes([0] * (TILE_COUNT * 3))
    return bytes(
        max(
            0,
            min(255, round(sum(point[2][index] for point in samples) / len(samples))),
        )
        for index in range(TILE_COUNT * 3)
    )


def _hash_similarity(left: int, right: int) -> float:
    return 1.0 - ((int(left) ^ int(right)).bit_count() / 64.0)


def _tile_color_similarity(
    left: tuple[int, ...], right: tuple[int, ...], index: int
) -> float:
    offset = index * 3
    distance = sum(
        abs(int(left[offset + channel]) - int(right[offset + channel]))
        for channel in range(3)
    ) / (3.0 * 255.0)
    return max(0.0, 1.0 - distance)


def point_similarity(left: Fingerprint, right: Fingerprint) -> float:
    center_similarity = _hash_similarity(left[0], right[0])
    tile_scores = []
    for index in range(TILE_COUNT):
        tile_scores.append(
            (0.82 * _hash_similarity(left[1][index], right[1][index]))
            + (0.18 * _tile_color_similarity(left[2], right[2], index))
        )
    tile_scores.sort(reverse=True)
    robust_tile_similarity = sum(tile_scores[:MATCHED_TILE_COUNT]) / MATCHED_TILE_COUNT
    return max(
        0.0,
        min(1.0, (0.78 * robust_tile_similarity) + (0.22 * center_similarity)),
    )


def _motion_profile(points: list[Fingerprint]) -> list[float]:
    result = []
    for left, right in zip(points, points[1:]):
        result.append(
            sum(
                1.0 - _hash_similarity(a, b)
                for a, b in zip(left[1], right[1])
            ) / TILE_COUNT
        )
    return result


def sequence_similarity(left: Iterable[Fingerprint], right: Iterable[Fingerprint]) -> float:
    left_points, right_points = list(left), list(right)
    if not left_points or len(left_points) != len(right_points):
        return 0.0
    visual = sum(
        point_similarity(left_point, right_point)
        for left_point, right_point in zip(left_points, right_points)
    ) / len(left_points)
    left_motion = _motion_profile(left_points)
    right_motion = _motion_profile(right_points)
    if left_motion:
        motion = 1.0 - min(
            1.0,
            sum(abs(a - b) for a, b in zip(left_motion, right_motion))
            / (len(left_motion) * 0.50),
        )
    else:
        motion = 1.0
    return max(0.0, min(1.0, (0.86 * visual) + (0.14 * motion)))


def shifted_sequence_similarity(
    left: Iterable[Fingerprint],
    right: Iterable[Fingerprint],
    *,
    max_shift: int = FULL_SEQUENCE_MAX_SHIFT_POINTS,
) -> float:
    left_points, right_points = list(left), list(right)
    if not left_points or len(left_points) != len(right_points):
        return 0.0
    minimum_overlap = max(4, len(left_points) - max(0, int(max_shift)))
    best = 0.0
    for shift in range(-max(0, int(max_shift)), max(0, int(max_shift)) + 1):
        if shift < 0:
            aligned_left = left_points[:shift]
            aligned_right = right_points[-shift:]
        elif shift > 0:
            aligned_left = left_points[shift:]
            aligned_right = right_points[:-shift]
        else:
            aligned_left = left_points
            aligned_right = right_points
        if len(aligned_left) >= minimum_overlap:
            best = max(best, sequence_similarity(aligned_left, aligned_right))
    return best


def _coarse_center_similarity(
    left: Iterable[Fingerprint],
    right: Iterable[Fingerprint],
    *,
    max_shift: int = FULL_SEQUENCE_MAX_SHIFT_POINTS,
) -> float:
    """Cheaply reject unrelated clips before comparing every screen tile."""
    left_points, right_points = list(left), list(right)
    if not left_points or len(left_points) != len(right_points):
        return 0.0
    minimum_overlap = max(4, len(left_points) - max(0, int(max_shift)))
    best = 0.0
    for shift in range(-max(0, int(max_shift)), max(0, int(max_shift)) + 1):
        if shift < 0:
            aligned_left = left_points[:shift]
            aligned_right = right_points[-shift:]
        elif shift > 0:
            aligned_left = left_points[shift:]
            aligned_right = right_points[:-shift]
        else:
            aligned_left = left_points
            aligned_right = right_points
        if len(aligned_left) < minimum_overlap:
            continue
        best = max(
            best,
            sum(
                _hash_similarity(left_point[0], right_point[0])
                for left_point, right_point in zip(aligned_left, aligned_right)
            ) / len(aligned_left),
        )
    return best


def _episode_windows(points: Iterable[Fingerprint]) -> list[list[Fingerprint]]:
    samples = list(points)
    if len(samples) < SHINGLE_POINTS:
        return []
    return [
        samples[start:start + SHINGLE_POINTS]
        for start in range(
            0,
            len(samples) - SHINGLE_POINTS + 1,
            SHINGLE_STRIDE_POINTS,
        )
    ]


def _best_match(
    rows: Iterable[sqlite3.Row | dict], points: list[Fingerprint]
) -> tuple[sqlite3.Row | dict | None, float]:
    best_row, best_score = None, 0.0
    for row in rows:
        candidate = _unpack(bytes(row["fingerprints"]))
        score = shifted_sequence_similarity(points, candidate)
        if score > best_score:
            best_row, best_score = row, score
    return best_row, best_score


def _status_for_count(count: int) -> str:
    if count >= PROMOTION_OCCURRENCES:
        return "classified"
    if count >= PROBABLE_OCCURRENCES:
        return "probable"
    return "candidate"


def _confidence_for_count(count: int) -> float:
    return min(1.0, max(0, count) / float(PROMOTION_OCCURRENCES))


def _record_event(
    conn: sqlite3.Connection,
    signature_id: int,
    channel_identity: str,
    event_id: str,
    timestamp: str,
    *,
    user_confirmed: bool,
) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO commercial_ad_signature_events_v2 (
            signature_id, channel_identity, event_id, observed_at, user_confirmed
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            signature_id,
            channel_identity[:240],
            event_id[:120],
            timestamp,
            int(user_confirmed),
        ),
    )
    return bool(cursor.rowcount)


def record_episode(
    db_path: Path | str,
    channel_identity: str,
    event_id: str,
    points: Iterable[Fingerprint],
    *,
    user_confirmed: bool = False,
    trigger_reason: str = "",
    observed_at: datetime | None = None,
) -> dict:
    """Cluster sliding three-second shingles across independent break events."""
    episode_points = list(points)
    clean_event_id = str(event_id or "")[:120]
    if clean_event_id.startswith("clock-") or str(trigger_reason or "") == "countdown-clock":
        return {
            "windows": 0,
            "inserted": 0,
            "matched": 0,
            "promoted": 0,
            "skipped": "countdown-clock",
            "signature_ids": [],
        }
    windows = _episode_windows(episode_points)
    timestamp = (observed_at or _utc_now()).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    promoted: set[int] = set()
    matched: set[int] = set()
    inserted: set[int] = set()
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM commercial_ad_signatures_v2
            WHERE channel_identity = ?
            ORDER BY status DESC, occurrence_count DESC, last_seen DESC
            """,
            (channel_identity[:240],),
        ).fetchall()
        mutable_rows: list[sqlite3.Row | dict] = list(rows)
        for window in windows:
            row, score = _best_match(mutable_rows, window)
            if row is not None and score >= FULL_SEQUENCE_MATCH_THRESHOLD:
                signature_id = int(row["id"])
                matched.add(signature_id)
                new_event = _record_event(
                    conn,
                    signature_id,
                    channel_identity,
                    clean_event_id,
                    timestamp,
                    user_confirmed=user_confirmed,
                )
                old_count = int(row["occurrence_count"] or 0)
                occurrence_count = old_count + (1 if new_event else 0)
                if user_confirmed:
                    occurrence_count = max(PROMOTION_OCCURRENCES, occurrence_count)
                old_status = str(row["status"] or "candidate")
                status = _status_for_count(occurrence_count)
                conn.execute(
                    """
                    UPDATE commercial_ad_signatures_v2
                    SET occurrence_count = ?, user_confirmations = user_confirmations + ?,
                        confidence = ?, status = ?, last_seen = ?, last_event_id = ?
                    WHERE id = ?
                    """,
                    (
                        occurrence_count,
                        int(user_confirmed and new_event),
                        _confidence_for_count(occurrence_count),
                        status,
                        timestamp,
                        clean_event_id,
                        signature_id,
                    ),
                )
                if status == "classified" and old_status != "classified":
                    promoted.add(signature_id)
                mutable_rows = [
                    (
                        dict(existing)
                        | {
                            "occurrence_count": occurrence_count,
                            "status": status,
                            "last_event_id": clean_event_id,
                        }
                    )
                    if int(existing["id"]) == signature_id
                    else existing
                    for existing in mutable_rows
                ]
                continue

            occurrence_count = PROMOTION_OCCURRENCES if user_confirmed else 1
            status = _status_for_count(occurrence_count)
            packed = _pack(window)
            cursor = conn.execute(
                """
                INSERT INTO commercial_ad_signatures_v2 (
                    channel_identity, duration_seconds, fingerprints, status,
                    occurrence_count, user_confirmations, confidence,
                    first_seen, last_seen, last_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_identity[:240],
                    SHINGLE_SECONDS,
                    packed,
                    status,
                    occurrence_count,
                    int(user_confirmed),
                    _confidence_for_count(occurrence_count),
                    timestamp,
                    timestamp,
                    clean_event_id,
                ),
            )
            signature_id = int(cursor.lastrowid)
            _record_event(
                conn,
                signature_id,
                channel_identity,
                clean_event_id,
                timestamp,
                user_confirmed=user_confirmed,
            )
            inserted.add(signature_id)
            if status == "classified":
                promoted.add(signature_id)
            mutable_rows.append({
                "id": signature_id,
                "fingerprints": packed,
                "occurrence_count": occurrence_count,
                "status": status,
                "last_event_id": clean_event_id,
            })
        conn.commit()
    prune(db_path, now=observed_at)
    invalidate_cache(db_path)
    return {
        "windows": len(windows),
        "inserted": len(inserted),
        "matched": len(matched),
        "promoted": len(promoted),
        "signature_ids": sorted(inserted | matched),
    }


def _load_recognized(db_path: Path | str, channel_identity: str) -> list[dict]:
    key = (_db_key(db_path), channel_identity[:240])
    with _CACHE_LOCK:
        cached = _KNOWN_CACHE.get(key)
        if cached is not None:
            return cached
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, fingerprints, status, occurrence_count,
                   user_confirmations, confidence
            FROM commercial_ad_signatures_v2
            WHERE channel_identity = ? AND status IN ('probable', 'classified')
            ORDER BY occurrence_count DESC, last_seen DESC
            LIMIT ?
            """,
            (channel_identity[:240], MAX_PROBABLE + MAX_CLASSIFIED),
        ).fetchall()
    loaded = [
        {
            "id": int(row["id"]),
            "points": _unpack(bytes(row["fingerprints"])),
            "status": str(row["status"]),
            "occurrence_count": int(row["occurrence_count"] or 0),
            "user_confirmations": int(row["user_confirmations"] or 0),
            "confidence": float(row["confidence"] or 0),
        }
        for row in rows
    ]
    with _CACHE_LOCK:
        _KNOWN_CACHE[key] = loaded
    return loaded


def _best_live_anchor(
    signatures: list[dict], window: list[Fingerprint]
) -> tuple[dict | None, float]:
    best_signature, best_score = None, 0.0
    for signature in signatures:
        points = list(signature["points"])
        if len(points) != len(window):
            continue
        if _coarse_center_similarity(window, points) < LIVE_COARSE_MATCH_THRESHOLD:
            continue
        score = shifted_sequence_similarity(
            window,
            points,
            max_shift=FULL_SEQUENCE_MAX_SHIFT_POINTS,
        )
        if score > best_score:
            best_signature, best_score = signature, score
    return best_signature, best_score


def match_live(
    db_path: Path | str,
    channel_identity: str,
    history: Iterable[Fingerprint],
) -> dict:
    """Require two ordered three-second anchors spanning about five seconds."""
    recent = list(history)[-LIVE_MATCH_POINTS:]
    if len(recent) < LIVE_MATCH_POINTS:
        return {"matched": False, "score": 0.0}
    signatures = _load_recognized(db_path, channel_identity)
    if not signatures:
        return {"matched": False, "score": 0.0}
    first_window = recent[:SHINGLE_POINTS]
    second_window = recent[-SHINGLE_POINTS:]
    first_signature, first_score = _best_live_anchor(signatures, first_window)
    second_signature, second_score = _best_live_anchor(signatures, second_window)
    score = min(first_score, second_score)
    if (
        first_signature is None
        or second_signature is None
        or score < LIVE_SEQUENCE_MATCH_THRESHOLD
    ):
        return {"matched": False, "score": max(first_score, second_score)}
    occurrences = min(
        int(first_signature["occurrence_count"]),
        int(second_signature["occurrence_count"]),
    )
    user_confirmed = bool(
        first_signature["user_confirmations"]
        or second_signature["user_confirmations"]
    )
    authority = min(
        1.0,
        (0.68 if occurrences == PROBABLE_OCCURRENCES else 0.78)
        + (0.05 * max(0, occurrences - PROMOTION_OCCURRENCES))
        + (0.08 if user_confirmed else 0.0),
    )
    return {
        "matched": True,
        "score": score,
        "signature_id": int(second_signature["id"]),
        "signature_ids": [
            int(first_signature["id"]),
            int(second_signature["id"]),
        ],
        "duration_seconds": SHINGLE_SECONDS,
        "offset_seconds": 0,
        "seconds_remaining": 2,
        "occurrence_count": occurrences,
        "authority": authority,
        "confidence": score * authority,
    }


def mark_false_positive(db_path: Path | str, signature_id: int) -> bool:
    if int(signature_id or 0) <= 0:
        return False
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT occurrence_count FROM commercial_ad_signatures_v2 WHERE id = ?",
            (int(signature_id),),
        ).fetchone()
        if row is None:
            return False
        count = max(0, int(row["occurrence_count"] or 0) - 2)
        if count == 0:
            conn.execute(
                "DELETE FROM commercial_ad_signatures_v2 WHERE id = ?",
                (int(signature_id),),
            )
        else:
            conn.execute(
                """
                UPDATE commercial_ad_signatures_v2
                SET occurrence_count = ?, confidence = ?, status = ?
                WHERE id = ?
                """,
                (
                    count,
                    _confidence_for_count(count),
                    _status_for_count(count),
                    int(signature_id),
                ),
            )
        conn.commit()
    invalidate_cache(db_path)
    return True


def mark_false_positives(
    db_path: Path | str,
    signature_ids: Iterable[int],
) -> int:
    """Demote every anchor that jointly caused a known-ad transition."""
    changed = 0
    for signature_id in sorted({int(value or 0) for value in signature_ids}):
        if signature_id > 0 and mark_false_positive(db_path, signature_id):
            changed += 1
    return changed


def library_stats(db_path: Path | str, channel_identity: str = "") -> dict:
    with closing(_connect(db_path)) as conn:
        where = "WHERE channel_identity = ?" if channel_identity else ""
        params = (channel_identity[:240],) if channel_identity else ()
        row = conn.execute(
            f"""
            SELECT
                SUM(status = 'classified') AS classified,
                SUM(status = 'probable') AS probable,
                SUM(status = 'candidate') AS candidates,
                COALESCE(SUM(occurrence_count), 0) AS occurrences,
                COALESCE(SUM(LENGTH(fingerprints)), 0) AS bytes
            FROM commercial_ad_signatures_v2
            {where}
            """,
            params,
        ).fetchone()
    return {
        "classified": int(row["classified"] or 0),
        "probable": int(row["probable"] or 0),
        "candidates": int(row["candidates"] or 0),
        "occurrences": int(row["occurrences"] or 0),
        "storage_bytes": int(row["bytes"] or 0),
        "version": SIGNATURE_VERSION,
    }


def prune(db_path: Path | str, *, now: datetime | None = None) -> int:
    current = (now or _utc_now()).astimezone(timezone.utc)
    cutoffs = {
        "candidate": (
            current - timedelta(days=CANDIDATE_RETENTION_DAYS)
        ).isoformat(timespec="seconds"),
        "probable": (
            current - timedelta(days=PROBABLE_RETENTION_DAYS)
        ).isoformat(timespec="seconds"),
        "classified": (
            current - timedelta(days=CLASSIFIED_RETENTION_DAYS)
        ).isoformat(timespec="seconds"),
    }
    limits = {
        "candidate": MAX_CANDIDATES,
        "probable": MAX_PROBABLE,
        "classified": MAX_CLASSIFIED,
    }
    removed = 0
    with closing(_connect(db_path)) as conn:
        for status, cutoff in cutoffs.items():
            removed += int(
                conn.execute(
                    "DELETE FROM commercial_ad_signatures_v2 "
                    "WHERE status = ? AND last_seen < ?",
                    (status, cutoff),
                ).rowcount
                or 0
            )
        for status, limit in limits.items():
            excess = int(
                conn.execute(
                    "SELECT COUNT(*) FROM commercial_ad_signatures_v2 WHERE status = ?",
                    (status,),
                ).fetchone()[0]
                or 0
            ) - limit
            if excess > 0:
                removed += int(
                    conn.execute(
                        """
                        DELETE FROM commercial_ad_signatures_v2
                        WHERE id IN (
                            SELECT id FROM commercial_ad_signatures_v2
                            WHERE status = ?
                            ORDER BY confidence ASC, occurrence_count ASC, last_seen ASC
                            LIMIT ?
                        )
                        """,
                        (status, excess),
                    ).rowcount
                    or 0
                )
        conn.commit()
    if removed:
        invalidate_cache(db_path)
    return removed
