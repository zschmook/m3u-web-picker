from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import commercial_lab_rotation
import dvr


class CommercialLabRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "app.db"
        dvr.init_db(self.db_path)
        self.channels = [
            {
                "name": f"Channel {index}",
                "tvg_id": f"channel-{index}",
                "play_url": f"/guide/play/manual/{index}",
            }
            for index in range(10)
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_finished_capture_is_replaced_immediately(self):
        current = commercial_lab_rotation.set_control(
            self.db_path, enabled=True, slots=4, sample_minutes=20
        )
        now = datetime(2035, 8, 30, 20, 0, tzinfo=timezone.utc)

        first = commercial_lab_rotation.ensure_capacity(
            self.db_path, self.channels, dvr.schedule_recording, current=current, now=now
        )
        self.assertEqual(len(first), 4)
        dvr._update_recording(self.db_path, first[0]["id"], status="completed")

        replacement = commercial_lab_rotation.ensure_capacity(
            self.db_path, self.channels, dvr.schedule_recording, current=current, now=now
        )
        self.assertEqual(len(replacement), 1)
        active = [
            item for item in dvr.list_recordings(self.db_path)
            if item["status"] in {"scheduled", "recording", "processing"}
        ]
        self.assertEqual(len(active), 4)

    def test_excludes_noncommercial_and_generated_channels(self):
        excluded = [
            {"name": "PBS WLIW", "tvg_id": "pbs", "play_url": "/guide/play/manual/pbs"},
            {"name": "HBO East", "tvg_id": "hbo", "play_url": "/guide/play/manual/hbo"},
            {"name": "US: AMC (EAST)", "tvg_id": "amc", "play_url": "/guide/play/manual/amc"},
            {"name": "US: AMC+", "tvg_id": "amc-plus", "play_url": "/guide/play/manual/amc-plus"},
            {"name": "Synthetic Game", "tvg_id": "game", "play_url": "/guide/play/sports/game"},
        ]
        self.assertTrue(all(not commercial_lab_rotation.eligible_channel(item) for item in excluded))

    def test_disabled_control_does_not_schedule(self):
        current = commercial_lab_rotation.set_control(
            self.db_path, enabled=False, slots=4, sample_minutes=20
        )
        self.assertEqual(
            commercial_lab_rotation.ensure_capacity(
                self.db_path, self.channels, dvr.schedule_recording, current=current
            ),
            [],
        )

    def test_oldest_completed_unprocessed_capture_is_selected(self):
        now = datetime(2035, 8, 30, 20, 0, tzinfo=timezone.utc)
        first = dvr.schedule_recording(
            self.db_path,
            play_url=self.channels[0]["play_url"],
            tvg_id=self.channels[0]["tvg_id"],
            channel_name=self.channels[0]["name"],
            title=f"{commercial_lab_rotation.TITLE_PREFIX}First",
            start_at=now,
            stop_at=now + timedelta(minutes=20),
        )
        second = dvr.schedule_recording(
            self.db_path,
            play_url=self.channels[1]["play_url"],
            tvg_id=self.channels[1]["tvg_id"],
            channel_name=self.channels[1]["name"],
            title=f"{commercial_lab_rotation.TITLE_PREFIX}Second",
            start_at=now,
            stop_at=now + timedelta(minutes=20),
        )
        dvr._update_recording(
            self.db_path,
            first["id"],
            status="completed",
            output_name="first.ts",
            completed_at="2035-08-30T20:00:00+00:00",
        )
        dvr._update_recording(
            self.db_path,
            second["id"],
            status="completed",
            output_name="second.ts",
            completed_at="2035-08-30T20:01:00+00:00",
        )

        self.assertEqual(
            commercial_lab_rotation.next_completed_recording(self.db_path),
            first["id"],
        )
        self.assertEqual(
            commercial_lab_rotation.next_completed_recording(
                self.db_path, excluded_ids={first["id"]}
            ),
            second["id"],
        )


if __name__ == "__main__":
    unittest.main()
