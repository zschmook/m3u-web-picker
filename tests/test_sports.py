from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree

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
        self.assertEqual(ids, ["m3u-picker-sports-1000", "m3u-picker-sports-1001", "m3u-picker-sports-1002"])
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

        first_pitch = datetime(2026, 8, 1, 19, 5, tzinfo=ZoneInfo("America/New_York"))
        for row in sports.generated_rows(self.db_path):
            covering = []
            for programme in root.findall(f"programme[@channel='{row['tvg_id']}']"):
                start = sports._parse_xmltv_time(programme.attrib["start"], first_pitch.tzinfo)
                stop = sports._parse_xmltv_time(programme.attrib["stop"], first_pitch.tzinfo)
                if start <= first_pitch < stop:
                    covering.append(programme)
            self.assertEqual(len(covering), 1)

    def test_zero_event_scan_writes_valid_empty_sports_guide(self):
        sports_epg = Path(self.temp.name) / "sports-empty.xml"
        combined_epg = Path(self.temp.name) / "combined-empty.xml"
        after = datetime(2026, 8, 2, 4, 0, tzinfo=ZoneInfo("America/New_York"))
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

        after = datetime(2026, 8, 2, 4, 0, tzinfo=ZoneInfo("America/New_York"))
        result = sports.scan_channels(self.db_path, self.channels, now=after, trigger="test")
        self.assertEqual(result["count"], 0)
        self.assertEqual(sports.generated_rows(self.db_path), [])

    def test_generic_nfl_slot_is_not_treated_as_a_game(self):
        now = datetime(2026, 8, 2, 2, 30, tzinfo=ZoneInfo("America/New_York"))
        sports.scan_channels(self.db_path, self.channels, now=now, trigger="test")
        names = [row["display_name"] for row in sports.generated_rows(self.db_path)]
        self.assertFalse(any("NFL 01" in name for name in names))

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

    def test_malformed_xmltv_program_is_skipped_and_later_programs_continue(self):
        epg = Path(self.temp.name) / "malformed-epg.xml"
        epg.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="mlb.event"><display-name>MLB Event</display-name></channel>
  <programme channel="mlb.event" start="20260801990500 -0400">
    <title>Bad Team at Worse Team</title><category>MLB</category>
  </programme>
  <programme channel="mlb.event" start="20260801190500 -0400">
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

    def test_stale_event_timestamp_still_has_guide_at_actual_game_window(self):
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
        self.assertEqual(len(covering), 1)
        self.assertIn("Philadelphia Phillies at Baltimore Orioles", covering[0].findtext("title", default=""))

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
        row = sports.generated_rows(legacy)[0]
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


if __name__ == "__main__":
    unittest.main()
