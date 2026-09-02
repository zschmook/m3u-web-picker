#!/usr/bin/env python3
"""Continuously collect, compare, and discard short commercial-learning captures."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import dvr
from database import connect
from commercial_lab_rotation import set_control


ALGORITHM = "chromaprint-ffmpeg-raw-v1"
TITLE_PREFIX = "Commercial Lab · "
DEFAULT_API = "http://127.0.0.1:9999"
DEFAULT_ROOT = Path("/recordings")
DEFAULT_DB = Path("/app/data/m3u_picker.db")
LOCK_PATH = Path("/tmp/m3u-commercial-lab.lock")
EXCLUDED_CHANNEL_TERMS = (
    "hbo", "showtime", "tmc", "the movie channel", "starz",
    "amc",
    "pbs", "public television",
)


def _api(base: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base.rstrip("/") + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def _duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return round(float(result.stdout.strip()), 6)


def _edl_breaks(path: Path, source_duration: float) -> list[dict[str, float]]:
    breaks: list[dict[str, float]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = raw_line.split()
        if len(fields) < 2:
            continue
        try:
            start = max(0.0, float(fields[0]))
            end = min(source_duration, float(fields[1]))
            action = int(float(fields[2])) if len(fields) > 2 else 0
        except ValueError:
            continue
        if action != 0 or end - start < 2.0:
            continue
        breaks.append({
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(end - start, 3),
        })
    return breaks


def _fingerprint(source: Path, start: float, end: float) -> tuple[str, str]:
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(source), "-vn", "-ac", "1", "-ar", "11025",
            "-f", "chromaprint", "-fp_format", "raw", "pipe:1",
        ],
        capture_output=True,
        timeout=max(120, int((end - start) * 3)),
        check=True,
    )
    raw = bytes(result.stdout)
    if not raw:
        raise RuntimeError(f"No fingerprint generated for {start:.3f}-{end:.3f}")
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _words(encoded: str) -> list[int]:
    raw = base64.b64decode(encoded)
    usable = len(raw) - (len(raw) % 4)
    return [item[0] for item in struct.iter_unpack("<I", raw[:usable])]


def fingerprint_similarity(left: str, right: str) -> float:
    """Return the best small-offset Hamming similarity for two audio fingerprints."""
    first = _words(left)
    second = _words(right)
    if len(first) > len(second):
        first, second = second, first
    if len(first) < 16:
        return 0.0
    best = 0.0
    for shift in range(-24, 25):
        first_start = max(0, -shift)
        second_start = max(0, shift)
        overlap = min(len(first) - first_start, len(second) - second_start)
        if overlap < max(16, int(len(first) * 0.72)):
            continue
        distance = sum(
            (first[first_start + index] ^ second[second_start + index]).bit_count()
            for index in range(overlap)
        )
        score = 1.0 - (distance / (32.0 * overlap))
        best = max(best, score)
    return round(best, 6)


def _run_comskip(source: Path) -> tuple[Path, Path]:
    executable = shutil.which("comskip")
    ini = Path(__file__).resolve().parents[1] / "resources" / "comskip.ini"
    if not executable or not ini.is_file():
        raise RuntimeError("Comskip or its configuration is unavailable")
    edl = source.with_suffix(".edl")
    log = source.with_suffix(".comskip.log")
    edl.unlink(missing_ok=True)
    with log.open("wb") as handle:
        result = subprocess.run(
            [executable, f"--ini={ini}", str(source)],
            cwd=source.parent,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=7200,
            check=False,
        )
    log_text = log.read_text(encoding="utf-8", errors="replace")
    no_commercials = "commercials were not found" in log_text.casefold()
    if no_commercials and not edl.is_file():
        edl.touch()
    if (result.returncode != 0 and not no_commercials) or not edl.is_file():
        raise RuntimeError(f"Comskip exited with code {result.returncode}")
    return edl, log


def _safe_source(root: Path, output_name: str) -> Path:
    source = (root.resolve() / str(output_name or "")).resolve()
    source.relative_to(root.resolve())
    if source.suffix.lower() != ".ts" or not source.is_file():
        raise FileNotFoundError(source)
    return source


def _create_h265_comparison(source: Path, edl: Path, source_duration: float) -> dict:
    converted = source.with_name(f".{source.stem}.commercial-lab.mkv")
    log = source.with_name(f".{source.stem}.h265.log")
    converted.unlink(missing_ok=True)
    media_duration, audio_streams = dvr._media_details(source)
    cuts = dvr._validated_commercial_plan(edl, media_duration)
    current = dvr.settings()
    if cuts:
        command = dvr._commercial_transcode_command(
            source,
            converted,
            current,
            duration=media_duration,
            audio_streams=audio_streams,
            cuts=cuts,
        )
    else:
        command = dvr._transcode_command(source, converted, current)
    encoder = "hevc_nvenc" if "hevc_nvenc" in command else "libx265"
    try:
        with log.open("wb") as handle:
            result = subprocess.run(
                command,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=7200,
                check=False,
            )
        if result.returncode != 0 or not dvr._valid_media(converted):
            raise RuntimeError(f"H.265 comparison encode failed with code {result.returncode}")
        converted_duration = _duration(converted)
    except Exception:
        converted.unlink(missing_ok=True)
        raise
    expected_removed = round(sum(stop - start for start, stop in cuts), 3)
    actual_removed = round(max(0.0, source_duration - converted_duration), 3)
    return {
        "path": converted,
        "log_path": log,
        "duration_seconds": converted_duration,
        "encoder": encoder,
        "cuts": cuts,
        "expected_removed_seconds": expected_removed,
        "actual_removed_seconds": actual_removed,
        "removal_delta_seconds": round(actual_removed - expected_removed, 3),
        "source_bytes": source.stat().st_size,
        "converted_bytes": converted.stat().st_size,
    }


def process_recording(db_path: Path, root: Path, recording_id: int, *, delete_source: bool) -> dict:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        recording = conn.execute(
            "SELECT * FROM dvr_recordings WHERE id = ?", (int(recording_id),)
        ).fetchone()
        existing = conn.execute(
            "SELECT * FROM dvr_commercial_lab_runs WHERE recording_id = ?", (int(recording_id),)
        ).fetchone()
    finally:
        conn.close()
    if recording is None:
        raise ValueError(f"DVR recording {recording_id} does not exist")
    if existing is not None:
        return {"recording_id": int(recording_id), "already_processed": True, **dict(existing)}
    source = _safe_source(root, str(recording["output_name"] or ""))
    duration = _duration(source)
    edl, comskip_log = _run_comskip(source)
    breaks = _edl_breaks(edl, duration)

    segments: list[dict] = []
    for index, item in enumerate(breaks, start=1):
        encoded, digest = _fingerprint(source, item["start_seconds"], item["end_seconds"])
        segments.append({
            **item,
            "segment_index": index,
            "fingerprint": encoded,
            "fingerprint_sha256": digest,
        })

    h265 = _create_h265_comparison(source, edl, duration)
    converted = Path(h265["path"])
    conversion_log = Path(h265["log_path"])

    now = datetime.now(timezone.utc).isoformat()
    source_relative = source.relative_to(root.resolve()).as_posix()
    converted_relative = converted.relative_to(root.resolve()).as_posix()
    edl_relative = edl.relative_to(root.resolve()).as_posix()
    comparison_rows: list[dict] = []
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO dvr_commercial_samples (
                recording_id, source_path, converted_path, edl_path, detector,
                source_duration, converted_duration, detected_breaks_json,
                observations_json, review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'comskip', ?, ?, ?, ?, 'automated', ?, ?)
            ON CONFLICT(recording_id) DO UPDATE SET
                source_path = excluded.source_path,
                converted_path = excluded.converted_path,
                edl_path = excluded.edl_path,
                detector = excluded.detector,
                source_duration = excluded.source_duration,
                converted_duration = excluded.converted_duration,
                detected_breaks_json = excluded.detected_breaks_json,
                observations_json = excluded.observations_json,
                review_status = excluded.review_status,
                updated_at = excluded.updated_at
            """,
            (
                int(recording_id), source_relative, converted_relative, edl_relative, duration,
                float(h265["duration_seconds"]),
                json.dumps(breaks, separators=(",", ":")),
                json.dumps([
                    {"kind": "commercial-lab", "automated": True},
                    {
                        "kind": "ts-h265-comparison",
                        "encoder": h265["encoder"],
                        "source_bytes": h265["source_bytes"],
                        "converted_bytes": h265["converted_bytes"],
                        "expected_removed_seconds": h265["expected_removed_seconds"],
                        "actual_removed_seconds": h265["actual_removed_seconds"],
                        "removal_delta_seconds": h265["removal_delta_seconds"],
                    },
                ], separators=(",", ":")),
                now, now,
            ),
        )
        sample_id = int(conn.execute(
            "SELECT id FROM dvr_commercial_samples WHERE recording_id = ?", (int(recording_id),)
        ).fetchone()[0])
        conn.execute("DELETE FROM dvr_commercial_fingerprints WHERE sample_id = ?", (sample_id,))

        new_fingerprints: list[tuple[int, dict]] = []
        for item in segments:
            cursor = conn.execute(
                """
                INSERT INTO dvr_commercial_fingerprints (
                    sample_id, label_kind, segment_index, start_seconds, end_seconds,
                    duration_seconds, algorithm, fingerprint, fingerprint_sha256, created_at
                ) VALUES (?, 'comskip', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_id, item["segment_index"], item["start_seconds"], item["end_seconds"],
                    item["duration_seconds"], ALGORITHM, item["fingerprint"],
                    item["fingerprint_sha256"], now,
                ),
            )
            new_fingerprints.append((int(cursor.lastrowid), item))

        candidates = conn.execute(
            """
            SELECT f.id, f.sample_id, f.duration_seconds, f.fingerprint,
                   r.channel_name, r.title
            FROM dvr_commercial_fingerprints f
            JOIN dvr_commercial_samples s ON s.id = f.sample_id
            JOIN dvr_recordings r ON r.id = s.recording_id
            WHERE f.sample_id != ? AND f.algorithm = ?
            """,
            (sample_id, ALGORITHM),
        ).fetchall()
        for fingerprint_id, item in new_fingerprints:
            duration_value = float(item["duration_seconds"])
            for candidate in candidates:
                candidate_duration = float(candidate["duration_seconds"])
                if candidate_duration < duration_value * 0.60 or candidate_duration > duration_value * 1.40:
                    continue
                similarity = fingerprint_similarity(item["fingerprint"], str(candidate["fingerprint"]))
                if similarity < 0.80:
                    continue
                matched_id = int(candidate["id"])
                first_id, second_id = sorted((fingerprint_id, matched_id))
                if first_id == fingerprint_id:
                    first_sample, second_sample = sample_id, int(candidate["sample_id"])
                else:
                    first_sample, second_sample = int(candidate["sample_id"]), sample_id
                conn.execute(
                    """
                    INSERT OR REPLACE INTO dvr_commercial_comparisons (
                        sample_id, fingerprint_id, matched_sample_id,
                        matched_fingerprint_id, similarity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (first_sample, first_id, second_sample, second_id, similarity, now),
                )
                comparison_rows.append({
                    "fingerprint_id": fingerprint_id,
                    "matched_fingerprint_id": matched_id,
                    "matched_sample_id": int(candidate["sample_id"]),
                    "channel_name": str(candidate["channel_name"] or ""),
                    "title": str(candidate["title"] or ""),
                    "similarity": similarity,
                })

        best_similarity = max((item["similarity"] for item in comparison_rows), default=0.0)
        result = {
            "recording_id": int(recording_id),
            "sample_id": sample_id,
            "channel_name": str(recording["channel_name"] or ""),
            "duration_seconds": duration,
            "detected_breaks": len(breaks),
            "commercial_seconds": round(sum(item["duration_seconds"] for item in breaks), 3),
            "converted_duration_seconds": float(h265["duration_seconds"]),
            "encoder": str(h265["encoder"]),
            "source_bytes": int(h265["source_bytes"]),
            "converted_bytes": int(h265["converted_bytes"]),
            "compression_ratio": round(int(h265["converted_bytes"]) / max(1, int(h265["source_bytes"])), 6),
            "expected_removed_seconds": float(h265["expected_removed_seconds"]),
            "actual_removed_seconds": float(h265["actual_removed_seconds"]),
            "removal_delta_seconds": float(h265["removal_delta_seconds"]),
            "fingerprints_created": len(segments),
            "comparison_count": len(comparison_rows),
            "best_similarity": best_similarity,
            "matches": sorted(comparison_rows, key=lambda item: item["similarity"], reverse=True)[:20],
        }
        conn.execute(
            """
            INSERT INTO dvr_commercial_lab_runs (
                recording_id, sample_id, channel_name, detected_breaks,
                fingerprints_created, comparison_count, best_similarity,
                result_json, source_deleted, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                int(recording_id), sample_id, result["channel_name"], len(breaks),
                len(segments), len(comparison_rows), best_similarity,
                json.dumps(result, separators=(",", ":")), now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        converted.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    deleted = False
    if delete_source:
        deletion_errors: list[str] = []
        for artifact in (source, converted, edl, comskip_log, conversion_log):
            try:
                artifact.unlink(missing_ok=True)
            except OSError as exc:
                deletion_errors.append(f"{artifact.name}: {type(exc).__name__}")
        deleted = not source.exists() and not converted.exists()
        conn = connect(db_path)
        try:
            conn.execute(
                "UPDATE dvr_commercial_lab_runs SET source_deleted = ? WHERE recording_id = ?",
                (1 if deleted else 0, int(recording_id)),
            )
            if deleted:
                conn.execute(
                    """
                    UPDATE dvr_commercial_samples
                    SET source_path = '', converted_path = '', edl_path = '', updated_at = ?
                    WHERE recording_id = ?
                    """,
                    (now, int(recording_id)),
                )
            conn.execute(
                """
                UPDATE dvr_recordings
                SET status = ?, output_name = ?, conversion_status = '',
                    commercial_status = ?, commercial_error = ?, commercial_count = ?,
                    commercial_seconds = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "analyzed" if deleted else "failed",
                    "" if deleted else source_relative,
                    "analyzed" if breaks and deleted else "none" if deleted else "failed",
                    "" if not deletion_errors else "Unable to delete every temporary lab artifact: " + ", ".join(deletion_errors),
                    len(breaks),
                    round(sum(item["duration_seconds"] for item in breaks), 3), now,
                    int(recording_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    result["source_deleted"] = deleted
    result["artifacts_retained"] = [] if deleted else [
        artifact.name for artifact in (source, converted) if artifact.exists()
    ]
    return result


def _recent_channel_ids(db_path: Path, limit: int = 32) -> set[str]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT r.tvg_id FROM dvr_commercial_lab_runs l
            JOIN dvr_recordings r ON r.id = l.recording_id
            ORDER BY l.processed_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}
    finally:
        conn.close()


def schedule_to_capacity(db_path: Path, api_base: str, *, slots: int, minutes: int) -> list[dict]:
    state = _api(api_base, "/api/dvr")
    recordings = list(state.get("recordings") or [])
    active = [
        item for item in recordings
        if str(item.get("title") or "").startswith(TITLE_PREFIX)
        and item.get("status") in {"scheduled", "recording", "processing"}
    ]
    needed = max(0, int(slots) - len(active))
    if needed == 0:
        return []
    guide = _api(api_base, "/api/guide/channels")
    active_ids = {str(item.get("tvg_id") or "") for item in active}
    recent_ids = _recent_channel_ids(db_path)
    candidates = [
        item for item in list(guide.get("channels") or [])
        if str(item.get("play_url") or "").strip()
        and not str(item.get("play_url") or "").startswith("/guide/play/sports/")
        and str(item.get("tvg_id") or "").strip()
        and str(item.get("tvg_id") or "") not in active_ids
        and "4k" not in str(item.get("name") or "").casefold()
        and "news now" not in str(item.get("name") or "").casefold()
        and not any(term in str(item.get("name") or "").casefold() for term in EXCLUDED_CHANNEL_TERMS)
    ]
    fresh = [item for item in candidates if str(item.get("tvg_id") or "") not in recent_ids]
    pool = fresh if len(fresh) >= needed else candidates
    random.SystemRandom().shuffle(pool)
    selected = pool[:needed]
    if len(selected) < needed:
        raise RuntimeError(f"Only {len(selected)} distinct channels are available for {needed} open slots")

    previous_padding = int((state.get("settings") or {}).get("padding_after_seconds") or 0)
    _api(api_base, "/api/dvr/settings", "PATCH", {
        "max_concurrent_recordings": max(int(slots), int((state.get("settings") or {}).get("max_concurrent_recordings") or 1)),
        "padding_after_seconds": 0,
    })
    created: list[dict] = []
    try:
        start = datetime.now(timezone.utc)
        stop = start + timedelta(minutes=int(minutes))
        stamp = start.strftime("%Y-%m-%d %H:%M UTC")
        for channel in selected:
            response = _api(api_base, "/api/dvr/recordings", "POST", {
                "play_url": channel["play_url"],
                "tvg_id": channel["tvg_id"],
                "title": f"{TITLE_PREFIX}{channel.get('name') or channel['tvg_id']} · {stamp}",
                "subtitle": "Automated commercial fingerprint sample",
                "description": "Twenty-minute rotating commercial-learning capture.",
                "start": start.isoformat(),
                "stop": stop.isoformat(),
            })
            created.append(response["recording"])
    finally:
        _api(api_base, "/api/dvr/settings", "PATCH", {"padding_after_seconds": previous_padding})
    return created


def cycle(db_path: Path, root: Path, api_base: str, *, slots: int, minutes: int) -> dict:
    set_control(db_path, enabled=True, slots=slots, sample_minutes=minutes)
    scheduled: list[dict] = []
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT r.id FROM dvr_recordings r
            LEFT JOIN dvr_commercial_lab_runs l ON l.recording_id = r.id
            WHERE r.status = 'completed' AND lower(r.output_name) LIKE '%.ts'
              AND r.title LIKE ? AND l.id IS NULL
            ORDER BY r.completed_at ASC
            """,
            (f"{TITLE_PREFIX}%",),
        ).fetchall()
    finally:
        conn.close()
    processed: list[dict] = []
    errors: list[dict] = []
    for row in rows:
        recording_id = int(row[0])
        try:
            processed.append(process_recording(db_path, root, recording_id, delete_source=True))
        except Exception as exc:
            errors.append({"recording_id": recording_id, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "scheduled": [{"id": item["id"], "channel": item["channel_name"]} for item in scheduled],
        "processed": processed,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("cycle", "process", "stop"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--recordings-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--recording-id", type=int)
    parser.add_argument("--keep-source", action="store_true")
    args = parser.parse_args()

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"skipped": "commercial lab cycle already running"}, sort_keys=True))
            return
        if args.command == "stop":
            result = set_control(
                args.db,
                enabled=False,
                slots=max(1, min(4, args.slots)),
                sample_minutes=max(5, min(120, args.minutes)),
            )
        elif args.command == "process":
            if args.recording_id is None:
                parser.error("process requires --recording-id")
            result = process_recording(
                args.db, args.recordings_root, args.recording_id,
                delete_source=not args.keep_source,
            )
        else:
            result = cycle(
                args.db, args.recordings_root, args.api,
                slots=max(1, min(4, args.slots)),
                minutes=max(5, min(120, args.minutes)),
            )
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
