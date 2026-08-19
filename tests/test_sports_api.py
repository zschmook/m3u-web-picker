from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
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
        self.original_provider_sources = core.provider_sources
        self.original_playlist_path = core.PLAYLIST_PATH
        self.original_sports_epg_path = core.SPORTS_EPG_PATH
        self.original_combined_epg_path = core.COMBINED_EPG_PATH
        self.original_epg_cache_path = core.EPG_CACHE_PATH
        self.original_master_cache_path = core.MASTER_CACHE_PATH
        self.original_provider_dir = core.PROVIDER_DIR
        self.original_config_path = core.CONFIG_PATH
        core.DB_PATH = Path(self.temp.name) / "api.db"
        core.PLAYLIST_PATH = Path(self.temp.name) / "custom.m3u"
        core.SPORTS_EPG_PATH = Path(self.temp.name) / "sports.xml"
        core.COMBINED_EPG_PATH = Path(self.temp.name) / "combined.xml"
        core.EPG_CACHE_PATH = Path(self.temp.name) / "provider.xml"
        core.MASTER_CACHE_PATH = Path(self.temp.name) / "primary.m3u"
        core.PROVIDER_DIR = Path(self.temp.name) / "providers"
        core.PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
        core.CONFIG_PATH = Path(self.temp.name) / "config.json"
        core.channels = []
        core.selected_ids = set()
        core.source_mode = ""
        core.last_source_url = ""
        core.provider_sources = []
        sports.init_db(core.DB_PATH)
        self.client = app.test_client()

    def tearDown(self):
        core.DB_PATH = self.original_db_path
        core.channels = self.original_channels
        core.selected_ids = self.original_selected_ids
        core.source_mode = self.original_source_mode
        core.last_source_url = self.original_source_url
        core.provider_sources = self.original_provider_sources
        core.PLAYLIST_PATH = self.original_playlist_path
        core.SPORTS_EPG_PATH = self.original_sports_epg_path
        core.COMBINED_EPG_PATH = self.original_combined_epg_path
        core.EPG_CACHE_PATH = self.original_epg_cache_path
        core.MASTER_CACHE_PATH = self.original_master_cache_path
        core.PROVIDER_DIR = self.original_provider_dir
        core.CONFIG_PATH = self.original_config_path
        self.temp.cleanup()

    def test_guide_manual_channel_uses_ffmpeg_playback_route_without_exposing_provider_url(self):
        source_url = "http://provider.test/user/pass/live.ts"
        core.channels = core.parse_m3u_text(
            f"""#EXTM3U
#EXTINF:-1 tvg-id="test.channel" group-title="Test",Test Channel
{source_url}
"""
        )
        core.selected_ids = {int(core.channels[0]["id"])}

        response = self.client.get("/api/guide/channels")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        item = payload["channels"][0]
        self.assertTrue(item["play_url"].startswith("/guide/play/manual/"))
        self.assertNotIn("direct_url", item)
        self.assertNotIn(source_url, response.get_data(as_text=True))

        with patch("api.routes.shutil.which", return_value=None):
            playback = self.client.get(item["play_url"])
        self.assertEqual(playback.status_code, 503)
        self.assertIn("ffmpeg", playback.get_data(as_text=True).lower())

    def test_generated_sports_stream_uses_ffmpeg_alert_wrapper(self):
        source_url = "http://provider.test/user/pass/game.ts"
        assigned_number = 1070
        with patch(
            "api.outputs.sports.generated_stream_target",
            return_value=source_url,
        ):
            response = self.client.get(
                sports.generated_stream_path(assigned_number),
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["Location"],
            f"/sports/alert-stream/{assigned_number}/stream.m3u8",
        )
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

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

    def test_interval_schedule_settings_persist_and_validate(self):
        good = self.client.patch(
            "/api/sports/settings",
            json={"schedule_mode": "interval", "interval_hours": 2},
        )
        self.assertEqual(good.status_code, 200)
        settings = good.get_json()["settings"]
        self.assertEqual(settings["schedule_mode"], "interval")
        self.assertEqual(settings["interval_hours"], 2)
        self.assertTrue(good.get_json()["next_update"])

        bad = self.client.patch(
            "/api/sports/settings",
            json={"interval_hours": 99},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("1 to 24", bad.get_json()["error"])
        self.assertEqual(sports.get_settings(core.DB_PATH)["interval_hours"], 2)

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

    def test_provider_api_hides_url_and_credentials(self):
        fixture = """#EXTM3U
#EXTINF:-1 group-title="News",Local News
http://provider.test/news.ts
"""
        parsed = core.parse_m3u_text(fixture)
        source = {
            "id": "primary",
            "name": "Primary",
            "role": "primary",
            "priority": 0,
            "kind": "xtream",
            "url": "https://provider.test:8443",
            "username": "secret-user",
            "password": "secret-password",
            "output": "ts",
            "xtream_api": True,
            "channel_count": 1,
            "account_status": "Active",
            "expires_at": "2026-09-17T00:00:00-04:00",
        }
        core.install_primary_provider(source, fixture, parsed)

        response = self.client.get("/api/providers")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["sources"][0]
        self.assertEqual(payload["role"], "primary")
        self.assertTrue(payload["credentials_saved"])
        self.assertEqual(payload["account_status"], "Active")
        self.assertEqual(payload["expires_at"], "2026-09-17T00:00:00-04:00")
        self.assertNotIn("url", payload)
        self.assertNotIn("username", payload)
        self.assertNotIn("password", payload)

    def test_fallback_api_requires_url_primary(self):
        core.channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="News",Local News
http://provider.test/news.ts
"""
        )
        core.source_mode = "file"
        response = self.client.post(
            "/api/providers/fallback",
            json={"name": "Backup", "url": "https://backup.test/playlist.m3u"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("URL primary", response.get_json()["error"])

    def test_load_primary_accepts_separate_xtream_fields(self):
        fixture = """#EXTM3U
#EXTINF:-1 group-title="News",Local News
http://provider.test/news.ts
"""
        parsed = core.parse_m3u_text(fixture)
        detected = {
            "id": "primary",
            "name": "Primary",
            "role": "primary",
            "priority": 0,
            "kind": "xtream",
            "url": "https://provider.test:8443",
            "username": "alice",
            "password": "secret",
            "output": "ts",
            "xtream_api": True,
            "channel_count": 1,
        }
        with patch("core.detect_provider_source", return_value=(detected, fixture, parsed)) as detect:
            response = self.client.post(
                "/api/load-url",
                json={
                    "name": "Primary",
                    "url": "https://provider.test:8443",
                    "username": "alice",
                    "password": "secret",
                },
            )
        self.assertEqual(response.status_code, 200)
        detect.assert_called_once_with(
            "Primary",
            "https://provider.test:8443",
            username="alice",
            password="secret",
            role="primary",
        )

    def test_schedule_api_settings_route_never_returns_secret(self):
        response = self.client.patch(
            "/api/sports/schedule-api",
            json={
                "enabled": True,
                "api_key": "secret-key",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["schedule_api"]
        self.assertTrue(payload["effective"])
        self.assertTrue(payload["key_configured"])
        self.assertNotIn("api_key", payload)
        status = self.client.get("/api/sports/settings").get_json()
        self.assertTrue(status["schedule_api"]["key_configured"])
        self.assertNotIn("api_key", status["schedule_api"])

    def test_force_schedule_api_refresh_route_uses_explicit_force(self):
        with patch("sports.refresh_schedule_api_if_due", return_value={"enabled": True, "fetched": []}) as refresh:
            response = self.client.post("/api/sports/schedule-api/refresh")
        self.assertEqual(response.status_code, 200)
        refresh.assert_called_once_with(core.DB_PATH, force=True)
        self.assertIn("schedule_api", response.get_json())

    def test_master_switch_rewrites_served_outputs_and_restores_cached_rows(self):
        fixture = """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 23:05:00)
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



    def test_new_public_routes_work_and_old_routes_remain_compatibility_aliases(self):
        core.PLAYLIST_PATH.write_text("#EXTM3U\n", encoding="utf-8")
        core.COMBINED_EPG_PATH.write_text("<?xml version='1.0'?><tv />", encoding="utf-8")
        core.SPORTS_EPG_PATH.write_text("<?xml version='1.0'?><tv />", encoding="utf-8")

        new_playlist = self.client.get("/playlist/channels.m3u")
        old_playlist = self.client.get("/playlist/custom.m3u")
        self.assertEqual(new_playlist.status_code, 200)
        self.assertEqual(old_playlist.status_code, 200)
        self.assertIn("/epg/epg.xml", new_playlist.get_data(as_text=True).splitlines()[0])
        self.assertEqual(new_playlist.get_data(as_text=True), old_playlist.get_data(as_text=True))

        new_epg = self.client.get("/epg/epg.xml")
        old_epg = self.client.get("/epg/combined.xml")
        self.assertEqual(new_epg.status_code, 200)
        self.assertEqual(old_epg.status_code, 200)
        self.assertEqual(new_epg.data, old_epg.data)


if __name__ == "__main__":
    unittest.main()
