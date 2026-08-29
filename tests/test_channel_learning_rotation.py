from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import commercial_profiles
import commercial_signatures
import channel_learning_rotation
from channel_learning_rotation import ChannelLearningRotation, stream_path
from media.logo_detector import LiveLogoDetector


class ChannelLearningRotationTests(unittest.TestCase):
    def test_default_rotation_uses_twenty_minute_visits(self):
        self.assertEqual(channel_learning_rotation.CHANNEL_SECONDS, 20 * 60)

    def test_stream_path_uses_encoded_production_pipeline(self):
        self.assertEqual(
            stream_path("/guide/play/manual/abc123"),
            "/stream/channel/manual/abc123/mpegts",
        )
        self.assertEqual(
            stream_path("/guide/play/sports/7001"),
            "/stream/channel/sports/7001/mpegts",
        )
        self.assertEqual(stream_path("https://example.com/video"), "")

    def test_rotation_skips_generated_sports_channels(self):
        rotation = ChannelLearningRotation(channel_seconds=1)
        captured = []
        rotation._run = lambda channels: captured.extend(channels)
        rotation.start([
            {
                "number": 1,
                "name": "Local NBC",
                "tvg_id": "nbc.local",
                "generated": False,
                "play_url": "/guide/play/manual/one",
            },
            {
                "number": 9001,
                "name": "Upcoming game",
                "tvg_id": "sports.event",
                "generated": True,
                "play_url": "/guide/play/sports/9001",
            },
        ])
        rotation._thread.join(timeout=1)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["name"], "Local NBC")
        self.assertFalse(captured[0]["generated"])

    def test_start_accepts_an_editable_channel_window(self):
        rotation = ChannelLearningRotation()
        rotation._run = lambda channels: None

        status = rotation.start([
            {
                "number": 1,
                "name": "Local NBC",
                "generated": False,
                "play_url": "/guide/play/manual/abc123",
            }
        ], channel_seconds=20 * 60)

        self.assertEqual(rotation.channel_seconds, 20 * 60)
        self.assertEqual(status["channel_seconds"], 20 * 60)

        with self.assertRaisesRegex(ValueError, "between 5 and 120"):
            rotation.start([
                {
                    "number": 1,
                    "name": "Local NBC",
                    "generated": False,
                    "play_url": "/guide/play/manual/abc123",
                }
            ], channel_seconds=2 * 60)

    def test_rotation_repeats_until_stopped(self):
        rotation = ChannelLearningRotation(channel_seconds=1)
        sampled = []

        def sample(index, total, channel):
            sampled.append((index, total, channel["name"]))
            if len(sampled) == 3:
                rotation._stop.set()

        rotation._sample_channel = sample
        rotation._status = rotation._empty_status()
        rotation._status.update(running=True, total_channels=1)
        rotation._run([{"name": "Local NBC"}])

        self.assertEqual(len(sampled), 3)
        self.assertEqual(rotation.status()["passes_completed"], 2)
        self.assertEqual(rotation.status()["total_channel_slots_completed"], 2)
        self.assertEqual(rotation.status()["phase"], "stopped")

    def test_later_pass_priority_prefers_channels_with_repeat_candidates(self):
        with tempfile.TemporaryDirectory() as parent:
            db_path = Path(parent) / "learning.db"
            commercial_profiles.record(
                db_path, "tvg:quiet", label="program", features={}
            )
            commercial_profiles.record(
                db_path, "tvg:repeat", label="commercial", features={}
            )
            points = [
                (
                    index,
                    tuple([index] * commercial_signatures.TILE_COUNT),
                    tuple([200] * (commercial_signatures.TILE_COUNT * 3)),
                )
                for index in range(15)
            ]
            commercial_signatures.record_episode(
                db_path, "tvg:repeat", "one", points,
            )
            commercial_signatures.record_episode(
                db_path, "tvg:repeat", "two", points,
            )
            rotation = ChannelLearningRotation(channel_seconds=1)
            rotation._db_path = db_path
            channels = [
                {"name": "Quiet", "profile_identity": "tvg:quiet"},
                {"name": "Repeat", "profile_identity": "tvg:repeat"},
            ]

            ordered = rotation._priority_order(channels)

            self.assertEqual(ordered[0]["name"], "Repeat")

    def test_only_static_event_like_channels_are_skipped_early(self):
        rotation = ChannelLearningRotation(channel_seconds=1)
        self.assertTrue(rotation._event_like_channel({"name": "ESPN 4K"}))
        self.assertFalse(rotation._event_like_channel({"name": "Local NBC"}))
        self.assertTrue(rotation._inactive_event_status({
            "commercial": False,
            "channel_features": {
                "cut_density": 0.4,
                "mean_color_change": 0.6,
                "color_volatility": 1.2,
            },
        }))
        self.assertFalse(rotation._inactive_event_status({
            "commercial": False,
            "channel_features": {
                "cut_density": 4.0,
                "mean_color_change": 6.0,
                "color_volatility": 12.0,
            },
        }))

    def test_channel_archive_keeps_timestamps_graph_and_raw_rows(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            db_path = root / "learning.db"
            run_dir = root / "run"
            run_dir.mkdir()
            commercial_profiles.ensure_schema(db_path)
            observed = datetime.now(timezone.utc).replace(microsecond=0)
            commercial_profiles.record(
                db_path,
                "tvg:nbc.local",
                label="commercial",
                observed_at=observed,
                features={
                    "cut_density": 0.4,
                    "color_volatility": 0.6,
                    "program_graphics_confidence": 0.1,
                    "bug_identity_confidence": 0.2,
                    "commercial_confidence": 0.9,
                },
            )
            rotation = ChannelLearningRotation(channel_seconds=1)
            rotation._db_path = db_path
            rotation._run_dir = run_dir
            rotation._status = rotation._empty_status()
            (run_dir / "manifest.json").write_text(
                json.dumps({"completed": []}), encoding="utf-8"
            )
            channel = {
                "number": 1,
                "name": "Local NBC",
                "tvg_id": "NBC.local",
                "profile_identity": "tvg:nbc.local",
            }
            rotation._archive_channel(
                index=1,
                channel=channel,
                started_at=(observed - timedelta(seconds=1)).isoformat(timespec="seconds"),
                ended_at=(observed + timedelta(seconds=1)).isoformat(timespec="seconds"),
                bytes_received=1234,
                reconnects=0,
            )
            folder = next(run_dir.glob("pass-*/*"))
            rows = json.loads((folder / "observations.json").read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["observed_at"], observed.isoformat(timespec="seconds"))
            self.assertEqual(rows[0]["commercial_confidence"], 0.9)
            self.assertIn("Commercial confidence", (folder / "graph.svg").read_text(encoding="utf-8"))
            self.assertTrue((folder / "observations.csv").is_file())

    def test_inspection_images_are_linked_to_recorded_decisions(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            source = root / "analysis-frame.jpg"
            source.write_bytes(b"same-frame-bytes")
            archive = root / "snapshots"
            detector = LiveLogoDetector.create(lambda _active: None)
            try:
                detector.set_inspection_archive(archive)
                detector._current_analysis_frame = source
                detector._archive_decision_frame(
                    {"recorded": True, "id": 42, "observed_at": "2026-08-26T12:34:56+00:00"},
                    label="commercial",
                    source="inferred",
                    features={"commercial_confidence": 0.9},
                    detector_state="commercial",
                    commercial_reason="logo-missing",
                )
                snapshots = sorted(archive.glob("observation-*.jpg"))
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].read_bytes(), b"same-frame-bytes")
                sidecar = json.loads(snapshots[0].with_suffix(".json").read_text(encoding="utf-8"))
                self.assertEqual(sidecar["id"], 42)
                self.assertEqual(sidecar["features"]["commercial_confidence"], 0.9)
            finally:
                detector.stop()


if __name__ == "__main__":
    unittest.main()
