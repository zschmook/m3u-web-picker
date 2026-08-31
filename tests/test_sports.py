from __future__ import annotations

import os
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import core  # noqa: E402
import sports  # noqa: E402


FIXTURE = """#EXTM3U
#EXTINF:-1 tvg-id="" tvg-name="" tvg-logo="https://example.test/mlb.png" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 23:05:00)
http://provider.test/user/pass/100.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" tvg-name="MLB Philadelphia Phillies" tvg-logo="https://example.test/phillies.png" group-title="MLB / MiLB",MLB Philadelphia Phillies
http://provider.test/user/pass/200.ts
#EXTINF:-1 tvg-id="BaltimoreOrioles.mlb" tvg-name="MLB Baltimore Orioles" tvg-logo="https://example.test/orioles.png" group-title="MLB / MiLB",MLB Baltimore Orioles
http://provider.test/user/pass/201.ts
#EXTINF:-1 tvg-id="" tvg-name="" tvg-logo="" group-title="NFL",NFL 01: 1PM
http://provider.test/user/pass/300.ts
"""


class SportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "sports.db"
        self.channels = core.parse_m3u_text(FIXTURE)
        sports.init_db(self.db_path)
        sports.discover_catalog_from_channels(self.db_path, self.channels)
        sports.update_settings(
            self.db_path,
            {
                "enabled": True,
                "start_channel": 1000,
                "channels_per_event": 10,
                "timezone": "America/New_York",
                "refresh_time": "03:00",
            },
        )
        sports.add_rule(
            self.db_path,
            {
                "scope_type": "team",
                "scope_id": "mlb:philadelphia-phillies",
                "feed_preference": "favorite",
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_xtream_separate_credentials_are_detected_without_exposing_them_in_base_url(self):
        auth = b'{"user_info":{"auth":1,"status":"Active","exp_date":"1780000000"},"server_info":{}}'
        with patch("core._download_probe_bytes", return_value=auth), patch(
            "core.download_m3u_text", return_value=FIXTURE
        ):
            source, text, parsed = core.detect_provider_source(
                "Primary",
                "https://provider.test:8443/player_api.php?old=ignored",
                username="alice@example.com",
                password="p@ss word",
                role="primary",
            )

        self.assertEqual(source["kind"], "xtream")
        self.assertTrue(source["xtream_api"])
        self.assertEqual(source["url"], "https://provider.test:8443")
        self.assertNotIn("alice", source["url"])
        self.assertEqual(source["account_status"], "Active")
        self.assertTrue(str(source["expires_at"]).startswith("2026-"))
        playlist_url = core.provider_playlist_url(source)
        self.assertIn("get.php?", playlist_url)
        self.assertIn("username=alice%40example.com", playlist_url)
        self.assertIn("password=p%40ss+word", playlist_url)
        self.assertEqual(text, FIXTURE)
        self.assertEqual(len(parsed), len(self.channels))

    def test_xtream_live_api_imports_only_live_streams(self):
        auth = b'{"user_info":{"auth":1,"status":"Active"},"server_info":{}}'
        live_streams = [
            {
                "stream_id": 101,
                "stream_type": "live",
                "name": "MLB Network",
                "category_id": "7",
                "epg_channel_id": "mlb.network",
                "stream_icon": "https://provider.test/mlb.png",
                "num": 42,
            },
            {
                "stream_id": 202,
                "stream_type": "movie",
                "name": "A VOD Movie",
                "category_id": "99",
            },
        ]
        categories = [{"category_id": "7", "category_name": "US Sports"}]
        with patch("core._download_probe_bytes", return_value=auth), patch(
            "core._download_json", side_effect=[live_streams, categories]
        ):
            source, text, parsed = core.detect_provider_source(
                "Primary",
                "https://provider.test:8443",
                username="alice",
                password="secret",
                role="primary",
            )

        self.assertTrue(source["xtream_api"])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "MLB Network")
        self.assertEqual(parsed[0]["group"], "US Sports")
        self.assertIn("/live/alice/secret/101.ts", parsed[0]["url"])
        self.assertNotIn("A VOD Movie", text)

    def test_fallback_registration_defers_channel_download(self):
        auth = b'{"user_info":{"auth":1,"status":"Active"},"server_info":{}}'
        with patch("core._download_probe_bytes", return_value=auth), patch(
            "core._download_json"
        ) as download_json:
            source, text, parsed = core.detect_provider_source(
                "Backup",
                "https://provider.test:8443",
                username="alice",
                password="secret",
                role="fallback",
                load_channels=False,
            )

        self.assertTrue(source["deferred"])
        self.assertEqual(source["channel_count"], 0)
        self.assertEqual(text, "")
        self.assertEqual(parsed, [])
        download_json.assert_not_called()

    def test_oversized_combined_playlist_is_rejected_before_parsing(self):
        huge = "#EXTM3U\n" + "".join(
            f"#EXTINF:-1,Entry {index}\nhttp://provider.test/{index}.ts\n"
            for index in range(4)
        )
        with self.assertRaisesRegex(ValueError, "safety limit"):
            core.validate_m3u_text(huge, max_channels=3)

    def test_provider_payload_hides_urls_and_separate_credentials(self):
        original_sources = core.provider_sources
        original_master = core.MASTER_CACHE_PATH
        try:
            core.MASTER_CACHE_PATH = Path(self.temp.name) / "primary.m3u"
            core.provider_sources = [
                {
                    "id": "primary",
                    "name": "Primary",
                    "role": "primary",
                    "priority": 0,
                    "kind": "xtream",
                    "url": "https://secret-provider.test:8443",
                    "username": "secret-user",
                    "password": "secret-password",
                    "xtream_api": True,
                    "channel_count": 123,
                    "account_status": "Active",
                    "expires_at": "2026-09-17T00:00:00-04:00",
                }
            ]
            payload = core.provider_sources_payload()
        finally:
            core.provider_sources = original_sources
            core.MASTER_CACHE_PATH = original_master

        self.assertEqual(payload[0]["kind"], "xtream")
        self.assertTrue(payload[0]["credentials_saved"])
        self.assertEqual(payload[0]["account_status"], "Active")
        self.assertEqual(payload[0]["expires_at"], "2026-09-17T00:00:00-04:00")
        self.assertNotIn("url", payload[0])
        self.assertNotIn("username", payload[0])
        self.assertNotIn("password", payload[0])

    def test_xtream_config_keeps_legacy_source_url_free_of_credentials(self):
        config_path = Path(self.temp.name) / "config.json"
        original_config = core.CONFIG_PATH
        original_sources = core.provider_sources
        original_source_url = core.last_source_url
        original_source_mode = core.source_mode
        original_last_refresh = core.last_refresh
        original_epg_sources = core.epg_sources
        try:
            core.CONFIG_PATH = config_path
            core.provider_sources = [
                {
                    "id": "primary",
                    "name": "Primary",
                    "role": "primary",
                    "priority": 0,
                    "kind": "xtream",
                    "url": "https://provider.test:8443",
                    "username": "alice",
                    "password": "secret",
                    "output": "ts",
                }
            ]
            core.source_mode = "url"
            core.last_source_url = core.provider_playlist_url(core.provider_sources[0])
            core.last_refresh = "2026-08-03T21:00:00-04:00"
            core.epg_sources = []
            core.save_config()

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["source_url"], "https://provider.test:8443")
            self.assertNotIn("alice", saved["source_url"])
            self.assertEqual(saved["provider_sources"][0]["username"], "alice")

            core.provider_sources = []
            core.last_source_url = ""
            core.restore_config()
            self.assertEqual(core.primary_provider_source()["username"], "alice")
            self.assertIn("username=alice", core.last_source_url)
        finally:
            core.CONFIG_PATH = original_config
            core.provider_sources = original_sources
            core.last_source_url = original_source_url
            core.source_mode = original_source_mode
            core.last_refresh = original_last_refresh
            core.epg_sources = original_epg_sources

    def test_primary_provider_feed_wins_over_matching_fallback_feed(self):
        fixture = """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 23:05:00)
{url}
"""
        primary = core.parse_m3u_text(fixture.format(url="http://primary.test/game.ts"))
        fallback = core.parse_m3u_text(fixture.format(url="http://fallback.test/game.ts"))
        for channel in primary:
            channel["_provider_priority"] = 0
            channel["_provider_source_id"] = "primary"
        for channel in fallback:
            channel["_provider_priority"] = 1
            channel["_provider_source_id"] = "backup"
        db_path = Path(self.temp.name) / "provider-priority.db"
        sports.init_db(db_path)
        sports.update_settings(
            db_path,
            {
                "enabled": True,
                "everything_mode": True,
                "timezone": "America/New_York",
            },
        )
        sports.scan_channels(
            db_path,
            [*fallback, *primary],
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        generated = sports.generated_rows(db_path)
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["url"], "http://primary.test/game.ts")
        self.assertEqual(
            generated[0]["raw"][-1],
            sports.generated_stream_path(generated[0]["assigned_number"]),
        )
        self.assertEqual(
            sports.generated_stream_target(db_path, generated[0]["assigned_number"]),
            "http://primary.test/game.ts",
        )

    def test_fallback_provider_fills_event_missing_from_primary(self):
        primary = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="News",Local News
http://primary.test/news.ts
"""
        )
        fallback = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 23:05:00)
http://fallback.test/game.ts
"""
        )
        for channel in primary:
            channel["_provider_priority"] = 0
        for channel in fallback:
            channel["_provider_priority"] = 1
        db_path = Path(self.temp.name) / "provider-fill.db"
        sports.init_db(db_path)
        sports.update_settings(
            db_path,
            {
                "enabled": True,
                "everything_mode": True,
                "timezone": "America/New_York",
            },
        )
        sports.scan_channels(
            db_path,
            [*primary, *fallback],
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        generated = sports.generated_rows(db_path)
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["url"], "http://fallback.test/game.ts")
        self.assertEqual(
            generated[0]["raw"][-1],
            sports.generated_stream_path(generated[0]["assigned_number"]),
        )

    def test_fallback_xmltv_can_confirm_event_while_primary_stream_still_wins(self):
        sports.update_settings(
            self.db_path,
            {"event_window": "next_24_hours", "everything_mode": True},
        )
        primary = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="primary.event" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles
http://primary.test/game.ts
"""
        )
        fallback = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="fallback.event" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles
http://fallback.test/game.ts
"""
        )
        for channel in primary:
            channel["_provider_priority"] = 0
            channel["_provider_source_id"] = "primary"
        for channel in fallback:
            channel["_provider_priority"] = 1
            channel["_provider_source_id"] = "backup"

        fallback_epg = Path(self.temp.name) / "fallback-provider.xml"
        fallback_epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="fallback.event"><display-name>MLB Event</display-name></channel>
  <programme channel="fallback.event" start="20260803190000 -0400" stop="20260803230000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )

        result = sports.scan_channels(
            self.db_path,
            [*primary, *fallback],
            provider_epg_sources=[(fallback_epg, fallback)],
            now=datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        generated = sports.generated_rows(self.db_path)
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["untimed_skipped"], 0)
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["url"], "http://primary.test/game.ts")


    def test_channel_manager_lists_manual_channels_before_generated_channels(self):
        manual = [
            {"id": 1, "name": "Local News", "url": "http://provider.test/news"},
            {"id": 2, "name": "Weather", "url": "http://provider.test/weather"},
        ]
        generated = [
            {"id": -1, "name": "Generated Game", "is_sports_generated": True},
        ]
        original_channels = core.channels
        try:
            core.channels = manual
            with patch("core.sports.generated_channel_payloads", return_value=generated):
                combined = core.combined_channels_for_api()
        finally:
            core.channels = original_channels

        self.assertEqual([row["id"] for row in combined], [1, 2, -1])

    def test_manual_and_generated_channels_with_same_stream_both_remain_in_playlist(self):
        shared_url = "http://provider.test/user/pass/shared.ts"
        manual = core.parse_m3u_text(
            f"""#EXTM3U
#EXTINF:-1 tvg-id=\"NBCSportsPhilly.provider\" group-title=\"US-SPORTS\",US-S: NBC SPORTS PHILLY
{shared_url}
"""
        )
        generated_url = sports.generated_stream_path(1040)
        generated = [
            {
                "raw": [
                    '#EXTINF:-1 tvg-id="m3u-picker-sports-1040" tvg-chno="1040" group-title="Sports Today",MLB Phillies Feed',
                    generated_url,
                ]
            }
        ]
        db_path = Path(self.temp.name) / "manual-generated.db"
        playlist_path = Path(self.temp.name) / "manual-generated.m3u"
        with patch.object(core, "DB_PATH", db_path), patch.object(
            core, "PLAYLIST_PATH", playlist_path
        ), patch.object(core, "channels", manual), patch.object(
            core, "selected_ids", {0}
        ), patch("core.sports.generated_rows", return_value=generated):
            count = core.write_current_playlist()

        text = playlist_path.read_text(encoding="utf-8")
        self.assertEqual(count, 2)
        self.assertEqual(text.count(shared_url), 1)
        self.assertEqual(text.count(generated_url), 1)
        self.assertIn('tvg-id="NBCSportsPhilly.provider"', text)
        self.assertIn('tvg-id="m3u-picker-sports-1040"', text)

    def test_manual_rows_sharing_a_stream_url_keep_separate_saved_identities(self):
        shared_url = "http://provider.test/user/pass/shared.ts"
        manual = core.parse_m3u_text(
            f"""#EXTM3U
#EXTINF:-1 tvg-id=\"network.fulltime\" tvg-chno=\"22\" group-title=\"US-SPORTS\",Full-time Network
{shared_url}
#EXTINF:-1 tvg-id=\"network.alternate\" tvg-chno=\"222\" group-title=\"US-SPORTS\",Alternate Network Entry
{shared_url}
"""
        )
        db_path = Path(self.temp.name) / "manual-identity.db"
        playlist_path = Path(self.temp.name) / "manual-identity.m3u"
        with patch.object(core, "DB_PATH", db_path), patch.object(
            core, "PLAYLIST_PATH", playlist_path
        ), patch.object(core, "channels", manual), patch.object(
            core, "selected_ids", {0, 1}
        ), patch("core.sports.generated_rows", return_value=[]):
            count = core.write_current_playlist()
            keys = core.load_selected_keys_from_db()

        text = playlist_path.read_text(encoding="utf-8")
        self.assertEqual(count, 2)
        self.assertEqual(text.count(shared_url), 2)
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(key.startswith("manual:") for key in keys))
        self.assertNotEqual(core.channel_key(manual[0]), core.channel_key(manual[1]))

    def test_legacy_url_selection_migrates_without_losing_manual_channel(self):
        manual = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id=\"NBCSportsPhilly.provider\" group-title=\"US-SPORTS\",US-S: NBC SPORTS PHILLY
