import unittest

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


if __name__ == "__main__":
    unittest.main()
