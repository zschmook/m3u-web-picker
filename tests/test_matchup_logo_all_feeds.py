from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import sports  # noqa: E402


class MatchupLogoAllFeedsTests(unittest.TestCase):
    def test_every_feed_type_uses_same_matchup_composite(self):
        event = {
            "event_key": "mlb:kansas-city-royals@los-angeles-angels:2026-08-15",
            "league_id": "mlb",
            "sport_id": "baseball",
            "away_team_id": "mlb:kansas-city-royals",
            "away_team_name": "Kansas City Royals",
            "home_team_id": "mlb:los-angeles-angels",
            "home_team_name": "Los Angeles Angels",
            "api_away_logo": "https://provider.test/royals.png",
            "api_home_logo": "https://provider.test/angels.png",
        }
        channel = {"tvg_logo": "https://provider.test/network.png"}
        expected = f"/api/event-logo/{'a' * 64}.png"

        def espn_logo(_event, *, team_name):
            if team_name == "Kansas City Royals":
                return "https://a.espncdn.com/i/teamlogos/mlb/500/kc.png"
            if team_name == "Los Angeles Angels":
                return "https://a.espncdn.com/i/teamlogos/mlb/500/laa.png"
            return ""

        feeds = [
            {"feed_type": "away", "team_id": event["away_team_id"]},
            {"feed_type": "home", "team_id": event["home_team_id"]},
            {"feed_type": "event", "team_id": ""},
            {"feed_type": "national", "team_id": ""},
            {"feed_type": "spanish", "team_id": ""},
            {"feed_type": "backup", "team_id": ""},
        ]

        with patch("sports.feeds._espn_team_logo", side_effect=espn_logo), patch(
            "sports.feeds.event_logos.register_matchup_logo", return_value=expected
        ) as register_matchup:
            logos = [
                sports._preferred_feed_logo(event, feed, channel, {})
                for feed in feeds
            ]

        self.assertEqual(logos, [expected] * len(feeds))
        self.assertEqual(register_matchup.call_count, len(feeds))
        for call in register_matchup.call_args_list:
            self.assertEqual(call.kwargs["away_team_name"], "Kansas City Royals")
            self.assertEqual(call.kwargs["home_team_name"], "Los Angeles Angels")
            self.assertIn("espncdn.com", call.kwargs["away_logo_url"])
            self.assertIn("espncdn.com", call.kwargs["home_logo_url"])


if __name__ == "__main__":
    unittest.main()