http://provider.test/user/pass/philly.ts
"""
        )
        db_path = Path(self.temp.name) / "legacy-selection.db"
        with patch.object(core, "DB_PATH", db_path), patch.object(
            core, "channels", manual
        ), patch.object(core, "selected_ids", set()):
            conn = core.db_connect()
            try:
                conn.execute(
                    """
                    INSERT INTO selections (key, name, group_title, url, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        manual[0]["url"],
                        manual[0]["name"],
                        manual[0]["group"],
                        manual[0]["url"],
                        0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            core.apply_saved_selections_to_loaded_channels()
            keys = core.load_selected_keys_from_db()
            selected = set(core.selected_ids)

        self.assertEqual(selected, {0})
        self.assertEqual(keys, {core.channel_key(manual[0])})

    def test_before_refresh_uses_previous_sports_day(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        self.assertEqual(result["target_date"], "2026-08-01")
        self.assertEqual(result["events"], 1)

    def test_phillies_rule_builds_away_event_and_home_feeds(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        self.assertEqual(result["count"], 3)
        rows = sports.generated_rows(self.db_path)
        self.assertEqual([row["assigned_number"] for row in rows], [1000, 1001, 1002])
        self.assertEqual([row["feed_type"] for row in rows], ["away", "event", "home"])
        self.assertIn("Away broadcast", rows[0]["subtitle"])
        self.assertIn('x-sports-subtitle="Away broadcast', rows[0]["raw"][0])
        self.assertIn('tvg-logo="https://a.espncdn.com/i/teamlogos/mlb/500/phi.png"', rows[0]["raw"][0])

    def test_build_feeds_prefers_team_aware_candidate_for_same_url(self):
        shared = {
            "name": "MLB Philadelphia Phillies",
            "tvg_name": "MLB Philadelphia Phillies",
            "group": "MLB / MiLB",
            "url": "http://provider.test/user/pass/shared.ts",
            "_provider_priority": 0,
        }
        event = {
            "away_team_id": "mlb:toronto-blue-jays",
            "away_team_name": "Toronto Blue Jays",
            "home_team_id": "mlb:philadelphia-phillies",
            "home_team_name": "Philadelphia Phillies",
            "source_channels": [shared],
            "expanded_feeds": True,
        }
        team_feed_map = {"mlb:philadelphia-phillies": [shared]}
        rule = {
            "scope_type": "team",
            "scope_id": "mlb:philadelphia-phillies",
            "feed_preference": "favorite",
        }

        feeds = sports._build_feeds(event, team_feed_map, rule, {"use_backup_feeds": False})

        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]["feed_type"], "home")
        self.assertEqual(feeds[0]["team_id"], "mlb:philadelphia-phillies")

    def test_team_feed_logo_prefers_schedule_api_team_artwork(self):
        event = {
            "home_team_id": "mlb:philadelphia-phillies",
            "away_team_id": "mlb:toronto-blue-jays",
            "api_home_logo": "https://media.api-sports.io/baseball/teams/27.png",
            "api_away_logo": "https://media.api-sports.io/baseball/teams/36.png",
        }
        feed = {"feed_type": "home", "team_id": "mlb:philadelphia-phillies"}
        channel = {"tvg_logo": "http://provider.test/network-logo.png"}
        logo = sports._preferred_feed_logo(event, feed, channel, {})
        self.assertEqual(logo, "https://media.api-sports.io/baseball/teams/27.png")

        event_feed = {"feed_type": "event", "team_id": ""}
        event_logo = sports._preferred_feed_logo(event, event_feed, channel, {})
        self.assertEqual(event_logo, "https://media.api-sports.io/baseball/teams/36.png")

    def test_city_only_matchup_still_registers_an_event_logo(self):
        event = {
            "event_key": "2026-08-23:mlb-los-angeles-boston:1930",
            "league_id": "mlb",
            "away_team_id": "",
            "away_team_name": "Los Angeles",
            "home_team_id": "",
            "home_team_name": "Boston",
        }
        feed = {"feed_type": "national", "team_id": ""}
        with patch(
            "sports.feeds.event_logos.register_matchup_logo",
            return_value="http://picker.test/api/event-logo/city-only.png",
        ) as register:
            logo = sports._preferred_feed_logo(event, feed, {}, {})

        self.assertEqual(logo, "http://picker.test/api/event-logo/city-only.png")
        register.assert_called_once()
        self.assertEqual(register.call_args.kwargs["away_team_id"], "")
        self.assertEqual(register.call_args.kwargs["home_team_id"], "")

    def test_effective_sports_start_moves_above_manual_number_range(self):
        self.assertEqual(sports.effective_start_channel(1000, 999), 1000)
        self.assertEqual(sports.effective_start_channel(1000, 1000), 2000)
        self.assertEqual(sports.effective_start_channel(1000, 1021), 2000)
        self.assertEqual(sports.effective_start_channel(1500, 1600), 2500)

    def test_scan_auto_shifts_sports_slots_above_large_manual_lineup(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            now=now,
            trigger="test",
            manual_channel_count=1021,
        )
        rows = sports.generated_rows(self.db_path)
        self.assertEqual(result["numbering"]["configured_start_channel"], 1000)
        self.assertEqual(result["numbering"]["effective_start_channel"], 2000)
        self.assertTrue(result["numbering"]["auto_shifted"])
        self.assertEqual([row["assigned_number"] for row in rows], [2000, 2001, 2002])
        ids = [row["tvg_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(value.startswith("m3u-picker-sports-") for value in ids))

    def test_generated_m3u_ids_match_synthetic_xmltv_guide(self):
        sports_epg = Path(self.temp.name) / "sports.xml"
        combined_epg = Path(self.temp.name) / "combined.xml"
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=now,
            trigger="test",
        )
        self.assertEqual(result["guide_channels"], 3)
        rows = sports.generated_rows(self.db_path)
        ids = [row["tvg_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(value.startswith("m3u-picker-sports-") for value in ids))
        for row in rows:
            self.assertIn(f'tvg-id="{row["tvg_id"]}"', row["raw"][0])

        root = ElementTree.parse(sports_epg).getroot()
        xml_ids = {node.attrib["id"] for node in root.findall("channel")}
        programme_ids = {node.attrib["channel"] for node in root.findall("programme")}
        self.assertEqual(xml_ids, set(ids))
        self.assertEqual(programme_ids, set(ids))
        titles = [node.findtext("title", default="") for node in root.findall("programme")]
        self.assertTrue(any(title.startswith("Upcoming:") for title in titles))
        self.assertTrue(any("Philadelphia Phillies at Baltimore Orioles" in title for title in titles))

    def test_combined_xmltv_preserves_provider_guide_and_adds_sports(self):
        base_epg = Path(self.temp.name) / "provider.xml"
        base_epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv><channel id="normal"><display-name>Normal Channel</display-name></channel>
<programme channel="normal" start="20260801000000 -0400" stop="20260802000000 -0400"><title>Normal Show</title></programme></tv>""",
            encoding="utf-8",
        )
        sports_epg = Path(self.temp.name) / "sports.xml"
        combined_epg = Path(self.temp.name) / "combined.xml"
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(
            self.db_path,
            self.channels,
            base_epg,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=now,
            trigger="test",
        )
        root = ElementTree.parse(combined_epg).getroot()
        ids = {node.attrib["id"] for node in root.findall("channel")}
        self.assertIn("normal", ids)
        self.assertTrue(any(value.startswith("m3u-picker-sports-") for value in ids))
        titles = [node.findtext("title", default="") for node in root.findall("programme")]
        self.assertIn("Normal Show", titles)
        self.assertTrue(any("Phillies" in title for title in titles))

        # XMLTV's content model requires every channel definition before the
        # first programme. Jellyfin can ignore generated guide mappings when a
        # combined file appends sports channels after provider programmes.
        child_tags = [child.tag.rsplit("}", 1)[-1] for child in root]
        first_programme = child_tags.index("programme")
        self.assertNotIn("channel", child_tags[first_programme:])

        first_pitch = datetime(2026, 8, 1, 23, 5, tzinfo=ZoneInfo("America/New_York"))
        for row in sports.generated_rows(self.db_path):
            covering = []
            for programme in root.findall(f"programme[@channel='{row['tvg_id']}']"):
                start = sports._parse_xmltv_time(programme.attrib["start"], first_pitch.tzinfo)
                stop = sports._parse_xmltv_time(programme.attrib["stop"], first_pitch.tzinfo)
                if start <= first_pitch < stop:
                    covering.append(programme)
            self.assertEqual(len(covering), 1)

    def test_combined_xmltv_filters_provider_to_selected_manual_ids(self):
        base_epg = Path(self.temp.name) / "provider-large.xml"
        base_epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="Provider">
  <channel id="keep"><display-name>Saved Channel</display-name></channel>
  <channel id="drop"><display-name>Unselected Channel</display-name></channel>
  <programme channel="keep" start="20260801000000 -0400" stop="20260802000000 -0400"><title>Keep Me</title></programme>
  <programme channel="drop" start="20260801000000 -0400" stop="20260802000000 -0400"><title>Drop Me</title></programme>
</tv>""",
            encoding="utf-8",
        )
        sports_epg = Path(self.temp.name) / "sports-filtered.xml"
        combined_epg = Path(self.temp.name) / "combined-filtered.xml"
        sports.scan_channels(
            self.db_path,
            self.channels,
            base_epg,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            base_channel_ids={"keep"},
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        root = ElementTree.parse(combined_epg).getroot()
        channel_ids = {node.attrib["id"] for node in root.findall("channel")}
        titles = [node.findtext("title", default="") for node in root.findall("programme")]
        self.assertIn("keep", channel_ids)
        self.assertNotIn("drop", channel_ids)
        self.assertIn("Keep Me", titles)
        self.assertNotIn("Drop Me", titles)
        self.assertTrue(any(value.startswith("m3u-picker-sports-") for value in channel_ids))

    def test_finished_epg_event_is_not_generated_as_a_dead_channel(self):
        epg = Path(self.temp.name) / "finished-event.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.finished"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.finished" start="20260803120000 -0400" stop="20260803160000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        channels = [
            {
                "id": 0,
                "name": "MLB Event",
                "group": "MLB / MiLB",
                "url": "http://provider.test/user/pass/finished.ts",
                "raw": [
                    '#EXTINF:-1 tvg-id="mlb.finished" group-title="MLB / MiLB",MLB Event',
                    "http://provider.test/user/pass/finished.ts",
                ],
                "tvg_id": "mlb.finished",
                "tvg_name": "MLB Event",
                "tvg_logo": "",
            },
            *self.channels[1:3],
        ]
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 3, 18, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(sports.generated_rows(self.db_path), [])

    def test_next_24_hours_keeps_game_already_in_progress(self):
        sports.update_settings(self.db_path, {"event_window": "next_24_hours"})
        epg = Path(self.temp.name) / "live-next-24.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.live"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.live" start="20260803180000 -0400" stop="20260803220000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        channels = [
            {
                "id": 0,
                "name": "MLB Event",
                "group": "MLB / MiLB",
                "url": "http://provider.test/user/pass/live.ts",
                "raw": [
                    '#EXTINF:-1 tvg-id="mlb.live" group-title="MLB / MiLB",MLB Event',
                    "http://provider.test/user/pass/live.ts",
                ],
                "tvg_id": "mlb.live",
                "tvg_name": "MLB Event",
                "tvg_logo": "",
            },
            *self.channels[1:3],
        ]
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertGreaterEqual(result["count"], 1)

    def test_game_crossing_refresh_boundary_remains_live(self):
        epg = Path(self.temp.name) / "boundary-live.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.boundary"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.boundary" start="20260804010000 -0400" stop="20260804050000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        channels = [
            {
                "id": 0,
                "name": "MLB Event",
                "group": "MLB / MiLB",
                "url": "http://provider.test/user/pass/boundary.ts",
                "raw": [
                    '#EXTINF:-1 tvg-id="mlb.boundary" group-title="MLB / MiLB",MLB Event',
                    "http://provider.test/user/pass/boundary.ts",
                ],
                "tvg_id": "mlb.boundary",
                "tvg_name": "MLB Event",
                "tvg_logo": "",
            },
            *self.channels[1:3],
        ]
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 4, 3, 5, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)

    def test_embedded_start_uses_estimated_duration_for_live_window(self):
        sports.update_settings(self.db_path, {"event_window": "next_24_hours"})
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-03 19:00:00)
http://provider.test/user/pass/estimated.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" group-title="MLB / MiLB",MLB Philadelphia Phillies
http://provider.test/user/pass/200.ts
#EXTINF:-1 tvg-id="BaltimoreOrioles.mlb" group-title="MLB / MiLB",MLB Baltimore Orioles
http://provider.test/user/pass/201.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            now=datetime(2026, 8, 3, 21, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertGreaterEqual(result["count"], 1)

    def test_estimated_event_expires_at_end_plus_grace_boundary(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-03 12:00:00)
http://provider.test/user/pass/grace.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        still_live = sports.scan_channels(
            self.db_path,
            channels,
            now=datetime(2026, 8, 3, 17, 29, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(still_live["events"], 1)

        expired = sports.scan_channels(
            self.db_path,
            channels,
            now=datetime(2026, 8, 3, 17, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(expired["events"], 0)
        self.assertEqual(expired["count"], 0)

    def test_untimed_m3u_event_without_epg_confirmation_is_skipped(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles
http://provider.test/user/pass/untimed.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            now=datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["untimed_skipped"], 1)
        self.assertIn("without XMLTV schedule confirmation", result["message"])

    def test_untimed_summary_only_counts_events_matching_enabled_rules(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles
http://provider.test/user/pass/untimed-mlb.ts
#EXTINF:-1 group-title="RACING",F1-TV FHD
http://provider.test/user/pass/untimed-f1.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            now=datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["untimed_skipped"], 1)
        self.assertIn("Skipped 1 untimed provider event", result["message"])

    def test_untimed_m3u_event_is_kept_when_xmltv_supplies_timing(self):
        sports.update_settings(self.db_path, {"event_window": "next_24_hours"})
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="mlb.corroborated" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles
http://provider.test/user/pass/corroborated.ts
"""
        )
        epg = Path(self.temp.name) / "corroborated.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.corroborated"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.corroborated" start="20260803190000 -0400" stop="20260803230000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["untimed_skipped"], 0)

    def test_same_day_doubleheader_remains_two_distinct_events(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="mlb.doubleheader" group-title="MLB / MiLB",MLB Event
http://provider.test/user/pass/doubleheader.ts
"""
        )
        epg = Path(self.temp.name) / "doubleheader.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.doubleheader"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.doubleheader" start="20260803130000 -0400" stop="20260803170000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
  <programme channel="mlb.doubleheader" start="20260803190000 -0400" stop="20260803230000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 2)
        event_keys = {row["event_key"] for row in sports.generated_rows(self.db_path)}
        self.assertEqual(len(event_keys), 2)

    def test_near_duplicate_m3u_and_xmltv_times_merge_as_one_event(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="mlb.near" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-03 13:05:00)
http://provider.test/user/pass/near.ts
"""
        )
        epg = Path(self.temp.name) / "near.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.near"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.near" start="20260803131000 -0400" stop="20260803171000 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>""",
            encoding="utf-8",
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        starts = {row["event_start"] for row in sports.generated_rows(self.db_path)}
        self.assertEqual(starts, {"2026-08-03T13:10:00-04:00"})

    def test_cancelled_scan_keeps_existing_generated_output(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        before = sports.generated_rows(self.db_path)
        self.assertEqual(len(before), 3)
        with self.assertRaises(sports.ScanCancelled):
            sports.scan_channels(
                self.db_path,
                self.channels,
                now=now,
                trigger="manual",
                cancel_check=lambda: True,
            )
        after = sports.generated_rows(self.db_path)
        self.assertEqual(
            [(row["channel_key"], row["generated_at"]) for row in after],
            [(row["channel_key"], row["generated_at"]) for row in before],
        )

    def test_zero_event_scan_writes_valid_empty_sports_guide(self):
        sports_epg = Path(self.temp.name) / "sports-empty.xml"
        combined_epg = Path(self.temp.name) / "combined-empty.xml"
        after = datetime(2026, 8, 2, 5, 0, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=after,
            trigger="test",
        )
        self.assertEqual(result["count"], 0)
        root = ElementTree.parse(sports_epg).getroot()
        self.assertEqual(root.tag, "tv")
        self.assertEqual(root.findall("channel"), [])
        self.assertEqual(root.findall("programme"), [])

    def test_empty_successful_scan_removes_old_generated_channels(self):
        before = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=before, trigger="test")
        self.assertEqual(len(sports.generated_rows(self.db_path)), 3)

        after = datetime(2026, 8, 2, 5, 0, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, self.channels, now=after, trigger="test")
        self.assertEqual(result["count"], 0)
        self.assertEqual(sports.generated_rows(self.db_path), [])

    def test_generic_nfl_slot_is_not_treated_as_a_game(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        names = [row["display_name"] for row in sports.generated_rows(self.db_path)]
        self.assertFalse(any("NFL 01" in name for name in names))

    def test_explicit_sport_does_not_borrow_team_ids_from_another_sport(self):
        conn = sports._connect(self.db_path)
        try:
            sports._upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id="nfl:detroit-lions",
                display_name="Detroit Lions",
                subtitle="NFL team",
                league_id="nfl",
                aliases=["Detroit Lions", "Lions"],
                logo_url="https://example.test/lions.png",
                metadata={"sport_id": "football"},
                source="test",
            )
            sports._upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id="nfl:philadelphia-eagles",
                display_name="Philadelphia Eagles",
                subtitle="NFL team",
                league_id="nfl",
                aliases=["Philadelphia Eagles", "Eagles"],
                logo_url="https://example.test/eagles.png",
                metadata={"sport_id": "football"},
                source="test",
            )
            conn.commit()
        finally:
            conn.close()

        settings = sports.get_settings(self.db_path)
        now = datetime(2026, 8, 29, 20, 0, tzinfo=ZoneInfo("America/New_York"))
        event = sports._event_from_text(
            self.db_path,
            {"name": "Sports Event", "group_title": "Sports"},
            "Baseball • Detroit Lions at Philadelphia Eagles",
            settings,
            now,
            forced_start=now,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["sport_id"], "baseball")
        self.assertEqual(event["away_team_id"], "")
        self.assertEqual(event["home_team_id"], "")
        self.assertEqual(
            sports._matching_rules(event, sports.get_rules(self.db_path)),
            [],
        )

    def test_clear_golf_off_air_titles_are_filtered_without_dropping_real_programming(self):
        channel = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="golf.channel" group-title="PGA Tour",Golf Channel
http://provider.test/live/golf.ts
"""
        )[0]
        now = datetime(2026, 8, 3, 18, 0, tzinfo=ZoneInfo("America/New_York"))
        settings = sports.get_settings(self.db_path)

        for title in (
            "No EVENT Today",
            "No Events Today!",
            "No Game Today",
            "PGA Tour — Signing-Off",
        ):
            with self.subTest(title=title):
                self.assertIsNone(
                    sports._event_from_text(
                        self.db_path,
                        channel,
                        title,
                        settings,
                        now,
                        forced_start=now,
                    )
                )

        podcast = sports._event_from_text(
            self.db_path,
            channel,
            "Golf Channel Podcast With Rex & Lav",
            settings,
            now,
            forced_start=now,
        )
        self.assertIsNotNone(podcast)
        self.assertEqual(podcast["display_name"], "Golf Channel Podcast With Rex & Lav")

    def test_malformed_m3u_timestamp_is_skipped_without_aborting_scan(self):
        malformed = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 99) | Bad Team @ Worse Team (2026-08-01 99:99:00)
http://provider.test/user/pass/bad.ts
"""
        )
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            self.db_path,
            [*self.channels, *malformed],
            now=now,
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["skipped_entries"], 1)
        self.assertEqual(result["malformed_m3u"], 1)
        self.assertIn("Skipped 1 malformed provider entry", result["message"])


    def test_scheduled_scan_runs_after_pre_boundary_manual_scan(self):
        before = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=before, trigger="manual")
        at_refresh = datetime(2026, 8, 2, 3, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertTrue(sports.should_run_scheduled(self.db_path, at_refresh))

    def test_interval_schedule_uses_last_completed_attempt_as_anchor(self):
        sports.update_settings(
            self.db_path,
            {"schedule_mode": "interval", "interval_hours": 2},
        )
        finished = datetime(2026, 8, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("sports._now_iso", return_value=finished.isoformat()):
            sports._record_scan(
                self.db_path,
                started_at=(finished - timedelta(minutes=4)).isoformat(),
                status="success",
                message="ok",
                event_count=1,
                channel_count=3,
                target_date="2026-08-02",
                trigger="manual",
            )

        self.assertEqual(
            sports.next_update_at(self.db_path, finished + timedelta(minutes=30)),
            finished + timedelta(hours=2),
        )
        self.assertFalse(
            sports.should_run_scheduled(self.db_path, finished + timedelta(hours=1, minutes=59))
        )
        self.assertTrue(
            sports.should_run_scheduled(self.db_path, finished + timedelta(hours=2))
        )

    def test_failed_interval_scan_waits_the_full_interval_before_retrying(self):
        sports.update_settings(
            self.db_path,
            {"schedule_mode": "interval", "interval_hours": 3},
        )
        finished = datetime(2026, 8, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("sports._now_iso", return_value=finished.isoformat()):
            sports._record_scan(
                self.db_path,
                started_at=(finished - timedelta(minutes=1)).isoformat(),
                status="failed",
                message="provider unavailable",
                event_count=0,
                channel_count=0,
                target_date="2026-08-02",
                trigger="scheduled",
            )

        self.assertFalse(
            sports.should_run_scheduled(self.db_path, finished + timedelta(hours=2, minutes=59))
        )
        self.assertTrue(
            sports.should_run_scheduled(self.db_path, finished + timedelta(hours=3))
        )

    def test_interval_schedule_without_a_scan_uses_a_stable_settings_anchor(self):
        changed = datetime(2026, 8, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("sports._now_iso", return_value=changed.isoformat()):
            sports.update_settings(
                self.db_path,
                {"schedule_mode": "interval", "interval_hours": 2},
            )
        first = sports.next_update_at(self.db_path, changed + timedelta(minutes=10))
        second = sports.next_update_at(self.db_path, changed + timedelta(minutes=40))
        self.assertEqual(first, changed + timedelta(hours=2))
        self.assertEqual(second, first)

    def test_interval_schedule_validation_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "1 to 24"):
            sports.update_settings(self.db_path, {"interval_hours": 0})
        with self.assertRaisesRegex(ValueError, "1 to 24"):
            sports.update_settings(self.db_path, {"interval_hours": 25})
        with self.assertRaisesRegex(ValueError, "Daily or Every X hours"):
            sports.update_settings(self.db_path, {"schedule_mode": "weekly"})

    def test_failed_scheduled_scan_is_not_retried_twice_in_same_minute(self):
        at_refresh = datetime(2026, 8, 2, 3, 0, 5, tzinfo=ZoneInfo("America/New_York"))
        sports._record_scan(
            self.db_path,
            started_at=at_refresh.isoformat(),
            status="failed",
            message="provider unavailable",
            event_count=0,
            channel_count=0,
            target_date="2026-08-02",
            trigger="scheduled",
        )
        same_minute = datetime(2026, 8, 2, 3, 0, 40, tzinfo=ZoneInfo("America/New_York"))
        self.assertFalse(sports.should_run_scheduled(self.db_path, same_minute))

    def test_xmltv_can_supply_event_title_and_time(self):
        epg = Path(self.temp.name) / "epg.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.event"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.event" start="20260801230500 -0400" stop="20260802030500 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title>
    <category>MLB</category>
  </programme>
</tv>
""",
            encoding="utf-8",
        )
        channels = [
            {
                "id": 0,
                "name": "MLB Event",
                "group": "MLB / MiLB",
                "url": "http://provider.test/user/pass/500.ts",
                "raw": [
                    '#EXTINF:-1 tvg-id="mlb.event" group-title="MLB / MiLB",MLB Event',
                    "http://provider.test/user/pass/500.ts",
                ],
                "tvg_id": "mlb.event",
                "tvg_name": "MLB Event",
                "tvg_logo": "",
            },
            *self.channels[1:3],
        ]
        sports.discover_catalog_from_channels(self.db_path, channels)
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, channels, epg, now=now, trigger="test")
        self.assertEqual(result["events"], 1)
        self.assertGreaterEqual(result["count"], 1)

    def test_malformed_xmltv_program_is_skipped_and_later_programs_continue(self):
        epg = Path(self.temp.name) / "malformed-epg.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.event"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.event" start="20260801990500 -0400">
    <title>Bad Team at Worse Team</title><category>MLB</category>
  </programme>
  <programme channel="mlb.event" start="20260801230500 -0400" stop="20260802030500 -0400">
    <title>Philadelphia Phillies at Baltimore Orioles</title><category>MLB</category>
  </programme>
</tv>
""",
            encoding="utf-8",
        )
        channels = [
            {
                "id": 0,
                "name": "MLB Event",
                "group": "MLB / MiLB",
                "url": "http://provider.test/user/pass/500.ts",
                "raw": [
                    '#EXTINF:-1 tvg-id="mlb.event" group-title="MLB / MiLB",MLB Event',
                    "http://provider.test/user/pass/500.ts",
                ],
                "tvg_id": "mlb.event",
                "tvg_name": "MLB Event",
                "tvg_logo": "",
            },
            *self.channels[1:3],
        ]
        sports.discover_catalog_from_channels(self.db_path, channels)
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg,
            now=now,
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual(result["malformed_epg"], 1)
        self.assertEqual(result["skipped_entries"], 1)

    def test_fresh_database_has_no_assumed_sports_rules(self):
        fresh = Path(self.temp.name) / "fresh.db"
        sports.init_db(fresh)
        self.assertEqual(sports.get_rules(fresh), [])
        self.assertFalse(sports.get_settings(fresh)["everything_mode"])

    def test_everything_mode_matches_events_without_replacing_curated_rules(self):
        curated_before = sports.get_rules(self.db_path)
        self.assertEqual(len(curated_before), 1)
        sports.update_settings(self.db_path, {"everything_mode": True})
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertTrue(result["everything_mode"])
        self.assertEqual(result["events"], 1)
        self.assertEqual(sports.get_rules(self.db_path), curated_before)

        sports.update_settings(self.db_path, {"everything_mode": False})
        self.assertEqual(sports.get_rules(self.db_path), curated_before)

    def test_everything_mode_can_generate_with_zero_curated_rules(self):
        for rule in sports.get_rules(self.db_path):
            sports.delete_rule(self.db_path, rule["id"])
        sports.update_settings(self.db_path, {"everything_mode": True})
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(sports.get_rules(self.db_path), [])

    def test_scan_records_supplied_full_update_start_time(self):
        started = "2026-08-02T14:55:00-04:00"
        sports.scan_channels(
            self.db_path,
            self.channels,
            now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
            started_at=started,
        )
        self.assertEqual(sports.last_scan(self.db_path)["started_at"], started)

    def test_scan_state_survives_status_reads_and_clears_on_finish(self):
        started = "2026-08-02T15:00:00-04:00"
        state = sports.begin_scan_state(
            self.db_path,
            trigger="manual",
            started_at=started,
            stage="Refreshing provider playlist",
        )
        self.assertTrue(state["running"])
        self.assertEqual(state["started_at"], started)
        sports.update_scan_stage(self.db_path, "Scanning and matching channels")
        payload = sports.status_payload(
            self.db_path,
            datetime(2026, 8, 2, 15, 2, tzinfo=ZoneInfo("America/New_York")),
        )
        self.assertTrue(payload["scan"]["running"])
        self.assertEqual(payload["scan"]["stage"], "Scanning and matching channels")
        self.assertEqual(payload["scan"]["elapsed_seconds"], 120)
        sports.finish_scan_state(self.db_path)
        self.assertFalse(sports.scan_state(self.db_path)["running"])

    def test_interrupted_scan_is_converted_to_persistent_failure(self):
        sports.begin_scan_state(
            self.db_path,
            trigger="manual",
            started_at="2026-08-02T15:00:00-04:00",
            stage="Scanning and matching channels",
        )
        self.assertTrue(sports.recover_interrupted_scan(self.db_path))
        self.assertFalse(sports.scan_state(self.db_path)["running"])
        last = sports.last_scan(self.db_path)
        self.assertEqual(last["status"], "failed")
        self.assertIn("interrupted", last["message"])

    def test_removing_last_rule_stays_empty_after_reinitialization(self):
        rules = sports.get_rules(self.db_path)
        self.assertEqual(len(rules), 1)
        sports.delete_rule(self.db_path, rules[0]["id"])
        sports.init_db(self.db_path)
        self.assertEqual(sports.get_rules(self.db_path), [])

    def test_refresh_time_is_canonical_and_invalid_value_is_rejected(self):
        settings = sports.update_settings(self.db_path, {"refresh_time": "3:45 AM"})
        self.assertEqual(settings["refresh_time"], "03:45")
        with self.assertRaisesRegex(ValueError, "valid time"):
            sports.update_settings(self.db_path, {"refresh_time": "27:99"})
        self.assertEqual(sports.get_settings(self.db_path)["refresh_time"], "03:45")

    def test_upgrade_removes_only_the_untouched_v20_1_demo_set(self):
        legacy = Path(self.temp.name) / "legacy.db"
        sports.init_db(legacy)
        timestamp = "2026-08-02T02:00:00-04:00"
        with sports.closing(sports._connect(legacy)) as conn:
            conn.execute(
                "DELETE FROM sports_settings WHERE key = 'migration_removed_v20_1_demo_rules'"
            )
            for scope_type, scope_id in sports.LEGACY_DEMO_RULES:
                conn.execute(
                    """
                    INSERT INTO sports_rules
                        (scope_type, scope_id, display_name, feed_preference,
                         enabled, created_at, updated_at)
                    VALUES (?, ?, ?, 'best', 1, ?, ?)
                    """,
                    (scope_type, scope_id, scope_id, timestamp, timestamp),
                )
            conn.commit()
        sports.init_db(legacy)
        self.assertEqual(sports.get_rules(legacy), [])


    def test_mlb_and_milb_are_independent_league_rules(self):
        db_path = Path(self.temp.name) / "baseball-leagues.db"
        baseball_channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB / MiLB",(MLB 01) | Philadelphia Phillies @ Baltimore Orioles (2026-08-02 13:35:00)
http://provider.test/user/pass/mlb.ts
#EXTINF:-1 group-title="MLB / MiLB",US (MiLB 001) | Lehigh Valley IronPigs @ Norfolk Tides (2026-08-02 13:05:00)
http://provider.test/user/pass/milb.ts
"""
        )
        sports.init_db(db_path)
        sports.update_settings(
            db_path,
            {
                "enabled": True,
                "timezone": "America/New_York",
                "refresh_time": "03:00",
            },
        )
        league_ids = {
            item["id"]
            for item in sports.catalog_payload(db_path, scope_type="league")
        }
        self.assertIn("mlb", league_ids)
        self.assertIn("milb", league_ids)

        sports.add_rule(
            db_path,
            {"scope_type": "league", "scope_id": "mlb", "feed_preference": "best"},
        )
        now = datetime(2026, 8, 2, 4, 50, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(db_path, baseball_channels, now=now, trigger="test")
        rows = sports.generated_rows(db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["league_id"], "mlb")
        self.assertIn("Phillies", rows[0]["display_name"])
        self.assertNotIn("IronPigs", rows[0]["display_name"])

        for rule in sports.get_rules(db_path):
            sports.delete_rule(db_path, rule["id"])
        sports.add_rule(
            db_path,
            {"scope_type": "league", "scope_id": "milb", "feed_preference": "best"},
        )
        sports.scan_channels(db_path, baseball_channels, now=now, trigger="test")
        rows = sports.generated_rows(db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["league_id"], "milb")
        self.assertIn("IronPigs", rows[0]["display_name"])
        self.assertNotIn("Phillies", rows[0]["display_name"])

    def test_league_only_games_get_one_feed_but_selected_team_games_expand_without_duplicates(self):
        db_path = Path(self.temp.name) / "league-team-overlap.db"
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="MLB",(MLB 1) | Washington Nationals @ Philadelphia Phillies (2026-08-04 18:40:00)
http://provider.test/events/nats-phillies.ts
#EXTINF:-1 tvg-id="WashingtonNationals.mlb" group-title="MLB",MLB Washington Nationals
http://provider.test/teams/nationals.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" group-title="MLB",MLB Philadelphia Phillies
http://provider.test/teams/phillies.ts
#EXTINF:-1 group-title="MLB",(MLB 2) | New York Yankees @ Baltimore Orioles (2026-08-04 19:05:00)
http://provider.test/events/yankees-orioles.ts
#EXTINF:-1 tvg-id="NewYorkYankees.mlb" group-title="MLB",MLB New York Yankees
http://provider.test/teams/yankees.ts
#EXTINF:-1 tvg-id="BaltimoreOrioles.mlb" group-title="MLB",MLB Baltimore Orioles
http://provider.test/teams/orioles.ts
"""
        )
        sports.init_db(db_path)
        sports.discover_catalog_from_channels(db_path, channels)
        sports.update_settings(
            db_path,
            {
                "enabled": True,
                "timezone": "America/New_York",
                "event_window": "next_24_hours",
            },
        )
        sports.add_rule(
            db_path,
            {"scope_type": "league", "scope_id": "mlb", "feed_preference": "all"},
        )
        sports.add_rule(
            db_path,
            {
                "scope_type": "team",
                "scope_id": "mlb:philadelphia-phillies",
                "feed_preference": "favorite",
            },
        )

        result = sports.scan_channels(
            db_path,
            channels,
            now=datetime(2026, 8, 4, 17, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        rows = sports.generated_rows(db_path)

        self.assertEqual(result["events"], 2)
        self.assertEqual(result["count"], 4)
        phillies_rows = [row for row in rows if "Phillies" in row["display_name"]]
        orioles_rows = [row for row in rows if "Orioles" in row["display_name"]]
        self.assertEqual(len(phillies_rows), 3)
        self.assertEqual(len(orioles_rows), 1)
        self.assertEqual(len({row["event_key"] for row in phillies_rows}), 1)
        self.assertEqual(len({row["event_key"] for row in rows}), 2)

    def test_shared_baseball_group_uses_all_mlb_epg_matchups_and_full_names(self):
        db_path = Path(self.temp.name) / "all-mlb-epg.db"
        matchups = [
            ("ARI", "ATL"),
            ("BAL", "BOS"),
            ("CHC", "CIN"),
            ("CLE", "COL"),
            ("DET", "HOU"),
            ("KC", "LAA"),
            ("LAD", "MIA"),
            ("MIL", "MIN"),
            ("NYM", "NYY"),
            ("OAK", "PHI"),
            ("PIT", "SD"),
            ("SF", "SEA"),
            ("STL", "TEX"),
            ("TOR", "WSH"),
            ("CHW", "TB"),
        ]
        channel_lines = ["#EXTM3U"]
        xml_channels = []
        xml_programmes = []
        for index, (away, home) in enumerate(matchups, start=1):
            channel_id = f"baseball.event.{index}"
            channel_name = f"Baseball Event {index:02d}"
            if index == 1:
                # The same game appears in both M3U and XMLTV with different
                # naming styles; it must merge into one generated event.
                channel_name = (
                    "(MLB 01) | Arizona Diamondbacks @ Atlanta Braves "
                    "(2026-08-02 13:01:00)"
                )
            channel_lines.extend(
                [
                    f'#EXTINF:-1 tvg-id="{channel_id}" group-title="MLB / MiLB",{channel_name}',
                    f"http://provider.test/user/pass/{index}.ts",
                ]
            )
            xml_channels.append(
                f'<channel id="{channel_id}"><display-name>Baseball Event {index:02d}</display-name></channel>'
            )
            xml_programmes.append(
                f'<programme channel="{channel_id}" start="2026080213{index:02d}00 -0400" '
                f'stop="2026080217{index:02d}00 -0400"><title>{away} at {home}</title></programme>'
            )

        # Add a clearly marked minor-league event to prove it remains separate.
        channel_lines.extend(
            [
                '#EXTINF:-1 tvg-id="baseball.milb.1" group-title="MLB / MiLB",US (MiLB 001)',
                "http://provider.test/user/pass/milb.ts",
            ]
        )
        xml_channels.append(
            '<channel id="baseball.milb.1"><display-name>US (MiLB 001)</display-name></channel>'
        )
        xml_programmes.append(
            '<programme channel="baseball.milb.1" start="20260802130500 -0400" '
            'stop="20260802170500 -0400"><title>Lehigh Valley IronPigs at Norfolk Tides</title>'
            '<category>MiLB</category></programme>'
        )

        channels = core.parse_m3u_text("\n".join(channel_lines) + "\n")
        epg_path = Path(self.temp.name) / "all-mlb.xml"
        epg_path.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?><tv>"
            + "".join(xml_channels)
            + "".join(xml_programmes)
            + "</tv>",
            encoding="utf-8",
        )

        sports.init_db(db_path)
        sports.update_settings(
            db_path,
            {
                "enabled": True,
                "timezone": "America/New_York",
                "refresh_time": "03:00",
            },
        )
        mlb_teams = [
            item
            for item in sports.catalog_payload(db_path, scope_type="team")
            if item["league_id"] == "mlb"
        ]
        self.assertEqual(len(mlb_teams), 30)
        sports.add_rule(
            db_path,
            {"scope_type": "league", "scope_id": "mlb", "feed_preference": "best"},
        )
        now = datetime(2026, 8, 2, 5, 5, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(
            db_path, channels, epg_path=epg_path, now=now, trigger="test"
        )
        rows = sports.generated_rows(db_path)

        self.assertEqual(result["events"], 15)
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(row["league_id"] == "mlb" for row in rows))
        names = {row["display_name"] for row in rows}
        self.assertIn(
            "MLB • Chicago White Sox at Tampa Bay Rays — Event Feed", names
        )
        self.assertFalse(any("IronPigs" in name for name in names))
        self.assertFalse(any("CHW" in name or " TB " in name for name in names))

    def test_stale_event_timestamp_does_not_fake_later_guide_coverage(self):
        settings = sports.get_settings(self.db_path)
        generated_at = datetime(2026, 8, 2, 4, 50, tzinfo=ZoneInfo("America/New_York"))
        generated = [
            {
                "tvg_id": "m3u-picker.sports.stale",
                "assigned_number": 1000,
                "display_name": "MLB • Philadelphia Phillies at Baltimore Orioles — Event Feed",
                "tvg_logo": "",
                "league_id": "mlb",
                "event_title": "Philadelphia Phillies at Baltimore Orioles",
                "subtitle": "Provider event stream",
                "event_start": "2026-08-01T19:05:00-04:00",
                "event_end": "2026-08-01T23:05:00-04:00",
                "is_replay": False,
            }
        ]
        root = ElementTree.fromstring(
            sports.build_sports_xmltv(generated, settings, generated_at=generated_at)
        )
        game_time = datetime(2026, 8, 2, 13, 30, tzinfo=ZoneInfo("America/New_York"))
        covering = []
        for programme in root.findall("programme"):
            start = sports._parse_xmltv_time(programme.attrib["start"], game_time.tzinfo)
            stop = sports._parse_xmltv_time(programme.attrib["stop"], game_time.tzinfo)
            if start <= game_time < stop:
                covering.append(programme)
        self.assertEqual(covering, [])

    def test_live_xmltv_programme_starts_at_timezone_adjusted_first_pitch(self):
        settings = sports.get_settings(self.db_path)
        generated_at = datetime(2026, 8, 2, 4, 50, tzinfo=ZoneInfo("America/New_York"))
        generated = [
            {
                "tvg_id": "m3u-picker.sports.live",
                "assigned_number": 1000,
                "display_name": "MLB • Philadelphia Phillies at Baltimore Orioles — Event Feed",
                "tvg_logo": "",
                "league_id": "mlb",
                "event_title": "Philadelphia Phillies at Baltimore Orioles",
                "subtitle": "Provider event stream",
                "event_start": "2026-08-02T17:35:00+00:00",
                "event_end": "2026-08-02T21:35:00+00:00",
                "is_replay": False,
            }
        ]
        root = ElementTree.fromstring(
            sports.build_sports_xmltv(generated, settings, generated_at=generated_at)
        )
        first_pitch = datetime(2026, 8, 2, 13, 35, tzinfo=ZoneInfo("America/New_York"))
        covering = []
        for programme in root.findall("programme"):
            start = sports._parse_xmltv_time(programme.attrib["start"], first_pitch.tzinfo)
            stop = sports._parse_xmltv_time(programme.attrib["stop"], first_pitch.tzinfo)
            if start <= first_pitch < stop:
                covering.append(programme)
        self.assertEqual(len(covering), 1)
        self.assertTrue(covering[0].findtext("title", default="").startswith("MLB •"))

    def test_purge_stale_generated_removes_only_rows_past_postgame_grace(self):
        sports.update_settings(self.db_path, {"enabled": True, "timezone": "America/New_York"})
        now = datetime(2026, 8, 8, 21, 0, tzinfo=ZoneInfo("America/New_York"))
        rows = [
            ("expired", 1000, "2026-08-08T19:29:00-04:00"),
            ("within-grace", 1010, "2026-08-08T19:31:00-04:00"),
        ]
        with sports.closing(sports._connect(self.db_path)) as conn:
            for key, number, event_end in rows:
                raw = [
                    f'#EXTINF:-1 tvg-id="m3u-picker-sports-{number}" tvg-chno="{number}",Test {key}',
                    f'http://provider.test/{key}.ts',
                ]
                conn.execute(
                    """
                    INSERT INTO sports_generated
                        (channel_key, source_channel_key, event_key, league_id,
                         display_name, subtitle, feed_type, assigned_number,
                         group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                         event_title, event_start, event_end, is_replay,
                         epg_programme_json, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"sports:{key}", raw[1], key, "mlb", f"Test {key}",
                        "Provider event stream", "event", number, "Sports Today",
                        raw[1], f"m3u-picker-sports-{number}", "", "",
                        __import__("json").dumps(raw), key,
                        "2026-08-08T16:00:00-04:00", event_end, 0, "{}",
                        "2026-08-08T03:00:00-04:00",
                    ),
                )
            conn.commit()

        removed = sports.purge_stale_generated(self.db_path, now)
        self.assertEqual(removed, 1)
        remaining = sports.generated_rows(self.db_path)
        self.assertEqual([row["event_key"] for row in remaining], ["within-grace"])

    def test_synthetic_postgame_window_is_capped_at_ninety_minutes(self):
        settings = sports.get_settings(self.db_path)
        tz = ZoneInfo("America/New_York")
        generated = [{
            "tvg_id": "m3u-picker-sports-1000",
            "assigned_number": 1000,
            "display_name": "MLB • Toronto Blue Jays at Philadelphia Phillies — Event Feed",
            "tvg_logo": "",
            "league_id": "mlb",
            "event_title": "Toronto Blue Jays at Philadelphia Phillies",
            "subtitle": "Provider event stream",
            "event_start": "2026-08-08T18:05:00-04:00",
            "event_end": "2026-08-08T21:05:00-04:00",
            "is_replay": False,
        }]
        root = ElementTree.fromstring(
            sports.build_sports_xmltv(
                generated,
                settings,
                generated_at=datetime(2026, 8, 8, 17, 0, tzinfo=tz),
            )
        )
        windows = [
            node for node in root.findall("programme")
            if node.findtext("title", default="").endswith("— Event window")
        ]
        self.assertEqual(len(windows), 1)
        start = sports._parse_xmltv_time(windows[0].attrib["start"], tz)
        stop = sports._parse_xmltv_time(windows[0].attrib["stop"], tz)
        self.assertEqual(stop - start, sports.EVENT_END_GRACE)

    def test_generated_xmltv_ids_are_stable_numbered_slots(self):
        self.assertEqual(sports._generated_tvg_id(1000), "m3u-picker-sports-1000")
        self.assertEqual(sports._generated_tvg_id(1042), "m3u-picker-sports-1042")

    def test_existing_generated_rows_migrate_to_numbered_guide_ids(self):
        legacy = Path(self.temp.name) / "legacy-guide-id.db"
        sports.init_db(legacy)
        conn = sports._connect(legacy)
        try:
            conn.execute(
                "DELETE FROM sports_settings WHERE key = ?",
                ("migration_generated_xmltv_slot_ids_v20_7",),
            )
            old_id = "m3u-picker.sports.old-daily-event"
            raw = [
                f'#EXTINF:-1 tvg-id="{old_id}" tvg-chno="1000",Old event',
                "http://provider.test/user/pass/legacy.ts",
            ]
            conn.execute(
                """
                INSERT INTO sports_generated
                    (channel_key, source_channel_key, event_key, league_id,
                     display_name, subtitle, feed_type, assigned_number,
                     group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                     event_title, event_start, event_end, is_replay, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sports:legacy",
                    raw[1],
                    "2026-08-01:legacy",
                    "mlb",
                    "MLB • Old event — Event Feed",
                    "Provider event stream",
                    "event",
                    1000,
                    "Sports Today",
                    raw[1],
                    old_id,
                    "",
                    "",
                    __import__("json").dumps(raw),
                    "Old event",
                    "2026-08-01T13:00:00-04:00",
                    "2026-08-01T17:00:00-04:00",
                    0,
                    "2026-08-01T05:00:00-04:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        sports.init_db(legacy)
        row = sports.generated_rows(legacy, include_cached=True)[0]
        self.assertEqual(row["tvg_id"], "m3u-picker-sports-1000")
        self.assertIn('tvg-id="m3u-picker-sports-1000"', row["raw"][0])


    def test_xmltv_channel_includes_numeric_mapping_alias_and_live_marker(self):
        settings = sports.get_settings(self.db_path)
        generated_at = datetime(2026, 8, 2, 4, 50, tzinfo=ZoneInfo("America/New_York"))
        generated = [
            {
                "tvg_id": "m3u-picker-sports-1000",
                "assigned_number": 1000,
                "display_name": "MLB • Philadelphia Phillies at Baltimore Orioles — Event Feed",
                "tvg_logo": "",
                "league_id": "mlb",
                "event_title": "Philadelphia Phillies at Baltimore Orioles",
                "subtitle": "Provider event stream",
                "event_start": "2026-08-02T13:35:00-04:00",
                "event_end": "2026-08-02T17:35:00-04:00",
                "is_replay": False,
            }
        ]
        root = ElementTree.fromstring(
            sports.build_sports_xmltv(generated, settings, generated_at=generated_at)
        )
        channel = root.find("channel")
        self.assertIsNotNone(channel)
        display_names = [node.text for node in channel.findall("display-name")]
        self.assertIn("1000", display_names)
        live_programmes = [node for node in root.findall("programme") if node.find("live") is not None]
        self.assertEqual(len(live_programmes), 1)

    def test_authoritative_current_xmltv_programme_is_cloned_to_every_generated_feed(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="" tvg-name="" tvg-logo="https://example.test/mlb.png" group-title="MLB",(MLB 1) | Washington Nationals @ Philadelphia Phillies (2026-08-03 18:30:00)
http://provider.test/user/pass/event.ts
#EXTINF:-1 tvg-id="WashingtonNationals.mlb" tvg-name="MLB Washington Nationals" tvg-logo="https://example.test/nationals.png" group-title="MLB",MLB Washington Nationals
http://provider.test/user/pass/nationals.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" tvg-name="MLB Philadelphia Phillies" tvg-logo="https://example.test/phillies.png" group-title="MLB",MLB Philadelphia Phillies
http://provider.test/user/pass/phillies.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        epg_path = Path(self.temp.name) / "provider-current.xml"
        epg_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="PhiladelphiaPhillies.mlb"><display-name>MLB Philadelphia Phillies</display-name></channel>
  <programme channel="PhiladelphiaPhillies.mlb" start="20260803183000 -0400" stop="20260803213000 -0400">
    <title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title>
    <desc>From Citizens Bank Park in Philadelphia.</desc>
    <category>Baseball</category>
    <category>Sports</category>
    <live />
  </programme>
</tv>""",
            encoding="utf-8",
        )
        sports_epg = Path(self.temp.name) / "sports-current.xml"
        combined_epg = Path(self.temp.name) / "combined-current.xml"
        scan_time = datetime(2026, 8, 3, 20, 49, tzinfo=ZoneInfo("America/New_York"))

        result = sports.scan_channels(
            self.db_path,
            channels,
            epg_path=epg_path,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=scan_time,
            trigger="test",
        )
        self.assertEqual(result["count"], 3)
        rows = sports.generated_rows(self.db_path)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["epg_programme"]["is_live"] for row in rows))
        self.assertTrue(
            all(
                row["epg_programme"]["title"]
                == "MLB Baseball : Washington Nationals at Philadelphia Phillies"
                for row in rows
            )
        )

        root = ElementTree.parse(sports_epg).getroot()
        expected_start = datetime(2026, 8, 3, 18, 30, tzinfo=scan_time.tzinfo)
        expected_stop = datetime(2026, 8, 3, 21, 30, tzinfo=scan_time.tzinfo)
        for row in rows:
            covering = []
            for programme in root.findall(f"programme[@channel='{row['tvg_id']}']"):
                start = sports._parse_xmltv_time(programme.attrib["start"], scan_time.tzinfo)
                stop = sports._parse_xmltv_time(programme.attrib["stop"], scan_time.tzinfo)
                if start <= scan_time < stop:
                    covering.append((programme, start, stop))
            self.assertEqual(len(covering), 1)
            programme, start, stop = covering[0]
            self.assertEqual(start, expected_start)
            self.assertEqual(stop, expected_stop)
            self.assertEqual(
                programme.findtext("title", default=""),
                "MLB Baseball : Washington Nationals at Philadelphia Phillies",
            )
            self.assertEqual(
                programme.findtext("sub-title", default=""),
                sports._clean_feed_subtitle(row["subtitle"]),
            )
            self.assertIn("From Citizens Bank Park", programme.findtext("desc", default=""))
            self.assertIsNotNone(programme.find("live"))

            postgame = [
                node
                for node in root.findall(f"programme[@channel='{row['tvg_id']}']")
                if node.findtext("title", default="").endswith("— Event window")
            ]
            self.assertEqual(len(postgame), 1)
            post_start = sports._parse_xmltv_time(postgame[0].attrib["start"], scan_time.tzinfo)
            post_stop = sports._parse_xmltv_time(postgame[0].attrib["stop"], scan_time.tzinfo)
            self.assertEqual(post_start, expected_stop)
            self.assertEqual(
                post_stop,
                datetime(2026, 8, 3, 23, 0, tzinfo=scan_time.tzinfo),
            )

        # Startup/rebuild paths use persisted rows, so provenance must survive
        # SQLite rather than existing only in the in-memory scan result.
        rebuilt_sports = Path(self.temp.name) / "sports-rebuilt.xml"
        rebuilt_combined = Path(self.temp.name) / "combined-rebuilt.xml"
        sports.rebuild_epg_exports(
            self.db_path,
            base_epg_path=epg_path,
            sports_epg_path=rebuilt_sports,
            combined_epg_path=rebuilt_combined,
        )
        rebuilt_root = ElementTree.parse(rebuilt_sports).getroot()
        rebuilt_titles = {
            node.findtext("title", default="")
            for node in rebuilt_root.findall("programme")
        }
        self.assertIn(
            "MLB Baseball : Washington Nationals at Philadelphia Phillies",
            rebuilt_titles,
        )

    def test_served_guide_validation_checks_actual_playlist_and_xml_files(self):
        sports_epg = Path(self.temp.name) / "sports.xml"
        combined_epg = Path(self.temp.name) / "combined.xml"
        playlist = Path(self.temp.name) / "custom.m3u"
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(
            self.db_path,
            self.channels,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=now,
            trigger="test",
        )
        rows = sports.generated_rows(self.db_path)
        playlist.write_text(
            "#EXTM3U\n" + "\n".join(line for row in rows for line in row["raw"]) + "\n",
            encoding="utf-8",
        )
        check = sports.validate_guide_exports(
            self.db_path,
            playlist_path=playlist,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
        )
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["generated_channels"], 3)
        self.assertEqual(check["sports_xml_programme_channels"], 3)
        self.assertEqual(check["uncovered_event_starts"], [])

    def test_batch_add_rules(self):
        fresh = Path(self.temp.name) / "batch.db"
        sports.init_db(fresh)
        rules = sports.add_rules(
            fresh,
            [
                {"scope_type": "league", "scope_id": "nfl", "feed_preference": "best"},
                {"scope_type": "sport", "scope_id": "cornhole", "feed_preference": "best"},
            ],
        )
        self.assertEqual({rule["scope_id"] for rule in rules}, {"nfl", "cornhole"})


    def test_disabling_sports_hides_generated_rows_but_keeps_24_hour_cache(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        self.assertEqual(len(sports.generated_rows(self.db_path)), 3)

        sports.update_settings(self.db_path, {"enabled": False})
        self.assertEqual(sports.generated_rows(self.db_path), [])
        self.assertEqual(len(sports.generated_rows(self.db_path, include_cached=True)), 3)
        cache = sports.disabled_cache_status(self.db_path)
        self.assertEqual(cache["count"], 3)
        self.assertIsNotNone(cache["expires_at"])

        sports.update_settings(self.db_path, {"enabled": True})
        self.assertEqual(len(sports.generated_rows(self.db_path)), 3)

    def test_disabled_sports_exports_hide_cached_channels(self):
        base_epg = Path(self.temp.name) / "provider-disabled.xml"
        base_epg.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><tv>'
            '<channel id="dateline"><display-name>Dateline</display-name></channel>'
            '<programme channel="dateline" start="20260802000000 -0400" stop="20260803000000 -0400">'
            '<title>Dateline</title></programme></tv>',
            encoding="utf-8",
        )
        sports_epg = Path(self.temp.name) / "sports-disabled.xml"
        combined_epg = Path(self.temp.name) / "combined-disabled.xml"
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(
            self.db_path, self.channels, base_epg,
            sports_epg_path=sports_epg, combined_epg_path=combined_epg,
            now=now, trigger="test",
        )
        sports.update_settings(self.db_path, {"enabled": False})
        sports.rebuild_epg_exports(
            self.db_path,
            base_epg_path=base_epg,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
        )

        sports_root = ElementTree.parse(sports_epg).getroot()
        self.assertEqual(sports_root.findall("channel"), [])
        self.assertEqual(sports_root.findall("programme"), [])
        combined_root = ElementTree.parse(combined_epg).getroot()
        combined_ids = {node.attrib["id"] for node in combined_root.findall("channel")}
        self.assertEqual(combined_ids, {"dateline"})
        self.assertEqual(len(sports.generated_rows(self.db_path, include_cached=True)), 3)

    def test_disabled_cache_purges_after_24_hours(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        sports.update_settings(self.db_path, {"enabled": False})
        with sports.closing(sports._connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (sports.SPORTS_DISABLED_AT_KEY, '"2026-07-31T00:00:00-04:00"'),
            )
            conn.commit()
        self.assertTrue(sports.purge_expired_disabled_cache(
            self.db_path,
            datetime(2026, 8, 2, 6, 0, tzinfo=ZoneInfo("America/New_York")),
        ))
        self.assertEqual(sports.generated_rows(self.db_path, include_cached=True), [])

    def test_international_sports_are_first_class_catalog_choices(self):
        sport_ids = {
            item["id"] for item in sports.catalog_payload(self.db_path, scope_type="sport")
        }
        self.assertTrue({
            "curling", "cricket", "rugby-union", "rugby-league", "darts", "poker"
        }.issubset(sport_ids))
        self.assertEqual(sports._detect_sport("ICC Cricket World Cup"), "cricket")
        self.assertEqual(sports._detect_sport("PDC World Darts Championship"), "darts")
        self.assertEqual(sports._detect_sport("Six Nations Rugby Union"), "rugby-union")
        self.assertEqual(sports._detect_sport("World Series of Poker"), "poker")


    def test_v21_taxonomy_includes_cycling_racing_combat_and_olympic_sports(self):
        sport_ids = {
            item["id"] for item in sports.catalog_payload(self.db_path, scope_type="sport")
        }
        self.assertTrue({
            "cycling", "motorsports", "mma", "pro-wrestling", "golf",
            "track-field", "swimming", "figure-skating", "olympics",
            "tennis", "volleyball", "boxing", "biathlon", "rowing",
        }.issubset(sport_ids))
        league_ids = {
            item["id"] for item in sports.catalog_payload(self.db_path, scope_type="league")
        }
        self.assertTrue({
            "tour-de-france", "formula-1", "nascar-cup", "ufc", "wwe",
            "ncaaf-fbs", "ncaaf-fcs", "ncaaf-d2", "ncaaf-d3",
            "naia-football", "njcaa-football", "high-school-football",
        }.issubset(league_ids))

    def test_college_football_divisions_are_classified_separately(self):
        self.assertEqual(sports._detect_league("NCAA FBS College Football"), "ncaaf-fbs")
        self.assertEqual(sports._detect_league("FCS College Football"), "ncaaf-fcs")
        self.assertEqual(sports._detect_league("NCAA Division II Football"), "ncaaf-d2")
        self.assertEqual(sports._detect_league("NCAA Division III Football"), "ncaaf-d3")
        self.assertEqual(sports._detect_league("NAIA Football"), "naia-football")
        self.assertEqual(sports._detect_league("NJCAA Junior College Football"), "njcaa-football")
        self.assertEqual(sports._detect_league("High School Football All-American Bowl"), "high-school-football")
        # A cross-division event belongs to the higher subdivision.
        self.assertEqual(sports._detect_league("FBS vs FCS College Football"), "ncaaf-fbs")

    def test_cycling_and_olympic_event_detection(self):
        self.assertEqual(sports._detect_league("Tour de France Stage 7"), "tour-de-france")
        self.assertEqual(sports._detect_sport("Tour de France Stage 7"), "cycling")
        tags = sports._detect_sport_tags("Olympic Figure Skating Free Skate")
        self.assertIn("figure-skating", tags)
        self.assertIn("olympics", tags)

    def test_primary_blocks_are_1000_channels_and_overflow_never_spills(self):
        plan = sports.numbering_plan(sports.get_settings(self.db_path))
        first = {block["id"]: block for block in plan["blocks"][:7]}
        self.assertEqual((first["mlb"]["start"], first["mlb"]["end"]), (1000, 1999))
        self.assertEqual((first["nhl"]["start"], first["nhl"]["end"]), (2000, 2999))
        self.assertEqual((first["nba"]["start"], first["nba"]["end"]), (3000, 3999))
        self.assertEqual((first["nfl"]["start"], first["nfl"]["end"]), (4000, 4999))
        self.assertEqual(plan["events_per_primary_block"], 100)

        last_primary = sports.assigned_channel_number(
            "ncaaf-fbs", 99, 9, start_channel=1000, channels_per_event=10
        )
        first_overflow = sports.assigned_channel_number(
            "ncaaf-fbs", 100, 0, start_channel=1000, channels_per_event=10
        )
        fcs_start = sports.assigned_channel_number(
            "ncaaf-fcs", 0, 0, start_channel=1000, channels_per_event=10
        )
        self.assertEqual(last_primary, 6999)
        self.assertNotEqual(first_overflow, fcs_start)
        self.assertGreater(first_overflow, 1_000_000)

    def test_global_hide_sd_setting_excludes_sports_generated_feeds(self):
        sd_event = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="LOW BANDWIDTH",NHL | Philadelphia Flyers @ New York Rangers (2026-08-01 20:00:00) SD
http://provider.test/user/pass/nhl-sd.ts
"""
        )
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "nhl"})
        sports.update_settings(self.db_path, {"exclude_sd": True})
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, [*self.channels, *sd_event], now=now, trigger="test")
        self.assertFalse(any(row["league_id"] == "nhl" for row in sports.generated_rows(self.db_path)))

    def test_scan_groups_mlb_and_nhl_into_separate_number_ranges(self):
        extra = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 group-title="NHL",NHL | Philadelphia Flyers @ New York Rangers (2026-08-01 23:30:00)
http://provider.test/user/pass/nhl-event.ts
"""
        )
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "nhl"})
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, [*self.channels, *extra], now=now, trigger="test")
        rows = sports.generated_rows(self.db_path)
        mlb_numbers = [row["assigned_number"] for row in rows if row["league_id"] == "mlb"]
        nhl_numbers = [row["assigned_number"] for row in rows if row["league_id"] == "nhl"]
        self.assertTrue(mlb_numbers)
        self.assertTrue(nhl_numbers)
        self.assertTrue(all(1000 <= number <= 1999 for number in mlb_numbers))
        self.assertTrue(all(2000 <= number <= 2999 for number in nhl_numbers))


    def test_epg_manager_payload_hides_stored_url_and_credentials(self):
        original_sources = core.epg_sources
        try:
            core.epg_sources = [
                {
                    "id": "provider",
                    "name": "Provider guide",
                    "url": "http://provider.test/xmltv.php?username=secret&password=hidden",
                    "last_refresh": None,
                    "last_error": None,
                }
            ]
            payload = core.epg_sources_payload()
        finally:
            core.epg_sources = original_sources

        self.assertEqual(payload[0]["source_label"], "Provider")
        self.assertNotIn("url", payload[0])
        self.assertNotIn("secret", str(payload[0]))
        self.assertNotIn("hidden", str(payload[0]))

    def test_stale_empty_guide_is_detected_when_generated_rows_exist(self):
        original_db = core.DB_PATH
        original_sports_epg = core.SPORTS_EPG_PATH
        original_combined_epg = core.COMBINED_EPG_PATH
        original_epg_cache = core.EPG_CACHE_PATH
        try:
            core.DB_PATH = self.db_path
            core.SPORTS_EPG_PATH = Path(self.temp.name) / "served-sports.xml"
            core.COMBINED_EPG_PATH = Path(self.temp.name) / "served-combined.xml"
            core.EPG_CACHE_PATH = Path(self.temp.name) / "provider.xml"
            now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
            sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
            core.SPORTS_EPG_PATH.write_text(
                "<?xml version='1.0'?><tv />", encoding="utf-8"
            )
            self.assertTrue(core.guide_export_needs_rebuild(core.SPORTS_EPG_PATH))
            core.ensure_epg_exports_current()
            rebuilt = core.SPORTS_EPG_PATH.read_text(encoding="utf-8")
            generated_id = sports.generated_rows(self.db_path)[0]["tvg_id"]
            self.assertIn(generated_id, rebuilt)
            self.assertIn("<programme", rebuilt)
        finally:
            core.DB_PATH = original_db
            core.SPORTS_EPG_PATH = original_sports_epg
            core.COMBINED_EPG_PATH = original_combined_epg
            core.EPG_CACHE_PATH = original_epg_cache

    def test_builtin_epg_payload_advertises_only_epg_guide(self):
        original_sports_epg = core.SPORTS_EPG_PATH
        original_combined_epg = core.COMBINED_EPG_PATH
        try:
            core.SPORTS_EPG_PATH = Path(self.temp.name) / "served-sports.xml"
            core.COMBINED_EPG_PATH = Path(self.temp.name) / "served-combined.xml"
            core.SPORTS_EPG_PATH.write_text("<tv />", encoding="utf-8")
            core.COMBINED_EPG_PATH.write_text("<tv><channel id='one'/></tv>", encoding="utf-8")
            payload = {item["id"]: item for item in core.epg_builtin_payload()}
        finally:
            core.SPORTS_EPG_PATH = original_sports_epg
            core.COMBINED_EPG_PATH = original_combined_epg

        self.assertEqual(set(payload), {"epg"})
        self.assertEqual(payload["epg"]["url_path"], "/epg/epg.xml")
        self.assertTrue(payload["epg"]["cached"])
        self.assertTrue(payload["epg"]["last_refresh"])


    def _logical_airing_fixture(self):
        timezone = ZoneInfo("America/New_York")

        def event(start, stop, *, is_live=False, is_replay=False):
            return {
                "event_key": "temporary",
                "event_base_key": f"{start.date().isoformat()}:mlb:washington:philadelphia",
                "event_identity": "mlb:washington:philadelphia",
                "event_date": start.date().isoformat(),
                "league_id": "mlb",
                "sport_id": "baseball",
                "sport_tags": ["baseball"],
                "display_name": "Washington Nationals at Philadelphia Phillies",
                "away_team_id": "mlb:washington-nationals",
                "away_team_name": "Washington Nationals",
                "home_team_id": "mlb:philadelphia-phillies",
                "home_team_name": "Philadelphia Phillies",
                "start": start,
                "end": stop,
                "time_is_explicit": True,
                "timing_source": "xmltv",
                "source_channels": [
                    {
                        "url": f"http://provider.test/{start:%H%M}.ts",
                        "name": "MLB Philadelphia Phillies",
                    }
                ],
                "source_text": "Washington Nationals at Philadelphia Phillies",
                "is_replay": is_replay,
                "epg_programme": {
                    "title": "MLB Baseball : Washington Nationals at Philadelphia Phillies",
                    "subtitle": "",
                    "description": "From Citizens Bank Park in Philadelphia.",
                    "categories": ["Baseball", "Sports"],
                    "start": start,
                    "stop": stop,
                    "is_live": is_live,
                    "is_replay": is_replay,
                    "is_new": False,
                    "current_at_scan": False,
                    "source_channel_id": "PhiladelphiaPhillies.mlb",
                },
            }

        return [
            event(
                datetime(2026, 8, 4, 18, 40, tzinfo=timezone),
                datetime(2026, 8, 4, 22, 40, tzinfo=timezone),
                is_live=True,
            ),
            event(
                datetime(2026, 8, 5, 0, 30, tzinfo=timezone),
                datetime(2026, 8, 5, 3, 0, tzinfo=timezone),
            ),
            event(
                datetime(2026, 8, 5, 6, 0, tzinfo=timezone),
                datetime(2026, 8, 5, 8, 0, tzinfo=timezone),
            ),
        ]

    def test_later_same_matchup_airings_do_not_allocate_new_events_when_replays_disabled(self):
        merged = sports._merge_events(
            self._logical_airing_fixture(),
            settings={"include_replays": False},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start"].hour, 18)
        self.assertEqual(merged[0].get("epg_programmes", []), [])
        self.assertTrue(merged[0]["event_key"].endswith(":1840"))

    def test_replays_are_additional_programmes_on_one_logical_event(self):
        merged = sports._merge_events(
            self._logical_airing_fixture(),
            settings={"include_replays": True},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["epg_programmes"]), 2)
        self.assertTrue(all(item["is_replay"] for item in merged[0]["epg_programmes"]))
        self.assertEqual(
            sports._event_end(merged[0]),
            datetime(2026, 8, 5, 8, 0, tzinfo=ZoneInfo("America/New_York")),
        )

        item = {
            "tvg_id": "m3u-picker-sports-1000",
            "display_name": "MLB • Washington Nationals at Philadelphia Phillies — Phillies Feed",
            "assigned_number": 1000,
            "tvg_logo": "",
            "league_id": "mlb",
            "event_title": merged[0]["display_name"],
            "subtitle": "Home broadcast • Philadelphia Phillies • 6:40 PM",
            "event_start": merged[0]["start"].isoformat(),
            "event_end": sports._event_end(merged[0]).isoformat(),
            "is_replay": False,
            "epg_programme": sports._serialize_epg_programme(merged[0]),
        }
        xml = sports.build_sports_xmltv(
            [item],
            {
                "timezone": "America/New_York",
                "schedule_mode": "interval",
                "interval_hours": 2,
                "target_mode": "next24",
            },
            generated_at=datetime(2026, 8, 4, 17, 0, tzinfo=ZoneInfo("America/New_York")),
        )
        root = ElementTree.fromstring(xml)
        self.assertEqual(len(root.findall("channel")), 1)
        replay_nodes = [
            node
            for node in root.findall("programme")
            if node.find("previously-shown") is not None
        ]
        self.assertEqual(len(replay_nodes), 2)
        self.assertTrue(
            all(node.findtext("title", default="").startswith("Replay:") for node in replay_nodes)
        )
        for node in root.findall("programme"):
            if not node.findtext("title", default="").endswith("— Event window"):
                continue
            start = sports._parse_xmltv_time(node.attrib["start"], ZoneInfo("America/New_York"))
            stop = sports._parse_xmltv_time(node.attrib["stop"], ZoneInfo("America/New_York"))
            self.assertLessEqual(stop - start, timedelta(minutes=90))

    def test_embedded_schedule_anchor_overrides_bad_live_markers_on_replays(self):
        events = self._logical_airing_fixture()
        # Some provider guides mark every airing as live, including overnight
        # replays. The timed event M3U row is the canonical schedule anchor.
        events[0]["has_embedded_anchor"] = True
        events[0]["source_kind"] = "m3u"
        for event in events:
            event["epg_programme"]["is_live"] = True

        merged = sports._merge_events(
            events,
            settings={"include_replays": False, "timezone": "America/New_York"},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["event_key"], "2026-08-04:mlb:washington:philadelphia:1840")

        merged_with_replays = sports._merge_events(
            events,
            settings={"include_replays": True, "timezone": "America/New_York"},
        )
        self.assertEqual(len(merged_with_replays), 1)
        self.assertEqual(len(merged_with_replays[0].get("epg_programmes", [])), 2)
        self.assertTrue(
            all(not airing.get("is_live") for airing in merged_with_replays[0]["epg_programmes"])
        )

    def test_multiple_timed_provider_rows_in_one_broadcast_day_collapse_to_one_game(self):
        events = self._logical_airing_fixture()
        for event in events:
            event["timing_source"] = "embedded"
            event["source_kind"] = "m3u"
            event["has_embedded_anchor"] = True
            event.pop("epg_programme", None)

        merged = sports._merge_events(
            events,
            settings={"include_replays": False, "timezone": "America/New_York"},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start"].hour, 18)
        self.assertEqual(merged[0]["event_key"], "2026-08-04:mlb:washington:philadelphia:1840")

        merged_with_replays = sports._merge_events(
            events,
            settings={"include_replays": True, "timezone": "America/New_York"},
        )
        self.assertEqual(len(merged_with_replays), 1)
        # Timed rows without XMLTV programme metadata cannot create replay
        # guide entries, but they still must not allocate new channel blocks.
        self.assertEqual(merged_with_replays[0].get("epg_programmes", []), [])

    def test_migrated_duplicate_history_anchors_do_not_resurrect_old_channel_blocks(self):
        current = self._logical_airing_fixture()
        history = []
        for event in self._logical_airing_fixture():
            clone = dict(event)
            clone["source_channels"] = []
            clone["source_kind"] = "history"
            clone["source_kinds"] = ["history"]
            clone["historical_anchor"] = True
            clone["has_embedded_anchor"] = True
            clone["timing_source"] = "embedded"
            history.append(clone)

        merged = sports._merge_events(
            [*history, *current],
            settings={"include_replays": False, "timezone": "America/New_York"},
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start"].hour, 18)
        self.assertFalse(merged[0].get("historical_anchor", False))

    def test_same_matchup_next_evening_remains_a_new_logical_game(self):
        events = self._logical_airing_fixture()[:1]
        next_game = dict(events[0])
        next_game["start"] = datetime(
            2026, 8, 5, 18, 40, tzinfo=ZoneInfo("America/New_York")
        )
        next_game["end"] = datetime(
            2026, 8, 5, 22, 40, tzinfo=ZoneInfo("America/New_York")
        )
        next_game["epg_programme"] = dict(next_game["epg_programme"])
        next_game["epg_programme"]["start"] = next_game["start"]
        next_game["epg_programme"]["stop"] = next_game["end"]
        for event in (events[0], next_game):
            event["timing_source"] = "embedded"
            event["source_kind"] = "m3u"
            event["has_embedded_anchor"] = True

        merged = sports._merge_events(
            [events[0], next_game],
            settings={"include_replays": False, "timezone": "America/New_York"},
        )

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            [event["event_date"] for event in merged],
            ["2026-08-04", "2026-08-05"],
        )

    def test_scan_allocates_one_channel_block_for_live_game_and_overnight_replays(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="event.mlb" tvg-name="MLB Event" group-title="MLB",(MLB 1) | Washington Nationals @ Philadelphia Phillies (2026-08-04 18:40:00)
http://provider.test/event.ts
#EXTINF:-1 tvg-id="WashingtonNationals.mlb" tvg-name="MLB Washington Nationals" group-title="MLB",MLB Washington Nationals
http://provider.test/nationals.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" tvg-name="MLB Philadelphia Phillies" group-title="MLB",MLB Philadelphia Phillies
http://provider.test/phillies.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, channels)
        sports.add_rule(
            self.db_path,
            {
                "scope_type": "league",
                "scope_id": "mlb",
                "feed_preference": "all",
            },
        )
        sports.update_settings(
            self.db_path,
            {
                "event_window": "next_24_hours",
                "include_replays": False,
            },
        )
        epg_path = Path(self.temp.name) / "logical-airings.xml"
        epg_path.write_text(
            """<tv>
<channel id="event.mlb"><display-name>MLB Event</display-name></channel>
<programme channel="event.mlb" start="20260804184000 -0400" stop="20260804224000 -0400"><title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title><category>MLB</category><live/></programme>
<programme channel="event.mlb" start="20260805003000 -0400" stop="20260805030000 -0400"><title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title><category>MLB</category><live/></programme>
<programme channel="event.mlb" start="20260805060000 -0400" stop="20260805080000 -0400"><title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title><category>MLB</category><live/></programme>
</tv>""",
            encoding="utf-8",
        )
        sports_epg = Path(self.temp.name) / "logical-sports.xml"
        combined_epg = Path(self.temp.name) / "logical-combined.xml"
        scan_time = datetime(2026, 8, 4, 17, 0, tzinfo=ZoneInfo("America/New_York"))

        result = sports.scan_channels(
            self.db_path,
            channels,
            epg_path=epg_path,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=scan_time,
            trigger="test",
        )
        self.assertEqual(result["count"], 3)
        rows = sports.generated_rows(self.db_path)
        self.assertEqual([row["assigned_number"] for row in rows], [1000, 1001, 1002])
        self.assertEqual(len({row["event_key"] for row in rows}), 1)
        self.assertTrue(all(not row["epg_programme"].get("airings") for row in rows))

        sports.update_settings(self.db_path, {"include_replays": True})
        result = sports.scan_channels(
            self.db_path,
            channels,
            epg_path=epg_path,
            sports_epg_path=sports_epg,
            combined_epg_path=combined_epg,
            now=scan_time,
            trigger="test",
        )
        self.assertEqual(result["count"], 3)
        rows = sports.generated_rows(self.db_path)
        self.assertTrue(all(len(row["epg_programme"].get("airings", [])) == 2 for row in rows))
        root = ElementTree.parse(sports_epg).getroot()
        self.assertEqual(
            len([node for node in root.findall("programme") if node.find("previously-shown") is not None]),
            6,
        )

    def test_previous_scan_anchor_suppresses_replay_after_event_slot_disappears(self):
        live_channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="event.mlb" tvg-name="MLB Event" group-title="MLB",(MLB 1) | Washington Nationals @ Philadelphia Phillies (2026-08-04 18:40:00)
http://provider.test/event.ts
#EXTINF:-1 tvg-id="WashingtonNationals.mlb" tvg-name="MLB Washington Nationals" group-title="MLB",MLB Washington Nationals
http://provider.test/nationals.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" tvg-name="MLB Philadelphia Phillies" group-title="MLB",MLB Philadelphia Phillies
http://provider.test/phillies.ts
"""
        )
        sports.discover_catalog_from_channels(self.db_path, live_channels)
        sports.add_rule(
            self.db_path,
            {"scope_type": "league", "scope_id": "mlb", "feed_preference": "all"},
        )
        sports.update_settings(
            self.db_path,
            {"event_window": "next_24_hours", "include_replays": False},
        )
        live_epg = Path(self.temp.name) / "live.xml"
        live_epg.write_text(
            """<tv>
<channel id="event.mlb"><display-name>MLB Event</display-name></channel>
<programme channel="event.mlb" start="20260804184000 -0400" stop="20260804224000 -0400"><title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title><category>MLB</category><live/></programme>
</tv>""",
            encoding="utf-8",
        )
        sports.scan_channels(
            self.db_path,
            live_channels,
            epg_path=live_epg,
            now=datetime(2026, 8, 4, 19, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertGreater(len(sports.generated_rows(self.db_path)), 0)

        # The provider refresh has removed the timed event slot, but its guide
        # now labels the overnight replay as live. The previous logical game is
        # retained only as a classification anchor, so the replay does not
        # allocate a fresh channel block when replays are disabled.
        replay_channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-id="WashingtonNationals.mlb" tvg-name="MLB Washington Nationals" group-title="MLB",MLB Washington Nationals
http://provider.test/nationals.ts
#EXTINF:-1 tvg-id="PhiladelphiaPhillies.mlb" tvg-name="MLB Philadelphia Phillies" group-title="MLB",MLB Philadelphia Phillies
http://provider.test/phillies.ts
"""
        )
        replay_epg = Path(self.temp.name) / "replay.xml"
        replay_epg.write_text(
            """<tv>
<channel id="PhiladelphiaPhillies.mlb"><display-name>MLB Philadelphia Phillies</display-name></channel>
<programme channel="PhiladelphiaPhillies.mlb" start="20260805003000 -0400" stop="20260805030000 -0400"><title>MLB Baseball : Washington Nationals at Philadelphia Phillies</title><category>MLB</category><live/></programme>
</tv>""",
            encoding="utf-8",
        )
        result = sports.scan_channels(
            self.db_path,
            replay_channels,
            epg_path=replay_epg,
            now=datetime(2026, 8, 5, 0, 45, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["count"], 0)
        self.assertGreaterEqual(result["scan_metrics"]["history_anchors"], 1)

    def test_bad_live_marker_on_overnight_airing_does_not_force_duplicate_game(self):
        events = self._logical_airing_fixture()[:2]
        events[1]["epg_programme"]["is_live"] = True
        merged = sports._merge_events(
            events,
            settings={"include_replays": False, "timezone": "America/New_York"},
        )
        self.assertEqual(len(merged), 1)

    def test_schedule_api_config_hides_secret_and_is_optional(self):
        status = sports.update_schedule_api_config(
            self.db_path,
            enabled=True,
            url="https://v1.baseball.api-sports.io/",
            api_key="super-secret-key",
        )
        self.assertTrue(status["configured"])
        self.assertTrue(status["effective"])
        self.assertTrue(status["key_configured"])
        self.assertNotIn("api_key", status)
        self.assertEqual(len(status["apis"]), 1)
        self.assertEqual(status["apis"][0]["provider"], "API-SPORTS")
        self.assertEqual(status["apis"][0]["scope"], "MLB")
        self.assertNotIn("api_key", status["apis"][0])
        settings = sports.get_settings(self.db_path)
        # The legacy URL setting may survive upgrades, but RC5 no longer uses
        # a user-entered base URL to decide which API product to call.
        self.assertEqual(settings["schedule_api_url"], "https://v1.baseball.api-sports.io")
        self.assertNotIn("__schedule_api_key", settings)

        disabled = sports.update_schedule_api_config(self.db_path, enabled=False)
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled["effective"])
        self.assertTrue(disabled["configured"])
        self.assertTrue(disabled["fallback_mode"])
        self.assertEqual(len(disabled["apis"]), 1)

        reenabled = sports.update_schedule_api_config(self.db_path, enabled=True)
        self.assertTrue(reenabled["enabled"])
        self.assertTrue(reenabled["effective"])

        removed = sports.update_schedule_api_config(
            self.db_path,
            enabled=False,
            url="",
            clear_key=True,
        )
        self.assertFalse(removed["configured"])
        self.assertEqual(len(removed["apis"]), 1)
        self.assertFalse(removed["apis"][0]["configured"])

    def test_schedule_api_disabled_or_blank_uses_legacy_path_even_with_cache(self):
        settings = sports.get_settings(self.db_path)
        timezone = ZoneInfo(settings["timezone"])
        today = datetime(2026, 8, 8, 14, 0, tzinfo=timezone)
        with sports.closing(sports._connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO sports_schedule_api_cache
                    (source, league_id, season, schedule_date, fetched_on, fetched_at, result_count, remaining_quota)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sports.SCHEDULE_API_SOURCE, "mlb", 2026, "2026-08-08", "2026-08-08", today.isoformat(), 1, 99),
            )
            conn.execute(
                """
                INSERT INTO sports_schedule_events
                    (source, api_event_id, league_id, season, schedule_date, scheduled_start,
                     status_short, status_long, home_api_id, home_name, home_logo,
                     away_api_id, away_name, away_logo, raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sports.SCHEDULE_API_SOURCE, "179771", "mlb", 2026, "2026-08-08",
                    "2026-08-08T18:05:00-04:00", "NS", "Not Started",
                    "27", "Philadelphia Phillies", "", "36", "Toronto Blue Jays", "", "{}", today.isoformat(),
                ),
            )
            conn.commit()

        # Disabled means the canonical cache is intentionally ignored.
        self.assertEqual(sports.schedule_api_events_for_window(self.db_path, today), [])

        # Enabled with a blank URL is also explicitly legacy mode.
        sports.update_schedule_api_config(self.db_path, enabled=True, url="")
        self.assertEqual(sports.schedule_api_events_for_window(self.db_path, today), [])

    def test_scan_warns_when_configured_schedule_api_supplies_no_anchors(self):
        with patch("sports.schedule_api_status", return_value={"effective": True, "plan": {"datasets": [{"id": "mlb"}]}}), patch(
            "sports.schedule_api_events_for_window", return_value=[]
        ):
            result = sports.scan_channels(
                self.db_path,
                self.channels,
                now=datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York")),
                trigger="test",
            )
        self.assertIn("supplied no canonical events", result["message"])
        self.assertTrue(result["scan_metrics"]["schedule_api_effective"])
        self.assertEqual(result["scan_metrics"]["schedule_api_events"], 0)

    def test_master_cycle_order_validator_rejects_drift(self):
        expected = [
            "schedule_api",
            "provider_refresh",
            "epg_refresh",
            "sports_scan_match",
            "channel_build",
            "epg_publish",
            "m3u_publish",
        ]
        self.assertTrue(core.validate_sports_cycle_trace(expected)["ok"])
        drifted = expected.copy()
        drifted[1], drifted[2] = drifted[2], drifted[1]
        self.assertFalse(core.validate_sports_cycle_trace(drifted)["ok"])

    def test_schedule_api_request_plan_collapses_rules_and_leaves_unsupported_sports_legacy(self):
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "nfl"})
        sports.add_rule(self.db_path, {"scope_type": "conference", "scope_id": "ncaaf-fbs:big-ten"})
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "pga-tour"})
        plan = sports.schedule_api_request_plan(self.db_path)
        self.assertEqual(plan["dataset_ids"], ["mlb", "nfl", "ncaa"])
        self.assertEqual(plan["reference_datasets"], ["ncaa_membership"])
        self.assertIn("PGA Tour", plan["legacy_rules"])
        self.assertTrue(plan["uses_legacy"])

    def test_schedule_api_request_plan_does_not_scale_with_overlapping_rules(self):
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "nfl"})
        # Provider discovery can add team rows; these two synthetic catalog rows
        # model multiple explicit team selections in the same API-backed league.
        with sports.closing(sports._connect(self.db_path)) as conn:
            sports._upsert_catalog_item(
                conn,
                scope_type="team", scope_id="nfl:philadelphia-eagles",
                display_name="Philadelphia Eagles", subtitle="NFL team • home and away games",
                league_id="nfl", aliases=["Philadelphia Eagles", "Eagles"], logo_url="",
                metadata={"sport_id": "football", "family": "Football"}, source="provider",
            )
            sports._upsert_catalog_item(
                conn,
                scope_type="team", scope_id="nfl:pittsburgh-steelers",
                display_name="Pittsburgh Steelers", subtitle="NFL team • home and away games",
                league_id="nfl", aliases=["Pittsburgh Steelers", "Steelers"], logo_url="",
                metadata={"sport_id": "football", "family": "Football"}, source="provider",
            )
            conn.commit()
        sports.add_rule(self.db_path, {"scope_type": "team", "scope_id": "nfl:philadelphia-eagles"})
        sports.add_rule(self.db_path, {"scope_type": "team", "scope_id": "nfl:pittsburgh-steelers"})
        plan = sports.schedule_api_request_plan(self.db_path)
        self.assertEqual(plan["dataset_ids"].count("nfl"), 1)

    def test_schedule_api_ncaa_team_rule_does_not_require_conference_reference_call(self):
        with sports.closing(sports._connect(self.db_path)) as conn:
            sports._upsert_catalog_item(
                conn,
                scope_type="team", scope_id="ncaaf-fbs:penn-state",
                display_name="Penn State", subtitle="NCAA team • home and away games",
                league_id="ncaaf-fbs", aliases=["Penn State"], logo_url="",
                metadata={"sport_id": "football", "family": "Football"}, source="provider",
            )
            conn.commit()
        sports.add_rule(self.db_path, {"scope_type": "team", "scope_id": "ncaaf-fbs:penn-state"})
        plan = sports.schedule_api_request_plan(self.db_path)
        self.assertIn("ncaa", plan["dataset_ids"])
        self.assertNotIn("ncaa_membership", plan["reference_datasets"])

    def test_schedule_api_unsupported_only_selection_creates_no_api_dataset(self):
        with sports.closing(sports._connect(self.db_path)) as conn:
            conn.execute("DELETE FROM sports_rules")
            conn.commit()
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "pga-tour"})
        sports.add_rule(self.db_path, {"scope_type": "league", "scope_id": "world-athletics"})
        plan = sports.schedule_api_request_plan(self.db_path)
        self.assertEqual(plan["dataset_ids"], [])
        self.assertEqual(set(plan["legacy_rules"]), {"PGA Tour", "World Athletics"})

    def test_schedule_api_american_football_urls_use_one_league_day_dataset(self):
        for dataset_id, league_id in (("nfl", 1), ("ncaa", 2)):
            dataset = sports.SCHEDULE_API_DATASETS[dataset_id]
            url = sports._schedule_api_dataset_games_url(
                dataset,
                schedule_date=datetime(2026, 9, 12).date(),
                season=2026,
                timezone="America/New_York",
            )
            self.assertIn("/games?", url)
            self.assertIn(f"league={league_id}", url)
            self.assertIn("season=2026", url)
            self.assertIn("date=2026-09-12", url)
            self.assertIn("timezone=America%2FNew_York", url)

    def test_schedule_api_request_key_includes_timezone_and_exact_parameters(self):
        dataset = sports.SCHEDULE_API_DATASETS["nfl"]
        eastern = sports._schedule_api_request_key(
            dataset,
            schedule_date=datetime(2026, 9, 12).date(),
            season=2026,
            timezone="America/New_York",
        )
        central = sports._schedule_api_request_key(
            dataset,
            schedule_date=datetime(2026, 9, 12).date(),
            season=2026,
            timezone="America/Chicago",
        )
        self.assertNotEqual(eastern, central)
        payload = json.loads(eastern)
        self.assertEqual(payload["provider"], "api_sports")
        self.assertEqual(payload["product"], "american_football")
        self.assertEqual(payload["endpoint"], "games")
        self.assertEqual(payload["parameters"]["league"], "1")
        self.assertEqual(payload["parameters"]["season"], "2026")
        self.assertEqual(payload["parameters"]["timezone"], "America/New_York")

    def test_schedule_api_american_football_start_year_season(self):
        dataset = sports.SCHEDULE_API_DATASETS["nfl"]
        self.assertEqual(
            sports._schedule_api_dataset_season(
                dataset, datetime(2026, 1, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
            ),
            2025,
        )
        self.assertEqual(
            sports._schedule_api_dataset_season(
                dataset, datetime(2026, 9, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
            ),
            2026,
        )

    def test_schedule_api_parses_american_football_game_shape(self):
        game = {
            "game": {
                "id": 17377,
                "date": {"date": "2026-09-13", "time": "13:00", "timestamp": 1789322400, "timezone": "UTC"},
                "status": {"short": "NS", "long": "Not Started"},
            },
            "league": {"id": 1, "name": "NFL", "season": "2026"},
            "teams": {
                "home": {"id": 10, "name": "Philadelphia Eagles", "logo": "home.png"},
                "away": {"id": 20, "name": "Pittsburgh Steelers", "logo": "away.png"},
            },
        }
        fields = sports._schedule_api_game_fields(
            sports.SCHEDULE_API_DATASETS["nfl"], game, "America/New_York"
        )
        self.assertEqual(fields["event_id"], "17377")
        self.assertEqual(fields["home"]["name"], "Philadelphia Eagles")
        self.assertEqual(fields["away"]["name"], "Pittsburgh Steelers")
        self.assertEqual(fields["status_short"], "NS")
        self.assertTrue(fields["scheduled_start"])

    def test_schedule_api_anchor_identity_is_not_mlb_only(self):
        with sports.closing(sports._connect(self.db_path)) as conn:
            for scope_id, name in (
                ("nfl:philadelphia-eagles", "Philadelphia Eagles"),
                ("nfl:pittsburgh-steelers", "Pittsburgh Steelers"),
            ):
                sports._upsert_catalog_item(
                    conn,
                    scope_type="team", scope_id=scope_id,
                    display_name=name, subtitle="NFL team • home and away games",
                    league_id="nfl", aliases=[name], logo_url="",
                    metadata={"sport_id": "football", "family": "Football"}, source="api-sports",
                )
            conn.commit()
        team_lookup = sports._build_team_lookup(self.db_path)
        settings = sports.get_settings(self.db_path)
        raw_api = [{
            "api_source": "api-sports-american-football",
            "api_dataset": "nfl",
            "api_event_id": "17377",
            "league_id": "nfl",
            "sport_id": "football",
            "scheduled_start": datetime(2026, 9, 13, 13, 0, tzinfo=ZoneInfo("America/New_York")),
            "status_short": "NS",
            "status_long": "Not Started",
            "home_api_id": "10",
            "home_name": "Philadelphia Eagles",
            "home_logo": "",
            "away_api_id": "20",
            "away_name": "Pittsburgh Steelers",
            "away_logo": "",
        }]
        anchors = sports._schedule_api_anchor_events(raw_api, settings, team_lookup)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["league_id"], "nfl")
        self.assertEqual(anchors[0]["event_identity"], "api-sports-american-football:17377")
        provider = {
            "league_id": "nfl",
            "away_team_id": "nfl:pittsburgh-steelers",
            "away_team_name": "Pittsburgh Steelers",
            "home_team_id": "nfl:philadelphia-eagles",
            "home_team_name": "Philadelphia Eagles",
            "display_name": "Pittsburgh Steelers at Philadelphia Eagles",
            "start": datetime(2026, 9, 13, 13, 2, tzinfo=ZoneInfo("America/New_York")),
        }
        mapped = sports._apply_schedule_api_identity([provider], anchors)
        self.assertTrue(mapped[0]["has_schedule_api_identity"])
        self.assertEqual(mapped[0]["event_identity"], "api-sports-american-football:17377")

    def test_schedule_api_same_day_cache_prevents_repeat_fetch(self):
        sports.update_schedule_api_config(self.db_path, enabled=True, api_key="secret")
        now = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        original_fetch = sports._fetch_schedule_api_dataset_date

        def fake_fetch(db_path, *, dataset, api_key, schedule_date, season, timezone, fetched_on, cancel_check=None):
            fetched_at = now.isoformat()
            with sports.closing(sports._connect(db_path)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sports_schedule_api_cache
                        (source, league_id, season, schedule_date, request_key, fetched_on, fetched_at, result_count, remaining_quota)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset["source"], dataset["league_id"], season, schedule_date.isoformat(),
                        sports._schedule_api_request_key(
                            dataset, schedule_date=schedule_date, season=season, timezone=timezone
                        ),
                        fetched_on, fetched_at, 0, 99,
                    ),
                )
                conn.commit()
            return {"dataset": dataset["id"], "date": schedule_date.isoformat(), "games": 0, "fetched_at": fetched_at}

        with patch("sports._fetch_schedule_api_dataset_date", side_effect=fake_fetch) as fetch:
            first = sports.refresh_schedule_api_if_due(self.db_path, now)
            second = sports.refresh_schedule_api_if_due(self.db_path, now)
        self.assertGreaterEqual(len(first["fetched"]), 1)
        self.assertEqual(len(second["fetched"]), 0)
        self.assertGreaterEqual(len(second["cached"]), 1)
        self.assertEqual(fetch.call_count, len(first["fetched"]))

    def test_schedule_api_successful_current_empty_cache_is_authoritative(self):
        sports.update_schedule_api_config(self.db_path, enabled=True, api_key="secret")
        now = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        dataset = sports.SCHEDULE_API_DATASETS["mlb"]
        season = sports._schedule_api_dataset_season(dataset, now)
        required_dates = sports._schedule_api_required_dates(now, sports.get_settings(self.db_path))
        with sports.closing(sports._connect(self.db_path)) as conn:
            for schedule_date in required_dates:
                request_key = sports._schedule_api_request_key(
                    dataset, schedule_date=schedule_date, season=season, timezone="America/New_York"
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sports_schedule_api_cache
                        (source, league_id, season, schedule_date, request_key, fetched_on, fetched_at, result_count, remaining_quota)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset["source"], dataset["league_id"], season, schedule_date.isoformat(),
                        request_key, now.date().isoformat(), now.isoformat(), 0, 99,
                    ),
                )
            conn.commit()
        self.assertEqual(sports._schedule_api_authoritative_leagues(self.db_path, now), {"mlb"})

        # A stale empty cache after today's API failure is not authoritative.
        with sports.closing(sports._connect(self.db_path)) as conn:
            conn.execute(
                "UPDATE sports_schedule_api_cache SET fetched_on = ? WHERE source = ? AND league_id = ?",
                ((now.date() - timedelta(days=1)).isoformat(), dataset["source"], dataset["league_id"]),
            )
            conn.commit()
        self.assertEqual(sports._schedule_api_authoritative_leagues(self.db_path, now), set())

    def test_authoritative_schedule_suppresses_unmapped_current_provider_games_only(self):
        provider_events = [
            {"league_id": "mlb", "display_name": "1987 Phillies Classic"},
            {"league_id": "mlb", "display_name": "Current Phillies Game", "has_schedule_api_identity": True},
            {"league_id": "cornhole", "display_name": "Weird provider sport"},
        ]
        filtered = sports._filter_provider_events_by_authoritative_schedule(
            provider_events, {"mlb"}, include_replays=False
        )
        self.assertEqual(
            [item["display_name"] for item in filtered],
            ["Current Phillies Game", "Weird provider sport"],
        )
        self.assertEqual(
            sports._filter_provider_events_by_authoritative_schedule(
                provider_events, {"mlb"}, include_replays=True
            ),
            provider_events,
        )

    def test_schedule_api_games_url_targets_mlb_day(self):
        url = sports._schedule_api_games_url(
            "https://v1.baseball.api-sports.io",
            schedule_date=datetime(2026, 8, 8).date(),
            season=2026,
            timezone="America/New_York",
        )
        self.assertIn("/games?", url)
        self.assertNotIn("league=", url)
        self.assertNotIn("season=", url)
        self.assertIn("date=2026-08-08", url)
        self.assertIn("timezone=America%2FNew_York", url)

    def test_schedule_api_canonical_id_collapses_provider_replay_airings(self):
        settings = sports.get_settings(self.db_path)
        settings["include_replays"] = False
        team_lookup = sports._build_team_lookup(self.db_path)
        raw_api = [{
            "api_source": sports.SCHEDULE_API_SOURCE,
            "api_event_id": "179771",
            "league_id": "mlb",
            "season": 2026,
            "scheduled_start": datetime(2026, 8, 8, 18, 5, tzinfo=ZoneInfo("America/New_York")),
            "status_short": "NS",
            "status_long": "Not Started",
            "home_api_id": "27",
            "home_name": "Philadelphia Phillies",
            "home_logo": "",
            "away_api_id": "36",
            "away_name": "Toronto Blue Jays",
            "away_logo": "",
        }]
        anchors = sports._schedule_api_anchor_events(raw_api, settings, team_lookup)
        self.assertEqual(len(anchors), 1)
        channel = {
            "name": "MLB Toronto Blue Jays at Philadelphia Phillies",
            "tvg_name": "",
            "group": "MLB",
            "tvg_id": "event",
            "url": "http://provider.test/event.ts",
        }
        live = sports._event_from_text(
            self.db_path,
            channel,
            "MLB Toronto Blue Jays at Philadelphia Phillies",
            settings,
            datetime(2026, 8, 8, 14, 0, tzinfo=ZoneInfo("America/New_York")),
            forced_start=datetime(2026, 8, 8, 18, 5, tzinfo=ZoneInfo("America/New_York")),
            forced_end=datetime(2026, 8, 8, 21, 5, tzinfo=ZoneInfo("America/New_York")),
            team_lookup=team_lookup,
        )
        replay = sports._event_from_text(
            self.db_path,
            channel,
            "MLB Toronto Blue Jays at Philadelphia Phillies",
            {**settings, "include_replays": True},
            datetime(2026, 8, 8, 14, 0, tzinfo=ZoneInfo("America/New_York")),
            forced_start=datetime(2026, 8, 9, 0, 30, tzinfo=ZoneInfo("America/New_York")),
            forced_end=datetime(2026, 8, 9, 3, 0, tzinfo=ZoneInfo("America/New_York")),
            team_lookup=team_lookup,
        )
        mapped = sports._apply_schedule_api_identity([live, replay], anchors)
        self.assertEqual({item["event_identity"] for item in mapped}, {"api-sports-baseball:179771"})
        merged = sports._merge_events([*anchors, *mapped], settings=settings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["api_event_id"], "179771")
        self.assertEqual(merged[0]["event_key"], "api-sports-baseball:179771")
        self.assertEqual(merged[0]["start"].hour, 18)
        self.assertEqual(merged[0]["start"].minute, 5)

    def _api_phillies_anchor(self, *, include_replays=False):
        settings = sports.get_settings(self.db_path)
        settings["include_replays"] = include_replays
        team_lookup = sports._build_team_lookup(self.db_path)
        raw_api = [{
            "api_source": sports.SCHEDULE_API_SOURCE,
            "api_event_id": "179771",
            "league_id": "mlb",
            "season": 2026,
            "scheduled_start": datetime(2026, 8, 8, 18, 5, tzinfo=ZoneInfo("America/New_York")),
            "status_short": "NS",
            "status_long": "Not Started",
            "home_api_id": "27",
            "home_name": "Philadelphia Phillies",
            "home_logo": "",
            "away_api_id": "36",
            "away_name": "Toronto Blue Jays",
            "away_logo": "",
        }]
        anchors = sports._schedule_api_anchor_events(raw_api, settings, team_lookup)
        return settings, team_lookup, anchors

    def _api_epg_event(self, settings, team_lookup, title, start, stop, *, is_live=False, description="", url="event.ts"):
        channel = {
            "name": title,
            "tvg_name": title,
            "group": "MLB",
            "tvg_id": url,
            "url": f"http://provider.test/{url}",
        }
        event = sports._event_from_text(
            self.db_path,
            channel,
            title,
            settings,
            datetime(2026, 8, 8, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            forced_start=start,
            forced_end=stop,
            team_lookup=team_lookup,
        )
        self.assertIsNotNone(event)
        event["timing_source"] = "xmltv"
        event["source_kind"] = "xmltv"
        event["source_kinds"] = ["xmltv"]
        event["epg_programme"] = {
            "title": title,
            "description": description,
            "start": start,
            "stop": stop,
            "is_live": is_live,
            "is_replay": False,
        }
        return event

    def test_legacy_matching_uses_live_xmltv_to_drop_gameday_and_late_rebroadcast(self):
        settings = sports.get_settings(self.db_path)
        settings["include_replays"] = False
        team_lookup = sports._build_team_lookup(self.db_path)
        tz = ZoneInfo("America/New_York")
        gameday = self._api_epg_event(
            settings, team_lookup,
            "Toronto Blue Jays vs. Philadelphia Phillies MLB In-Game Live Gameday",
            datetime(2026, 8, 8, 18, 0, tzinfo=tz),
            datetime(2026, 8, 8, 19, 0, tzinfo=tz),
            description="Live wagering coverage with updated odds and player props.",
            url="legacy-gameday.ts",
        )
        live = self._api_epg_event(
            settings, team_lookup,
            "Toronto Blue Jays @ Philadelphia Phillies",
            datetime(2026, 8, 8, 18, 5, tzinfo=tz),
            datetime(2026, 8, 8, 21, 5, tzinfo=tz),
            is_live=True,
            description="Game 2 of 3 Live from Citizens Bank Park.",
            url="legacy-live.ts",
        )
        rebroadcast = self._api_epg_event(
            settings, team_lookup,
            "MLB Baseball : Toronto Blue Jays at Philadelphia Phillies",
            datetime(2026, 8, 8, 23, 0, tzinfo=tz),
            datetime(2026, 8, 9, 1, 30, tzinfo=tz),
            description="The Philadelphia Phillies host the Toronto Blue Jays in Game 2 of a three-game MLB interleague series.",
            url="legacy-late.ts",
        )
        merged = sports._merge_events([gameday, live, rebroadcast], settings=settings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start"], datetime(2026, 8, 8, 18, 5, tzinfo=tz))
        self.assertEqual(merged[0]["epg_programme"]["title"], "Toronto Blue Jays @ Philadelphia Phillies")
        self.assertEqual(merged[0].get("epg_programmes", []), [])

        settings["include_replays"] = True
        merged_with_replays = sports._merge_events([gameday, live, rebroadcast], settings=settings)
        self.assertEqual(len(merged_with_replays), 1)
        self.assertEqual(len(merged_with_replays[0].get("epg_programmes", [])), 1)
        self.assertEqual(
            merged_with_replays[0]["epg_programmes"][0]["start"],
            datetime(2026, 8, 8, 23, 0, tzinfo=tz),
        )
        self.assertTrue(merged_with_replays[0]["epg_programmes"][0]["is_replay"])

    def test_legacy_matching_collapses_five_second_live_duplicate_with_series_description(self):
        settings = sports.get_settings(self.db_path)
        settings["include_replays"] = False
        team_lookup = sports._build_team_lookup(self.db_path)
        tz = ZoneInfo("America/New_York")
        primary = self._api_epg_event(
            settings, team_lookup,
            "Los Angeles Angels @ Miami Marlins",
            datetime(2026, 8, 8, 16, 10, tzinfo=tz),
            datetime(2026, 8, 8, 19, 10, tzinfo=tz),
            is_live=True,
            description="Game 2 of 3 Live from loanDepot park.",
            url="marlins-live.ts",
        )
        near_duplicate = self._api_epg_event(
            settings, team_lookup,
            "MLB Baseball : Los Angeles Angels at Miami Marlins",
            datetime(2026, 8, 8, 16, 10, 5, tzinfo=tz),
            datetime(2026, 8, 8, 20, 10, 5, tzinfo=tz),
            is_live=True,
            description="Los Angeles Angels at Miami Marlins.",
            url="marlins-event.ts",
        )
        self.assertEqual(primary["event_identity"], near_duplicate["event_identity"])
        merged = sports._merge_events([primary, near_duplicate], settings=settings)
        self.assertEqual(len(merged), 1)

    def test_schedule_api_uses_live_game_and_drops_gameday_and_11pm_rebroadcast(self):
        settings, team_lookup, anchors = self._api_phillies_anchor(include_replays=False)
        tz = ZoneInfo("America/New_York")
        gameday = self._api_epg_event(
            settings, team_lookup,
            "Toronto Blue Jays vs. Philadelphia Phillies MLB In-Game Live Gameday",
            datetime(2026, 8, 8, 18, 0, tzinfo=tz),
            datetime(2026, 8, 8, 19, 0, tzinfo=tz),
            description="Live wagering coverage with updated odds and player props.",
            url="gameday.ts",
        )
        live = self._api_epg_event(
            settings, team_lookup,
            "Toronto Blue Jays @ Philadelphia Phillies",
            datetime(2026, 8, 8, 18, 5, tzinfo=tz),
            datetime(2026, 8, 8, 21, 5, tzinfo=tz),
            is_live=True,
            description="Game 2 of 3 Live from Citizens Bank Park.",
            url="live.ts",
        )
        rebroadcast = self._api_epg_event(
            settings, team_lookup,
            "MLB Baseball : Toronto Blue Jays at Philadelphia Phillies",
            datetime(2026, 8, 8, 23, 0, tzinfo=tz),
            datetime(2026, 8, 9, 1, 30, tzinfo=tz),
            description="Later broadcast of the same game.",
            url="late.ts",
        )
        mapped = sports._apply_schedule_api_identity([gameday, live, rebroadcast], anchors)
        merged = sports._merge_events([*anchors, *mapped], settings=settings)
        self.assertEqual(len(merged), 1)
        event = merged[0]
        self.assertEqual(event["event_key"], "api-sports-baseball:179771")
        self.assertEqual(event["start"], datetime(2026, 8, 8, 18, 5, tzinfo=tz))
        self.assertEqual(event["epg_programme"]["title"], "Toronto Blue Jays @ Philadelphia Phillies")
        self.assertEqual([item["url"] for item in event["source_channels"]], ["http://provider.test/live.ts"])
        self.assertEqual(event.get("epg_programmes", []), [])

    def test_schedule_api_matches_utc_xmltv_start_to_eastern_anchor(self):
        settings, team_lookup, anchors = self._api_phillies_anchor(include_replays=False)
        utc = ZoneInfo("UTC")
        live = self._api_epg_event(
            settings,
            team_lookup,
            "Toronto Blue Jays @ Philadelphia Phillies",
            datetime(2026, 8, 8, 22, 5, tzinfo=utc),
            datetime(2026, 8, 9, 1, 5, tzinfo=utc),
            is_live=True,
            description="Game 2 of 3 Live from Citizens Bank Park.",
            url="utc-live.ts",
        )
        mapped = sports._apply_schedule_api_identity([live], anchors)
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["api_event_id"], "179771")
        merged = sports._merge_events([*anchors, *mapped], settings=settings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["event_key"], "api-sports-baseball:179771")
        self.assertEqual(
            merged[0]["start"].astimezone(ZoneInfo("America/New_York")),
            datetime(2026, 8, 8, 18, 5, tzinfo=ZoneInfo("America/New_York")),
        )

    def test_series_game_number_in_description_does_not_split_event_identity(self):
        settings = sports.get_settings(self.db_path)
        team_lookup = sports._build_team_lookup(self.db_path)
        tz = ZoneInfo("America/New_York")
        base = sports._event_from_text(
            self.db_path,
            {
                "name": "Toronto Blue Jays @ Philadelphia Phillies",
                "tvg_name": "",
                "group": "MLB",
                "tvg_id": "phillies-live",
                "url": "http://provider.test/live.ts",
            },
            "Toronto Blue Jays @ Philadelphia Phillies",
            settings,
            datetime(2026, 8, 8, 17, 0, tzinfo=tz),
            forced_start=datetime(2026, 8, 8, 18, 5, tzinfo=tz),
            forced_end=datetime(2026, 8, 8, 21, 5, tzinfo=tz),
            extra_text="Game 2 of 3 Live from Citizens Bank Park.",
            team_lookup=team_lookup,
        )
        self.assertIsNotNone(base)
        self.assertNotIn(":game-2", base["event_identity"])

        doubleheader = sports._event_from_text(
            self.db_path,
            {
                "name": "Game 2 Toronto Blue Jays @ Philadelphia Phillies",
                "tvg_name": "",
                "group": "MLB",
                "tvg_id": "phillies-doubleheader",
                "url": "http://provider.test/game2.ts",
            },
            "Game 2 Toronto Blue Jays @ Philadelphia Phillies",
            settings,
            datetime(2026, 8, 8, 17, 0, tzinfo=tz),
            forced_start=datetime(2026, 8, 8, 21, 45, tzinfo=tz),
            forced_end=datetime(2026, 8, 9, 0, 45, tzinfo=tz),
            team_lookup=team_lookup,
        )
        self.assertIsNotNone(doubleheader)
        self.assertIn(":game-2", doubleheader["event_identity"])

    def test_schedule_api_replays_on_attaches_11pm_to_same_event_id(self):
        settings, team_lookup, anchors = self._api_phillies_anchor(include_replays=True)
        tz = ZoneInfo("America/New_York")
        live = self._api_epg_event(
            settings, team_lookup,
            "Toronto Blue Jays @ Philadelphia Phillies",
            datetime(2026, 8, 8, 18, 5, tzinfo=tz),
            datetime(2026, 8, 8, 21, 5, tzinfo=tz),
            is_live=True, url="live.ts",
        )
        rebroadcast = self._api_epg_event(
            settings, team_lookup,
            "MLB Baseball : Toronto Blue Jays at Philadelphia Phillies",
            datetime(2026, 8, 8, 23, 0, tzinfo=tz),
            datetime(2026, 8, 9, 1, 30, tzinfo=tz),
            url="late.ts",
        )
        mapped = sports._apply_schedule_api_identity([live, rebroadcast], anchors)
        merged = sports._merge_events([*anchors, *mapped], settings=settings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["event_key"], "api-sports-baseball:179771")
        self.assertEqual(len(merged[0].get("epg_programmes", [])), 1)
        replay = merged[0]["epg_programmes"][0]
        self.assertEqual(replay["start"], datetime(2026, 8, 8, 23, 0, tzinfo=tz))
        self.assertTrue(replay["is_replay"])

    def test_schedule_api_ignores_early_squeeze_play_as_game_source(self):
        settings = sports.get_settings(self.db_path)
        team_lookup = sports._build_team_lookup(self.db_path)
        tz = ZoneInfo("America/New_York")
        raw_api = [{
            "api_source": sports.SCHEDULE_API_SOURCE,
            "api_event_id": "179778",
            "league_id": "mlb",
            "season": 2026,
            "scheduled_start": datetime(2026, 8, 8, 20, 10, tzinfo=tz),
            "status_short": "NS",
            "status_long": "Not Started",
            "home_api_id": "2",
            "home_name": "Arizona Diamondbacks",
            "home_logo": "",
            "away_api_id": "18",
            "away_name": "Los Angeles Dodgers",
            "away_logo": "",
        }]
        anchors = sports._schedule_api_anchor_events(raw_api, settings, team_lookup)
        squeeze = self._api_epg_event(
            settings, team_lookup,
            "Dodgers Squeeze Play : Dodgers @ Diamondbacks",
            datetime(2026, 8, 8, 15, 0, tzinfo=tz),
            datetime(2026, 8, 8, 16, 0, tzinfo=tz),
            description="From Aug. 7, 2026.",
            url="squeeze.ts",
        )
        mapped = sports._apply_schedule_api_identity([squeeze], anchors)
        merged = sports._merge_events([*anchors, *mapped], settings=settings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["event_key"], "api-sports-baseball:179778")
        self.assertEqual(merged[0]["start"], datetime(2026, 8, 8, 20, 10, tzinfo=tz))
        self.assertEqual(merged[0].get("source_channels", []), [])

    def test_successful_scan_reports_internal_match_build_publish_order(self):
        result = sports.scan_channels(
            self.db_path,
            self.channels,
            now=datetime(2026, 8, 1, 20, 0, tzinfo=ZoneInfo("America/New_York")),
            trigger="test",
        )
        self.assertEqual(
            result["pipeline_trace"],
            ["sports_scan_match", "channel_build", "epg_publish"],
        )

    def test_epg_output_is_top_level_and_public_country_selector_remains(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "templates" / "index.html").read_text(encoding="utf-8")
        javascript = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("EPG Manager", html)
        self.assertNotIn('id="epgSources"', html)
        self.assertNotIn('id="combinedEpgUrl"', html)
        self.assertIn('id="epgOutputUrl"', html)
        self.assertIn('id="copyEpgBtn"', html)
        self.assertIn('id="manageOrderBtn"', html)
        self.assertIn('id="tvGuideLink"', html)
        self.assertIn("{{ url_for('guide') }}", html)
        self.assertGreater(html.index('id="manageOrderBtn"'), html.index('id="epgOutputUrl"'))
        self.assertIn('Include replays and classic games', html)
        self.assertIn('id="publicEpgDetails"', html)
        self.assertIn('id="publicEpgCountries"', html)
        self.assertIn('id="sportsSelectionType"', html)
        self.assertIn('<option value="league">League</option>', html)
        self.assertNotIn("League / series", html)
        self.assertIn('href="https://api-sports.io"', html)
        self.assertIn('id="sportsScheduleApiKey"', html)
        self.assertNotIn('id="sportsScheduleApiUrl"', html)
        self.assertIn('id="sportsScheduleApiRefresh"', html)
        self.assertIn('id="sportsScheduleApiRemove"', html)
        self.assertIn('button.disabled = masterRunning', javascript)
        self.assertIn("savePublicEpgSelections", javascript)
        self.assertIn("runMasterUpdate", javascript)
        self.assertIn("/playlist/channels.m3u", javascript)
        self.assertIn("/epg/epg.xml", javascript)
        self.assertIn("v='v30-experiments-exp12-schedule-api-state'", html)
        self.assertIn('id="masterUpdateEnabled"', html)
        self.assertIn('id="masterUpdateTime"', html)
        self.assertIn('id="masterUpdateNowBtn"', html)
        self.assertNotIn('id="sportsScheduleMode"', html)
        self.assertNotIn('id="sportsIntervalHours"', html)
        self.assertNotIn("Every X hours", html)


    def test_public_epg_filter_keeps_selected_non_sports_channel(self):
        original_channels = core.channels
        original_selected_ids = core.selected_ids
        try:
            core.channels = [{
                "id": 42,
                "tvg_id": "NBCWGAL.us",
                "tvg_name": "PA | Lancaster | NBC WGAL",
                "name": "PA | Lancaster | NBC WGAL",
                "group": "Local",
                "url": "http://provider.test/wgal",
            }]
            core.selected_ids = {42}
            with patch("core.selected_xmltv_ids", return_value={"NBCWGAL.us"}):
                wanted_ids, wanted_names = core._public_epg_relevant_matchers()
        finally:
            core.channels = original_channels
            core.selected_ids = original_selected_ids

        self.assertIn("NBCWGAL.us", wanted_ids)
        self.assertIn(sports._normalize("PA | Lancaster | NBC WGAL"), wanted_names)

    def test_public_epg_registry_defaults_to_compressed_us_guide(self):
        self.assertIn(("US", "United States"), core.PUBLIC_EPG_REGISTRY)
        self.assertEqual(core.public_epg_url("US"), "https://iptv-epg.org/files/epg-us.xml.gz")

    def test_gzip_public_epg_respects_provider_precedence(self):
        import gzip

        base = Path(self.temp.name) / "provider.xml"
        fallback = Path(self.temp.name) / "public.xml.gz"
        sports_xml = b'<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
        base.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><tv>'
            '<channel id="PhiladelphiaPhillies.mlb"><display-name>Phillies</display-name></channel>'
            '</tv>',
            encoding="utf-8",
        )
        with gzip.open(fallback, "wt", encoding="utf-8") as handle:
            handle.write(
                '<?xml version="1.0" encoding="UTF-8"?><tv>'
                '<channel id="PhiladelphiaPhillies.mlb"><display-name>Phillies Public</display-name></channel>'
                '<programme channel="PhiladelphiaPhillies.mlb" start="20260808180500 -0400" stop="20260808210500 -0400">'
                '<title>Toronto Blue Jays @ Philadelphia Phillies</title></programme>'
                '</tv>'
            )

        combined = sports.build_combined_xmltv(
            base,
            sports_xml,
            {"PhiladelphiaPhillies.mlb"},
            fallback_epg_paths=[fallback],
        )
        text = combined.decode("utf-8")
        self.assertIn("Toronto Blue Jays @ Philadelphia Phillies", text)
        self.assertEqual(text.count('channel id="PhiladelphiaPhillies.mlb"'), 1)

        base.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><tv>'
            '<channel id="PhiladelphiaPhillies.mlb"><display-name>Phillies</display-name></channel>'
            '<programme channel="PhiladelphiaPhillies.mlb" start="20260808180500 -0400" stop="20260808210500 -0400">'
            '<title>Provider Phillies Game</title></programme>'
            '</tv>',
            encoding="utf-8",
        )
        combined = sports.build_combined_xmltv(
            base,
            sports_xml,
            {"PhiladelphiaPhillies.mlb"},
            fallback_epg_paths=[fallback],
        )
        text = combined.decode("utf-8")
        self.assertIn("Provider Phillies Game", text)
        self.assertNotIn("Toronto Blue Jays @ Philadelphia Phillies", text)

    def test_public_epg_fills_non_sports_gaps_without_overwriting_provider_programmes(self):
        import gzip

        base = Path(self.temp.name) / "provider-local.xml"
        fallback = Path(self.temp.name) / "public-local.xml.gz"
        sports_xml = b'<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
        base.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><tv>'
            '<channel id="NBCWGAL.us"><display-name>PA | Lancaster | NBC WGAL</display-name></channel>'
            '<programme channel="NBCWGAL.us" start="20260808120000 +0000" stop="20260808130000 +0000">'
            '<title>Provider Morning News</title></programme>'
            '</tv>',
            encoding="utf-8",
        )
        with gzip.open(fallback, "wt", encoding="utf-8") as handle:
            handle.write(
                '<?xml version="1.0" encoding="UTF-8"?><tv>'
                '<channel id="NBCWGAL.us"><display-name>NBC WGAL</display-name></channel>'
                '<programme channel="NBCWGAL.us" start="20260808123000 +0000" stop="20260808133000 +0000">'
                '<title>Public Overlap Should Lose</title></programme>'
                '<programme channel="NBCWGAL.us" start="20260808133000 +0000" stop="20260808140000 +0000">'
                '<title>Public Local Gap Filler</title></programme>'
                '</tv>'
            )

        combined = sports.build_combined_xmltv(
            base,
            sports_xml,
            {"NBCWGAL.us"},
            fallback_epg_paths=[fallback],
        )
        text = combined.decode("utf-8")
        self.assertIn("Provider Morning News", text)
        self.assertIn("Public Local Gap Filler", text)
        self.assertNotIn("Public Overlap Should Lose", text)
        self.assertEqual(text.count('channel id="NBCWGAL.us"'), 1)


if __name__ == "__main__":
    unittest.main()

