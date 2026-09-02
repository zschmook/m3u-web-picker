from __future__ import annotations

import base64
import importlib.util
import sqlite3
import struct
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from database import connect
import dvr


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("commercial_lab", ROOT / "scripts" / "commercial_lab.py")
commercial_lab = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(commercial_lab)


def encoded(*words: int) -> str:
    return base64.b64encode(b"".join(struct.pack("<I", word) for word in words)).decode("ascii")


class CommercialLabTests(unittest.TestCase):
    def test_comskip_no_commercials_is_a_valid_zero_break_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.ts"
            source.write_bytes(b"valid-media-placeholder")

            def fake_run(_command, *, cwd, stdout, stderr, timeout, check):
                stdout.write(b"Commercials were not found.\n")
                return type("Result", (), {"returncode": 1})()

            with (
                patch.object(commercial_lab.shutil, "which", return_value="/usr/bin/comskip"),
                patch.object(commercial_lab.subprocess, "run", side_effect=fake_run),
            ):
                edl, log = commercial_lab._run_comskip(source)

            self.assertTrue(edl.is_file())
            self.assertEqual(edl.read_text(encoding="utf-8"), "")
            self.assertIn("Commercials were not found", log.read_text(encoding="utf-8"))

    def test_identical_fingerprints_have_perfect_similarity(self):
        value = encoded(*range(64))
        self.assertEqual(commercial_lab.fingerprint_similarity(value, value), 1.0)

    def test_similarity_tolerates_a_small_alignment_offset(self):
        core = list(range(64))
        left = encoded(*core)
        right = encoded(999, 998, *core, 997)
        self.assertEqual(commercial_lab.fingerprint_similarity(left, right), 1.0)

    def test_database_contains_lab_result_and_comparison_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            connection = connect(db_path)
            connection.close()
            raw = sqlite3.connect(db_path)
            try:
                tables = {
                    str(row[0]) for row in raw.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                raw.close()
        self.assertIn("dvr_commercial_comparisons", tables)
        self.assertIn("dvr_commercial_lab_runs", tables)

    def test_source_is_deleted_only_after_lab_result_commit(self):
        source = (ROOT / "scripts" / "commercial_lab.py").read_text(encoding="utf-8")
        process_source = source[source.index("def process_recording"):]
        self.assertLess(process_source.index("conn.commit()"), process_source.index("artifact.unlink"))
        self.assertIn("source_deleted = ?", source)

    def test_scheduler_fills_four_slots_and_restores_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            connection = connect(db_path)
            connection.close()
            calls = []

            def fake_api(_base, path, method="GET", payload=None):
                calls.append((path, method, payload))
                if path == "/api/dvr" and method == "GET":
                    return {"recordings": [], "settings": {"padding_after_seconds": 120, "max_concurrent_recordings": 2}}
                if path == "/api/guide/channels":
                    return {"channels": [
                        {"name": f"Channel {index}", "tvg_id": f"channel-{index}", "play_url": f"/guide/play/manual/{index}"}
                        for index in range(6)
                    ]}
                if path == "/api/dvr/settings":
                    return {"settings": payload}
                if path == "/api/dvr/recordings":
                    return {"recording": {
                        "id": len([call for call in calls if call[0] == "/api/dvr/recordings"]),
                        "channel_name": payload["title"],
                    }}
                raise AssertionError((path, method, payload))

            with patch.object(commercial_lab, "_api", side_effect=fake_api):
                created = commercial_lab.schedule_to_capacity(
                    db_path, "http://example.test", slots=4, minutes=20
                )

        recording_calls = [call for call in calls if call[0] == "/api/dvr/recordings"]
        settings_calls = [call for call in calls if call[0] == "/api/dvr/settings"]
        self.assertEqual(len(created), 4)
        self.assertEqual(len(recording_calls), 4)
        self.assertEqual(settings_calls[0][2]["padding_after_seconds"], 0)
        self.assertEqual(settings_calls[-1][2]["padding_after_seconds"], 120)
        for _path, _method, payload in recording_calls:
            start = commercial_lab.datetime.fromisoformat(payload["start"])
            stop = commercial_lab.datetime.fromisoformat(payload["stop"])
            self.assertEqual((stop - start).total_seconds(), 1200)

    def test_premium_movie_networks_are_excluded_from_rotation(self):
        source = (ROOT / "scripts" / "commercial_lab.py").read_text(encoding="utf-8")
        for term in ("hbo", "showtime", "tmc", "the movie channel", "starz", "amc"):
            self.assertIn(f'"{term}"', source)
        self.assertIn("EXCLUDED_CHANNEL_TERMS", source)

    def test_generated_sports_channels_are_excluded_from_rotation(self):
        source = (ROOT / "scripts" / "commercial_lab.py").read_text(encoding="utf-8")
        self.assertIn('startswith("/guide/play/sports/")', source)

    def test_public_television_channels_are_excluded_from_rotation(self):
        source = (ROOT / "scripts" / "commercial_lab.py").read_text(encoding="utf-8")
        for term in ("pbs", "public television"):
            self.assertIn(f'"{term}"', source)

    def test_ts_and_temporary_h265_are_deleted_after_comparison_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "test.db"
            dvr.init_db(db_path)
            start = datetime.now(timezone.utc) + timedelta(minutes=1)
            recording = dvr.schedule_recording(
                db_path,
                play_url="/guide/play/manual/test",
                tvg_id="test-channel",
                channel_name="Test Channel",
                title="Commercial Lab · Test Channel · sample",
                start_at=start,
                stop_at=start + timedelta(minutes=20),
            )
            source = root / "sample.ts"
            source.write_bytes(b"source-video")
            dvr._update_recording(
                db_path, recording["id"], status="completed", output_name=source.name
            )

            def fake_comskip(_source):
                edl = root / "sample.edl"
                log = root / "sample.comskip.log"
                edl.write_text("100 200 0\n", encoding="utf-8")
                log.write_text("ok", encoding="utf-8")
                return edl, log

            def fake_h265(_source, _edl, _duration):
                converted = root / ".sample.commercial-lab.mkv"
                log = root / ".sample.h265.log"
                converted.write_bytes(b"converted")
                log.write_text("ok", encoding="utf-8")
                return {
                    "path": converted,
                    "log_path": log,
                    "duration_seconds": 1100.0,
                    "encoder": "hevc_nvenc",
                    "cuts": [(100.0, 200.0)],
                    "expected_removed_seconds": 100.0,
                    "actual_removed_seconds": 100.0,
                    "removal_delta_seconds": 0.0,
                    "source_bytes": source.stat().st_size,
                    "converted_bytes": converted.stat().st_size,
                }

            with (
                patch.object(commercial_lab, "_duration", return_value=1200.0),
                patch.object(commercial_lab, "_run_comskip", side_effect=fake_comskip),
                patch.object(commercial_lab, "_fingerprint", return_value=(encoded(*range(64)), "digest")),
                patch.object(commercial_lab, "_create_h265_comparison", side_effect=fake_h265),
            ):
                result = commercial_lab.process_recording(
                    db_path, root, recording["id"], delete_source=True
                )

            self.assertTrue(result["source_deleted"])
            self.assertEqual(result["encoder"], "hevc_nvenc")
            self.assertFalse(source.exists())
            self.assertFalse((root / ".sample.commercial-lab.mkv").exists())
            raw = connect(db_path)
            try:
                sample = raw.execute(
                    "SELECT source_path, converted_path, edl_path, converted_duration FROM dvr_commercial_samples"
                ).fetchone()
            finally:
                raw.close()
            self.assertEqual(sample[:3], ("", "", ""))
            self.assertEqual(sample[3], 1100.0)


if __name__ == "__main__":
    unittest.main()
