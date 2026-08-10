from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import sports


class ScheduleApiHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "sports.db"
        sports.init_db(self.db_path)
        sports.add_rule(
            self.db_path,
            {"scope_type": "league", "scope_id": "nfl", "feed_preference": "best"},
        )
        sports.update_schedule_api_config(
            self.db_path,
            enabled=True,
            api_key="test-api-key",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_configured_dataset_without_cache_is_not_reported_as_active(self):
        payload = sports.schedule_api_status_payload(self.db_path)
        nfl = next(item for item in payload["apis"] if item["id"] == "nfl")

        self.assertTrue(payload["effective"])
        self.assertIsNone(nfl["last_fetch_at"])
        self.assertEqual(nfl["status_code"], "no_cache")
        self.assertEqual(nfl["status_label"], "No successful cache")
        self.assertEqual(payload["dataset_summary"]["planned"], 1)
        self.assertEqual(payload["dataset_summary"]["cached"], 0)
        self.assertEqual(payload["dataset_summary"]["no_cache"], 1)

    def test_failed_refresh_is_persisted_in_dataset_status(self):
        scan_anchor = datetime(
            2026,
            8,
            10,
            7,
            30,
            tzinfo=ZoneInfo("America/New_York"),
        )
        with patch.object(
            sports,
            "_fetch_schedule_api_dataset_date",
            side_effect=ValueError("Could not fetch NFL schedule for 2026-08-10."),
        ):
            result = sports.refresh_schedule_api_if_due(
                self.db_path,
                scan_anchor=scan_anchor,
                force=True,
            )

        self.assertTrue(result["failures"])
        self.assertIn("NFL", result["warning"])

        payload = sports.schedule_api_status_payload(self.db_path)
        nfl = next(item for item in payload["apis"] if item["id"] == "nfl")
        self.assertEqual(nfl["status_code"], "error")
        self.assertEqual(nfl["status_label"], "Refresh failed")
        self.assertIn("Could not fetch NFL schedule", nfl["last_error"])
        self.assertIsNotNone(nfl["last_attempt_at"])
        self.assertEqual(payload["dataset_summary"]["issues"], 1)

    def test_refresh_health_is_internal_and_does_not_leak_into_normal_settings(self):
        scan_anchor = datetime(
            2026,
            8,
            10,
            7,
            30,
            tzinfo=ZoneInfo("America/New_York"),
        )
        with patch.object(
            sports,
            "_fetch_schedule_api_dataset_date",
            side_effect=ValueError("Could not fetch NFL schedule for 2026-08-10."),
        ):
            sports.refresh_schedule_api_if_due(
                self.db_path,
                scan_anchor=scan_anchor,
                force=True,
            )

        settings = sports.get_settings(self.db_path)
        self.assertNotIn("__schedule_api_health", settings)
        payload = sports.schedule_api_status_payload(self.db_path)
        self.assertNotIn("test-api-key", repr(payload))


if __name__ == "__main__":
    unittest.main()
