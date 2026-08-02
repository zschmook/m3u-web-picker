from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import core  # noqa: E402
import sports  # noqa: E402

try:
    from app import app  # noqa: E402
except ModuleNotFoundError as exc:  # The lightweight source-test environment may omit Flask.
    if exc.name != "flask":
        raise
    app = None


@unittest.skipIf(app is None, "Flask is installed inside the Docker image")
class SportsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db_path = core.DB_PATH
        self.original_channels = core.channels
        self.original_selected_ids = core.selected_ids
        self.original_source_mode = core.source_mode
        self.original_source_url = core.last_source_url
        self.original_playlist_path = core.PLAYLIST_PATH
        self.original_sports_epg_path = core.SPORTS_EPG_PATH
        self.original_combined_epg_path = core.COMBINED_EPG_PATH
        self.original_epg_cache_path = core.EPG_CACHE_PATH
        core.DB_PATH = Path(self.temp.name) / "api.db"
        core.PLAYLIST_PATH = Path(self.temp.name) / "custom.m3u"
        core.SPORTS_EPG_PATH = Path(self.temp.name) / "sports.xml"
        core.COMBINED_EPG_PATH = Path(self.temp.name) / "combined.xml"
        core.EPG_CACHE_PATH = Path(self.temp.name) / "provider.xml"
        core.channels = []
        core.selected_ids = set()
        core.source_mode = ""
        core.last_source_url = ""
        sports.init_db(core.DB_PATH)
        self.client = app.test_client()

    def tearDown(self):
        core.DB_PATH = self.original_db_path
        core.channels = self.original_channels
        core.selected_ids = self.original_selected_ids
        core.source_mode = self.original_source_mode
        core.last_source_url = self.original_source_url
        core.PLAYLIST_PATH = self.original_playlist_path
        core.SPORTS_EPG_PATH = self.original_sports_epg_path
        core.COMBINED_EPG_PATH = self.original_combined_epg_path
        core.EPG_CACHE_PATH = self.original_epg_cache_path
        self.temp.cleanup()

    def test_fresh_api_has_no_sports_rules(self):
        response = self.client.get("/api/sports/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["rules"], [])
        self.assertFalse(payload["settings"]["everything_mode"])
        self.assertFalse(payload["scan"]["running"])

    def test_everything_mode_is_a_separate_setting_from_curated_rules(self):
        added = self.client.post(
            "/api/sports/rules",
            json={"scope_type": "league", "scope_id": "mlb"},
        )
        self.assertEqual(added.status_code, 200)
        rules_before = added.get_json()["rules"]
        enabled = self.client.patch(
            "/api/sports/settings",
            json={"everything_mode": True},
        )
        self.assertEqual(enabled.status_code, 200)
        payload = enabled.get_json()
        self.assertTrue(payload["settings"]["everything_mode"])
        self.assertEqual(payload["rules"], rules_before)

    def test_refresh_time_validation_is_friendly_and_non_destructive(self):
        good = self.client.patch("/api/sports/settings", json={"refresh_time": "04:15"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.get_json()["settings"]["refresh_time"], "04:15")

        bad = self.client.patch("/api/sports/settings", json={"refresh_time": "29:88"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("valid time", bad.get_json()["error"])
        self.assertEqual(sports.get_settings(core.DB_PATH)["refresh_time"], "04:15")

    def test_batch_add_then_remove_all_stays_empty(self):
        added = self.client.post(
            "/api/sports/rules",
            json={
                "items": [
                    {"scope_type": "league", "scope_id": "nfl"},
                    {"scope_type": "sport", "scope_id": "cornhole"},
                ]
            },
        )
        self.assertEqual(added.status_code, 200)
        rules = added.get_json()["rules"]
        self.assertEqual(len(rules), 2)
        for rule in rules:
            deleted = self.client.delete(f"/api/sports/rules/{rule['id']}")
            self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/sports/settings").get_json()["rules"], [])

    def test_update_now_requires_master_switch_but_not_auto_update(self):
        disabled = self.client.post("/api/sports/scan")
        self.assertEqual(disabled.status_code, 409)
        self.assertIn("Turn on Sports Automation", disabled.get_json()["error"])

        self.client.patch(
            "/api/sports/settings",
            json={"enabled": True, "auto_update": False},
        )
        no_source = self.client.post("/api/sports/scan")
        self.assertEqual(no_source.status_code, 409)
        self.assertIn("Load an M3U source", no_source.get_json()["error"])

    def test_master_switch_rewrites_served_outputs_and_restores_cached_rows(self):
        fixture = """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 19:05:00)
http://provider.test/user/pass/100.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" group-title="MLB / MiLB",MLB Philadelphia Phillies
http://provider.test/user/pass/200.ts
#EXTINF:-1 tvg-id="BaltimoreOrioles.mlb" group-title="MLB / MiLB",MLB Baltimore Orioles
http://provider.test/user/pass/201.ts
"""
        core.channels = core.parse_m3u_text(fixture)
        core.EPG_CACHE_PATH.write_text(
            '<?xml version="1.0"?><tv><channel id="dateline"><display-name>Dateline</display-name></channel></tv>',
            encoding="utf-8",
        )
        sports.update_settings(core.DB_PATH, {"enabled": True, "timezone": "America/New_York"})
        sports.add_rule(core.DB_PATH, {"scope_type": "team", "scope_id": "mlb:philadelphia-phillies"})
        sports.scan_channels(
            core.DB_PATH,
            core.channels,
            sports_epg_path=core.SPORTS_EPG_PATH,
            combined_epg_path=core.COMBINED_EPG_PATH,
            now=datetime(
                2026, 8, 2, 2, 30,
                tzinfo=ZoneInfo("America/New_York"),
            ),
            trigger="test",
        )
        core.write_current_playlist()

        disabled = self.client.patch("/api/sports/settings", json={"enabled": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertNotIn("m3u-picker-sports-", core.PLAYLIST_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("m3u-picker-sports-", core.SPORTS_EPG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(sports.generated_rows(core.DB_PATH, include_cached=True)), 3)

        enabled = self.client.patch("/api/sports/settings", json={"enabled": True})
        self.assertEqual(enabled.status_code, 200)
        self.assertIn("m3u-picker-sports-", core.PLAYLIST_PATH.read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main()
