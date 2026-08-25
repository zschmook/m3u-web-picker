from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from guide_epg import enrich_guide_channels, programme_window


class GuideEpgTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.epg_path = Path(self.temp.name) / "epg.xml"
        self.now = datetime(
            2026,
            8,
            10,
            14,
            30,
            tzinfo=ZoneInfo("America/New_York"),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _write_epg(self) -> None:
        self.epg_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="station-1"><display-name>Station 1</display-name></channel>
  <channel id="station-2"><display-name>Station 2</display-name></channel>
  <programme start="20260810140000 -0400" stop="20260810150000 -0400" channel="station-1">
    <title>Dateline NBC</title>
    <sub-title>The Mystery</sub-title>
    <desc>Current programme description.</desc>
    <category>News</category>
  </programme>
  <programme start="20260810150000 -0400" stop="20260810160000 -0400" channel="station-1">
    <title>NBC News</title>
  </programme>
  <programme start="20260810160000 -0400" stop="20260810170000 -0400" channel="station-2">
    <title>Later Show</title>
  </programme>
</tv>
""",
            encoding="utf-8",
        )

    def test_exact_tvg_id_gets_now_next_and_upcoming_schedule(self):
        self._write_epg()
        channels = [
            {
                "number": 10,
                "name": "NBC 10",
                "tvg_id": "station-1",
                "play_url": "/guide/play/manual/abc",
            }
        ]

        enriched, status = enrich_guide_channels(
            channels,
            self.epg_path,
            timezone_name="America/New_York",
            now=self.now,
        )

        self.assertEqual(enriched[0]["now"]["title"], "Dateline NBC")
        self.assertEqual(enriched[0]["now"]["subtitle"], "The Mystery")
        self.assertEqual(enriched[0]["next"]["title"], "NBC News")
        self.assertEqual(
            [programme["title"] for programme in enriched[0]["upcoming"]],
            ["NBC News"],
        )
        self.assertEqual(status["channel_count"], 1)
        self.assertEqual(status["matched_channels"], 1)
        self.assertEqual(status["current_channels"], 1)
        self.assertEqual(status["programme_count"], 3)

    def test_unmatched_tvg_id_does_not_guess_by_name(self):
        self._write_epg()
        channels = [
            {
                "number": 10,
                "name": "Station 1",
                "tvg_id": "wrong-id",
                "play_url": "/guide/play/manual/abc",
            }
        ]

        enriched, status = enrich_guide_channels(
            channels,
            self.epg_path,
            timezone_name="America/New_York",
            now=self.now,
        )

        self.assertIsNone(enriched[0]["now"])
        self.assertIsNone(enriched[0]["next"])
        self.assertEqual(enriched[0]["upcoming"], [])
        self.assertEqual(status["matched_channels"], 0)
        self.assertEqual(status["current_channels"], 0)

    def test_future_programme_is_reported_as_next_and_upcoming_without_current(self):
        self._write_epg()
        channels = [
            {
                "number": 20,
                "name": "Station 2",
                "tvg_id": "station-2",
                "play_url": "/guide/play/manual/xyz",
            }
        ]

        enriched, status = enrich_guide_channels(
            channels,
            self.epg_path,
            timezone_name="America/New_York",
            now=self.now,
        )

        self.assertIsNone(enriched[0]["now"])
        self.assertEqual(enriched[0]["next"]["title"], "Later Show")
        self.assertEqual(enriched[0]["upcoming"][0]["title"], "Later Show")
        self.assertEqual(status["matched_channels"], 1)
        self.assertEqual(status["current_channels"], 0)

    def test_missing_served_epg_is_reported_without_failing_channel_list(self):
        channels = [
            {
                "number": 10,
                "name": "NBC 10",
                "tvg_id": "station-1",
                "play_url": "/guide/play/manual/abc",
            }
        ]

        enriched, status = enrich_guide_channels(
            channels,
            Path(self.temp.name) / "missing.xml",
            timezone_name="America/New_York",
            now=self.now,
        )

        self.assertEqual(len(enriched), 1)
        self.assertIsNone(enriched[0]["now"])
        self.assertEqual(enriched[0]["upcoming"], [])
        self.assertFalse(status["available"])
        self.assertIn("not available", status["error"])

    def test_programme_window_reports_exact_current_boundary(self):
        self._write_epg()

        window = programme_window(
            self.epg_path,
            "station-1",
            timezone_name="America/New_York",
            now=self.now,
        )

        self.assertEqual(window["current"]["title"], "Dateline NBC")
        self.assertEqual(window["current"]["stop"], "2026-08-10T15:00:00-04:00")
        self.assertEqual(window["next"]["title"], "NBC News")


if __name__ == "__main__":
    unittest.main()
