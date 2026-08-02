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


FIXTURE = """#EXTM3U
#EXTINF:-1 tvg-id="" tvg-name="" tvg-logo="https://example.test/mlb.png" group-title="MLB / MiLB",(MLB 12) | Philadelphia Phillies @ Baltimore Orioles (2026-08-01 19:05:00)
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
        self.assertIn('tvg-logo="https://example.test/phillies.png"', rows[0]["raw"][0])

    def test_empty_successful_scan_removes_old_generated_channels(self):
        before = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=before, trigger="test")
        self.assertEqual(len(sports.generated_rows(self.db_path)), 3)

        after = datetime(2026, 8, 2, 4, 0, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, self.channels, now=after, trigger="test")
        self.assertEqual(result["count"], 0)
        self.assertEqual(sports.generated_rows(self.db_path), [])

    def test_generic_nfl_slot_is_not_treated_as_a_game(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        names = [row["display_name"] for row in sports.generated_rows(self.db_path)]
        self.assertFalse(any("NFL 01" in name for name in names))


    def test_scheduled_scan_runs_after_pre_boundary_manual_scan(self):
        before = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=before, trigger="manual")
        at_refresh = datetime(2026, 8, 2, 3, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertTrue(sports.should_run_scheduled(self.db_path, at_refresh))

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
  <programme channel="mlb.event" start="20260801190500 -0400" stop="20260801220500 -0400">
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

    def test_fresh_database_has_no_assumed_sports_rules(self):
        fresh = Path(self.temp.name) / "fresh.db"
        sports.init_db(fresh)
        self.assertEqual(sports.get_rules(fresh), [])

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


if __name__ == "__main__":
    unittest.main()
