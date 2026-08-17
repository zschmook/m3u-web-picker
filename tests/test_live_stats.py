import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from sports import live_stats
from sports import live_stats_transport
from sports import mlb_live_source
from sports import mlb_stats_companions


live_stats_transport.install(live_stats)


class LiveStatsTests(unittest.TestCase):
    def test_normalize_mlb_summary_reads_live_situation(self):
        summary = {
            "header": {
                "competitions": [
                    {
                        "status": {
                            "period": 7,
                            "displayClock": "",
                            "type": {"state": "in", "shortDetail": "Top 7th"},
                        },
                        "competitors": [
                            {
                                "homeAway": "away",
                                "score": "4",
                                "team": {"displayName": "Philadelphia Phillies", "abbreviation": "PHI"},
                                "linescores": [{"displayValue": str(value)} for value in [1, 0, 0, 2, 0, 1]],
                            },
                            {
                                "homeAway": "home",
                                "score": "3",
                                "team": {"displayName": "Los Angeles Dodgers", "abbreviation": "LAD"},
                                "linescores": [{"displayValue": str(value)} for value in [0, 1, 0, 0, 2, 0]],
                            },
                        ],
                    }
                ]
            },
            "situation": {
                "balls": 2,
                "strikes": 1,
                "outs": 1,
                "onFirst": True,
                "onSecond": False,
                "onThird": True,
                "batter": {"athlete": {"shortName": "B. Harper"}},
                "pitcher": {"athlete": {"shortName": "S. Ohtani"}},
                "lastPlay": {"text": "Harper singled to right, runner advanced to third."},
            },
            "boxscore": {
                "teams": [
                    {
                        "team": {"abbreviation": "PHI"},
                        "statistics": [
                            {"name": "hits", "displayValue": "8"},
                            {"name": "errors", "displayValue": "0"},
                            {"name": "walks", "displayValue": "3"},
                        ],
                    },
                    {
                        "team": {"abbreviation": "LAD"},
                        "statistics": [
                            {"name": "hits", "displayValue": "7"},
                            {"name": "errors", "displayValue": "1"},
                            {"name": "walks", "displayValue": "2"},
                        ],
                    },
                ]
            },
        }
        state = live_stats.normalize_mlb_summary(summary, espn_event_id="123")
        self.assertEqual(state["away"]["abbr"], "PHI")
        self.assertEqual(state["home"]["abbr"], "LAD")
        self.assertEqual(state["away"]["score"], "4")
        self.assertEqual(state["home"]["stats"]["hits"], "7")
        self.assertEqual(state["status"], "Top 7th")
        self.assertEqual(state["balls"], 2)
        self.assertTrue(state["on_first"])
        self.assertTrue(state["on_third"])
        self.assertEqual(state["batter"], "B. Harper")
        self.assertIn("singled", state["last_play"])

    def test_mlb_statsapi_event_match_requires_both_teams(self):
        row = {"event_title": "Philadelphia Phillies at Los Angeles Dodgers"}
        game = {
            "gamePk": 123,
            "teams": {
                "away": {"team": {"name": "Philadelphia Phillies", "abbreviation": "PHI"}},
                "home": {"team": {"name": "Los Angeles Dodgers", "abbreviation": "LAD"}},
            },
        }
        self.assertGreater(mlb_live_source.event_match_score(row, game), 0)
        game["teams"]["home"]["team"] = {"name": "New York Mets", "abbreviation": "NYM"}
        self.assertEqual(mlb_live_source.event_match_score(row, game), -1)

    def test_mlb_statsapi_normalizes_rich_live_state(self):
        feed = {
            "gamePk": 123456,
            "gameData": {
                "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
                "teams": {
                    "away": {
                        "name": "Philadelphia Phillies",
                        "abbreviation": "PHI",
                        "record": {"wins": 70, "losses": 52},
                    },
                    "home": {
                        "name": "Houston Astros",
                        "abbreviation": "HOU",
                        "record": {"wins": 67, "losses": 55},
                    },
                },
            },
            "liveData": {
                "linescore": {
                    "currentInning": 8,
                    "currentInningOrdinal": "8th",
                    "inningState": "Top",
                    "balls": 3,
                    "strikes": 2,
                    "outs": 2,
                    "offense": {
                        "batter": {"fullName": "Bryce Harper"},
                        "first": {"fullName": "Alec Bohm"},
                        "third": {"fullName": "Trea Turner"},
                    },
                    "defense": {"pitcher": {"fullName": "Josh Hader"}},
                    "teams": {
                        "away": {"runs": 5, "hits": 9, "errors": 0},
                        "home": {"runs": 4, "hits": 8, "errors": 1},
                    },
                    "innings": [
                        {"num": 1, "away": {"runs": 1}, "home": {"runs": 2}},
                        {"num": 2, "away": {"runs": 0}, "home": {"runs": 0}},
                        {"num": 3, "away": {"runs": 2}, "home": {"runs": 0}},
                        {"num": 4, "away": {"runs": 0}, "home": {"runs": 0}},
                        {"num": 5, "away": {"runs": 0}, "home": {"runs": 2}},
                        {"num": 6, "away": {"runs": 0}, "home": {"runs": 0}},
                        {"num": 7, "away": {"runs": 0}, "home": {"runs": 0}},
                        {"num": 8, "away": {"runs": 2}, "home": {"runs": None}},
                    ],
                },
                "boxscore": {
                    "teams": {
                        "away": {"teamStats": {"batting": {"baseOnBalls": 3, "strikeOuts": 7}}},
                        "home": {"teamStats": {"batting": {"baseOnBalls": 2, "strikeOuts": 8}}},
                    }
                },
                "plays": {
                    "currentPlay": {
                        "result": {"description": "Harper fouled off a 3-2 fastball."},
                        "matchup": {
                            "batter": {"fullName": "Bryce Harper"},
                            "pitcher": {"fullName": "Josh Hader"},
                        },
                        "playEvents": [
                            {
                                "isPitch": True,
                                "pitchNumber": 5,
                                "details": {
                                    "description": "Foul",
                                    "type": {"code": "FF", "description": "Four-Seam Fastball"},
                                },
                                "pitchData": {
                                    "startSpeed": 96.2,
                                    "endSpeed": 87.1,
                                    "zone": 5,
                                    "coordinates": {"pX": 0.12, "pZ": 2.55},
                                    "breaks": {"spinRate": 2388},
                                },
                            },
                            {
                                "isPitch": False,
                                "hitData": {
                                    "launchSpeed": 102.4,
                                    "launchAngle": 23.0,
                                    "totalDistance": 388.0,
                                    "trajectory": "fly_ball",
                                },
                            },
                        ],
                    }
                },
            },
        }
        state = mlb_live_source.normalize_live_feed(feed, game_pk="123456")
        self.assertEqual(state["data_source"], "mlb-statsapi")
        self.assertEqual(state["mlb_game_pk"], "123456")
        self.assertEqual(state["away"]["abbr"], "PHI")
        self.assertEqual(state["home"]["abbr"], "HOU")
        self.assertEqual(state["away"]["score"], "5")
        self.assertEqual(state["home"]["stats"]["errors"], "1")
        self.assertEqual(state["status"], "Top 8th")
        self.assertEqual(state["balls"], 3)
        self.assertEqual(state["strikes"], 2)
        self.assertEqual(state["outs"], 2)
        self.assertTrue(state["on_first"])
        self.assertFalse(state["on_second"])
        self.assertTrue(state["on_third"])
        self.assertEqual(state["batter"], "Bryce Harper")
        self.assertEqual(state["pitcher"], "Josh Hader")
        self.assertEqual(state["pitch"]["type"], "Four-Seam Fastball")
        self.assertEqual(state["pitch"]["start_speed"], 96.2)
        self.assertEqual(state["pitch"]["spin_rate"], 2388)
        self.assertEqual(state["batted_ball"]["launch_speed"], 102.4)
        self.assertEqual(state["batted_ball"]["distance"], 388.0)

    def test_render_mlb_frame_returns_png(self):
        payload = live_stats.render_mlb_frame(
            {
                "away": {"abbr": "PHI", "score": "4", "record": "70-52", "stats": {"hits": "8", "errors": "0"}, "innings": ["1", "0", "0", "2", "0", "1"]},
                "home": {"abbr": "LAD", "score": "3", "record": "74-48", "stats": {"hits": "7", "errors": "1"}, "innings": ["0", "1", "0", "0", "2", "0"]},
                "status": "Top 7th",
                "balls": 2,
                "strikes": 1,
                "outs": 1,
                "on_first": True,
                "on_second": False,
                "on_third": True,
                "batter": "B. Harper",
                "pitcher": "S. Ohtani",
                "last_play": "Harper singled to right, runner advanced to third.",
                "source_event_id": "123",
                "data_source_label": "MLB StatsAPI",
                "updated_at": "2026-08-16T22:40:00-04:00",
            }
        )
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_playlist_injection_adds_decimal_companion_after_mlb_channel(self):
        row = {
            "assigned_number": 1000,
            "league_id": "mlb",
            "event_key": "mlb:phi@lad",
            "event_title": "Philadelphia Phillies at Los Angeles Dodgers",
            "display_name": "MLB · Phillies at Dodgers",
            "group_title": "Sports Today",
            "tvg_logo": "",
        }
        playlist = "#EXTM3U\n#EXTINF:-1 tvg-chno=\"1000\",MLB Game\nhttp://picker:9999/sports/stream/1000\n"
        with mock.patch.object(live_stats._s, "generated_rows", return_value=[row]):
            output = live_stats.inject_stats_channels(playlist, Path("unused.db"), "http://picker:9999")
        self.assertIn('tvg-chno="1000.1"', output)
        self.assertIn("Philadelphia Phillies at Los Angeles Dodgers — Live Stats", output)
        self.assertIn("/sports/stats/1000/stream.m3u8", output)
        self.assertLess(output.index("/sports/stream/1000"), output.index("/sports/stats/1000/stream.m3u8"))

    def test_mlb_companion_uses_lowest_feed_for_one_logical_event(self):
        rows = [
            {"assigned_number": 1002, "league_id": "mlb", "event_key": "mlb:phi@was", "event_title": "Phillies at Nationals"},
            {"assigned_number": 1000, "league_id": "mlb", "event_key": "mlb:phi@was", "event_title": "Phillies at Nationals"},
            {"assigned_number": 1001, "league_id": "mlb", "event_key": "mlb:phi@was", "event_title": "Phillies at Nationals"},
            {"assigned_number": 1010, "league_id": "mlb", "event_key": "mlb:nym@atl", "event_title": "Mets at Braves"},
            {"assigned_number": 2000, "league_id": "nfl", "event_key": "nfl:phi@bal", "event_title": "Eagles at Ravens"},
        ]
        companions = mlb_stats_companions.primary_mlb_rows(rows)
        self.assertEqual([row["assigned_number"] for row in companions], [1000, 1010])
        self.assertEqual(mlb_stats_companions.stats_number(companions[0]), "1000.1")

    def test_mlb_companion_xmltv_mirrors_parent_programme_window(self):
        row = {
            "assigned_number": 1000,
            "league_id": "mlb",
            "event_key": "mlb:phi@was:2026-08-17",
            "event_title": "Phillies at Nationals",
            "event_start": "2026-08-17T19:05:00-04:00",
            "event_end": "2026-08-17T23:05:00-04:00",
            "group_title": "Sports Today",
            "epg_programme": {
                "start": "2026-08-17T20:05:00-04:00",
                "stop": "2026-08-17T22:45:00-04:00",
                "categories": ["Baseball"],
            },
        }
        root = ElementTree.Element("tv")
        parent_channel = ElementTree.SubElement(root, "channel", {"id": "parent"})
        ElementTree.SubElement(parent_channel, "display-name").text = "1000"
        ElementTree.SubElement(
            root,
            "programme",
            {
                "start": "20260817200500 -0400",
                "stop": "20260817224500 -0400",
                "channel": "parent",
            },
        )

        mlb_stats_companions.append_xmltv(root, [row], "America/New_York")
        companion_id = mlb_stats_companions.stats_tvg_id(row)
        children = list(root)
        first_programme = next(index for index, child in enumerate(children) if child.tag == "programme")
        companion_channel = next(child for child in children if child.tag == "channel" and child.attrib.get("id") == companion_id)
        self.assertLess(children.index(companion_channel), first_programme)

        programme = next(child for child in children if child.tag == "programme" and child.attrib.get("channel") == companion_id)
        self.assertEqual(programme.attrib["start"], "20260817200500 -0400")
        self.assertEqual(programme.attrib["stop"], "20260817224500 -0400")
        self.assertEqual(programme.findtext("title"), "Phillies at Nationals — Live Stats")

    def test_espn_event_match_requires_both_teams(self):
        row = {"event_title": "Philadelphia Phillies at Los Angeles Dodgers"}
        event = {
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "Philadelphia Phillies", "abbreviation": "PHI"}},
                        {"team": {"displayName": "Los Angeles Dodgers", "abbreviation": "LAD"}},
                    ]
                }
            ]
        }
        self.assertGreater(live_stats._event_match_score(row, event), 0)
        event["competitions"][0]["competitors"][1]["team"] = {"displayName": "New York Mets", "abbreviation": "NYM"}
        self.assertEqual(live_stats._event_match_score(row, event), -1)


if __name__ == "__main__":
    unittest.main()
