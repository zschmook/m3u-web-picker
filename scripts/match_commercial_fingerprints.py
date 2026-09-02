#!/usr/bin/env python3
"""Match stored commercial Chromaprint sequences against a DVR transport stream."""

from __future__ import annotations

import argparse
import base64
import json
import struct
import subprocess
from pathlib import Path

from database import connect


ALGORITHM = "chromaprint-ffmpeg-raw-v1"


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
    return float(result.stdout.strip())


def _raw_fingerprint(path: Path) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", "11025", "-f", "chromaprint",
            "-fp_format", "raw", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    if not result.stdout:
        raise RuntimeError("The target recording did not produce an audio fingerprint")
    return bytes(result.stdout)


def _words(raw: bytes) -> list[int]:
    usable = len(raw) - (len(raw) % 4)
    return [value[0] for value in struct.iter_unpack("<I", raw[:usable])]


def _sample_score(full: list[int], needle: list[int], offset: int, positions: list[int]) -> float:
    distance = sum((needle[index] ^ full[offset + index]).bit_count() for index in positions)
    return 1.0 - (distance / (32.0 * len(positions)))


def _full_score(full: list[int], needle: list[int], offset: int) -> float:
    distance = sum((expected ^ full[offset + index]).bit_count() for index, expected in enumerate(needle))
    return 1.0 - (distance / (32.0 * len(needle)))


def _find_matches(
    full: list[int],
    needle: list[int],
    hashes_per_second: float,
) -> list[tuple[int, float]]:
    if not needle or len(needle) > len(full):
        return []
    edge = min(16, max(0, len(needle) // 12))
    core = needle[edge:len(needle) - edge] if len(needle) > edge * 2 else needle
    core_shift = edge if core is not needle else 0
    sample_count = min(20, len(core))
    positions = sorted({round(index * (len(core) - 1) / max(1, sample_count - 1)) for index in range(sample_count)})
    candidates: list[tuple[float, int]] = []
    limit = len(full) - len(core)
    for offset in range(limit + 1):
        score = _sample_score(full, core, offset, positions)
        candidates.append((score, offset))
    candidates.sort(reverse=True)

    selected: list[tuple[int, float]] = []
    minimum_separation = max(8, round(hashes_per_second * 5.0))
    evaluated = 0
    for sample_score, offset in candidates:
        if sample_score < 0.62 or evaluated >= 200:
            break
        if any(abs(offset - existing) < minimum_separation for existing, _score in selected):
            continue
        evaluated += 1
        best_offset = offset
        best_score = 0.0
        for nearby in range(max(0, offset - 3), min(limit, offset + 3) + 1):
            score = _full_score(full, core, nearby)
            if score > best_score:
                best_score = score
                best_offset = nearby
        if best_score >= 0.70:
            selected.append((max(0, best_offset - core_shift), round(best_score, 6)))
        if len(selected) >= 12:
            break
    return sorted(selected)


def match(db_path: Path, recordings_root: Path, sample_id: int) -> dict[str, object]:
    conn = connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    try:
        sample = conn.execute(
            "SELECT * FROM dvr_commercial_samples WHERE id = ?",
            (int(sample_id),),
        ).fetchone()
        if sample is None:
            raise ValueError(f"Commercial sample {sample_id} does not exist")
        rows = conn.execute(
            """
            SELECT * FROM dvr_commercial_fingerprints
            WHERE sample_id = ? AND algorithm = ?
            ORDER BY label_kind, segment_index
            """,
            (int(sample_id), ALGORITHM),
        ).fetchall()
    finally:
        conn.close()

    target = (recordings_root.resolve() / str(sample["source_path"])).resolve()
    target.relative_to(recordings_root.resolve())
    duration = _duration(target)
    full = _words(_raw_fingerprint(target))
    hashes_per_second = len(full) / duration
    matches: list[dict[str, object]] = []
    for row in rows:
        needle = _words(base64.b64decode(str(row["fingerprint"])))
        expected = float(row["start_seconds"])
        found = _find_matches(full, needle, hashes_per_second)
        occurrences = [
            {
                "start_seconds": round(offset / hashes_per_second, 3),
                "similarity": score,
            }
            for offset, score in found
        ]
        nearest = min(
            occurrences,
            key=lambda item: abs(float(item["start_seconds"]) - expected),
            default={"start_seconds": 0.0, "similarity": 0.0},
        )
        matches.append({
            "label_kind": str(row["label_kind"]),
            "segment_index": int(row["segment_index"]),
            "expected_start_seconds": round(expected, 3),
            "matched_start_seconds": nearest["start_seconds"],
            "delta_seconds": round(float(nearest["start_seconds"]) - expected, 3),
            "duration_seconds": round(float(row["duration_seconds"]), 3),
            "similarity": nearest["similarity"],
            "matched": bool(occurrences),
            "occurrences": occurrences,
        })
    return {
        "sample_id": int(sample_id),
        "target": str(sample["source_path"]),
        "duration_seconds": round(duration, 3),
        "fingerprints_checked": len(matches),
        "matches_found": sum(1 for item in matches if item["matched"]),
        "matches": matches,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--recordings-root", required=True, type=Path)
    parser.add_argument("--sample-id", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(match(args.db, args.recordings_root, args.sample_id), sort_keys=True))


if __name__ == "__main__":
    main()
