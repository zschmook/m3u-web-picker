from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sports


class ForcedEventTimestampTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "sports.db"
        sports.init_db(self.db_path)
        self.settings = dict(sports.DEFAULT_SETTINGS)
        self.settings["timezone"] = "America/New_York"
        self.now = datetime(2026, 8, 11, 19, 0, tzinfo=ZoneInfo("America/New_York"))
        self.channel = {
            "name": "Historical sports anchor",
            "tvg_name": "",
            "group": "MLB",
            "tvg_id": "",
            "url": "",
        }

    def test_forced_start_bypasses_malformed_title_time(self):
        forced_start = self.now - timedelta(hours=1)
        forced_end = forced_start + timedelta(hours=4)

        event = sports._event_from_text(
            self.db_path,
            self.channel,
            "30pm Phillies at Mets",
            self.settings,
            self.now,
            forced_start=forced_start,
            forced_end=forced_end,
            extra_text="mlb",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["start"], forced_start)
        self.assertEqual(event["end"], forced_end)
        self.assertEqual(event["timing_source"], "xmltv")

    def test_unforced_provider_entry_still_rejects_malformed_time(self):
        with self.assertRaises(sports.MalformedSportsEntry):
            sports._event_from_text(
                self.db_path,
                self.channel,
                "30pm Phillies at Mets",
                self.settings,
                self.now,
                extra_text="mlb",
            )


if __name__ == "__main__":
    unittest.main()
