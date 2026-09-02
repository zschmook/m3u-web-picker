#!/usr/bin/env python3
"""Store one reviewed DVR recording and Chromaprint commercial fingerprints."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from database import connect


ALGORITHM = "chromaprint-ffmpeg-raw-v1"


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / str(relative or "")).resolve()
    candidate.relative_to(root)
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return round(float(result.stdout.strip()), 6)


def _edl_breaks(path: Path, source_duration: float) -> list[dict[str, float]]:
    breaks: list[dict[str, float]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = raw_line.split()
        if len(fields) < 2:
            continue
        start = max(0.0, float(fields[0]))
        end = min(source_duration, float(fields[1]))
        action = int(float(fields[2])) if len(fields) > 2 else 0
        if action != 0 or end <= start:
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
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(source), "-vn", "-ac", "1", "-ar", "11025",
            "-f", "chromaprint", "-fp_format", "raw", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    raw = bytes(result.stdout)
    if not raw:
        raise RuntimeError(f"No audio fingerprint was generated for {start:.3f}-{end:.3f}")
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def collect(db_path: Path, manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = Path(manifest["recordings_root"]).resolve()
    source_relative = str(manifest["source_path"])
    converted_relative = str(manifest["converted_path"])
    edl_relative = str(manifest["edl_path"])
    source = _safe_path(root, source_relative)
    converted = _safe_path(root, converted_relative)
    edl = _safe_path(root, edl_relative)
    source_duration = _duration(source)
    converted_duration = _duration(converted)
    detected = _edl_breaks(edl, source_duration)
    observations = list(manifest.get("observations") or [])

    segments: list[dict[str, object]] = []
    for index, item in enumerate(detected, start=1):
        segments.append({"label_kind": "comskip", "segment_index": index, **item})
    for index, item in enumerate(manifest.get("fingerprint_segments") or [], start=1):
        start = float(item["start_seconds"])
        end = float(item["end_seconds"])
        if start < 0 or end <= start or end > source_duration:
            raise ValueError(f"Invalid manual fingerprint interval {start}-{end}")
        segments.append({
            "label_kind": str(item.get("label_kind") or "manual"),
            "segment_index": index,
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": round(end - start, 3),
        })

    for item in segments:
        value, digest = _fingerprint(
            source,
            float(item["start_seconds"]),
            float(item["end_seconds"]),
        )
        item["fingerprint"] = value
        item["fingerprint_sha256"] = digest

    now = datetime.now(timezone.utc).isoformat()
    recording_id = int(manifest["recording_id"])
    conn = connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM dvr_recordings WHERE id = ?", (recording_id,)).fetchone() is None:
            raise ValueError(f"DVR recording {recording_id} does not exist")
        conn.execute(
            """
            INSERT INTO dvr_commercial_samples (
                recording_id, source_path, converted_path, edl_path, detector,
                source_duration, converted_duration, detected_breaks_json,
                observations_json, review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                recording_id, source_relative, converted_relative, edl_relative,
                str(manifest.get("detector") or "comskip"), source_duration,
                converted_duration, json.dumps(detected, separators=(",", ":")),
                json.dumps(observations, separators=(",", ":")),
                str(manifest.get("review_status") or "reviewing"), now, now,
            ),
        )
        sample_id = int(conn.execute(
            "SELECT id FROM dvr_commercial_samples WHERE recording_id = ?",
            (recording_id,),
        ).fetchone()[0])
        conn.execute("DELETE FROM dvr_commercial_fingerprints WHERE sample_id = ?", (sample_id,))
        for item in segments:
            conn.execute(
                """
                INSERT INTO dvr_commercial_fingerprints (
                    sample_id, label_kind, segment_index, start_seconds, end_seconds,
                    duration_seconds, algorithm, fingerprint, fingerprint_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_id, item["label_kind"], item["segment_index"],
                    item["start_seconds"], item["end_seconds"], item["duration_seconds"],
                    ALGORITHM, item["fingerprint"], item["fingerprint_sha256"], now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "sample_id": sample_id,
        "recording_id": recording_id,
        "detected_breaks": len(detected),
        "fingerprints": len(segments),
        "source_duration": source_duration,
        "converted_duration": converted_duration,
        "review_status": str(manifest.get("review_status") or "reviewing"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(collect(args.db, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()
