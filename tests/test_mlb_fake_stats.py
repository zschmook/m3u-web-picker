import unittest
from datetime import datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from api import stats_guide_demo
from sports import mlb_fake_stats


class FakeMlbStatsTests(unittest.TestCase):
    def test_fake_channel_is_inserted_after_1_1(self):
        playlist = (
            '#EXTM3U\n'
            '#EXTINF:-1 tvg-chno="1.1",NFL Demo\n'
            'http://picker:9999/sports/stats-demo/1/stream.m3u8\n'
            '#EXTINF:-1 tvg-chno="2",Other\n'
            'http://example/2\n'
        )
        output = mlb_fake_stats.inject_demo_channel(playlist, "http://picker:9999")
        self.assertIn('tvg-chno="1.2"', output)
        self.assertIn('/sports/stats-fake/stream.m3u8', output)
        self.assertLess(output.index('tvg-chno="1.1"'), output.index('tvg-chno="1.2"'))
        self.assertLess(output.index('tvg-chno="1.2"'), output.index('tvg-chno="2"'))

    def test_fake_game_states_change_score_and_situation(self):
        first = mlb_fake_stats._state(0)
        late = mlb_fake_stats._state(6)
        self.assertEqual(first["away"]["score"], "0")
        self.assertEqual(late["away"]["score"], "5")
        self.assertEqual(late["status"], "Top 8th")
        self.assertTrue(late["on_first"])
        self.assertTrue(late["on_third"])
        self.assertEqual(late["balls"], 3)
        self.assertEqual(late["strikes"], 2)

    def test_guide_item_is_channel_1_2(self):
        item = mlb_fake_stats.guide_item()
        self.assertEqual(item["number"], "1.2")
        self.assertEqual(item["play_url"], "/guide/play/stats-fake/1.2")
        self.assertTrue(item["stats_fake"])

    def test_fake_channel_has_long_running_simulated_live_stats_programme(self):
        root = ElementTree.Element("tv")
        anchor = datetime(
            2026,
            8,
            17,
            18,
            5,
            tzinfo=ZoneInfo("America/New_York"),
        )
        stats_guide_demo._append_fake_mlb_xmltv(
            root,
            "America/New_York",
            generated_at=anchor,
        )

        channel = next(
            child
            for child in root
            if child.tag == "channel" and child.attrib.get("id") == mlb_fake_stats.TVG_ID
        )
        self.assertEqual(channel.findtext("display-name"), mlb_fake_stats.DISPLAY_NAME)

        programme = next(
            child
            for child in root
            if child.tag == "programme" and child.attrib.get("channel") == mlb_fake_stats.TVG_ID
        )
        self.assertEqual(
            programme.findtext("title"),
            f"{mlb_fake_stats.DISPLAY_NAME} — Live Stats",
        )
        self.assertEqual(programme.findtext("sub-title"), "Simulated Live Stats")
        self.assertIn("Simulation", [item.text for item in programme.findall("category")])
        self.assertLess(programme.attrib["start"], programme.attrib["stop"])


if __name__ == "__main__":
    unittest.main()
