from __future__ import annotations

import os
import unittest

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import sports  # noqa: E402


class GeneratedSportsLogoScrubTests(unittest.TestCase):
    def test_blank_generated_logo_removes_provider_logo_from_extinf(self):
        channel = {
            "raw": [
                '#EXTINF:-1 tvg-id="provider-1" tvg-logo="https://provider.test/phillies.png" group-title="MLB",Phillies Feed',
                "https://provider.test/live/1.ts",
            ]
        }
        generated = {
            "assigned_number": 1000,
            "display_name": "MLB • Chicago White Sox @ Detroit Tigers — Event Feed",
            "group_title": "Sports Today",
            "event_key": "mlb:chicago-white-sox@detroit-tigers:2026-08-15",
            "feed_type": "event",
            "subtitle": "Provider event stream",
            "tvg_id": "m3u-picker-sports-current-event",
            "tvg_logo": "",
        }

        raw = sports._generated_raw(channel, generated)

        self.assertNotIn("phillies.png", raw[0].lower())
        self.assertNotIn("tvg-logo=", raw[0].lower())
        self.assertIn('tvg-id="m3u-picker-sports-current-event"', raw[0])
        self.assertEqual(raw[-1], "/sports/stream/1000")

    def test_generated_event_logo_replaces_provider_logo(self):
        channel = {
            "raw": [
                '#EXTINF:-1 tvg-logo="https://provider.test/phillies.png",Phillies Feed',
                "https://provider.test/live/1.ts",
            ]
        }
        generated = {
            "assigned_number": 1000,
            "display_name": "MLB • Chicago White Sox @ Detroit Tigers — Event Feed",
            "group_title": "Sports Today",
            "event_key": "mlb:chicago-white-sox@detroit-tigers:2026-08-15",
            "feed_type": "event",
            "subtitle": "Provider event stream",
            "tvg_id": "m3u-picker-sports-current-event",
            "tvg_logo": "http://picker.test/api/event-logo/current.png",
        }

        raw = sports._generated_raw(channel, generated)

        self.assertNotIn("phillies.png", raw[0].lower())
        self.assertIn('tvg-logo="http://picker.test/api/event-logo/current.png"', raw[0])


if __name__ == "__main__":
    unittest.main()
