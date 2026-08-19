from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import espn_known_logos  # noqa: E402
from sports import feeds  # noqa: E402


class EspnKnownLogoFallbackTests(unittest.TestCase):
    def test_known_mlb_full_default_paths_cover_reported_misses(self):
        self.assertEqual(
            espn_known_logos.direct_full_default_url("mlb", "Philadelphia Phillies"),
            "https://a.espncdn.com/i/teamlogos/mlb/500/phi.png",
        )
        self.assertEqual(
            espn_known_logos.direct_full_default_url("mlb", "Washington Nationals"),
            "https://a.espncdn.com/i/teamlogos/mlb/500/wsh.png",
        )
        self.assertEqual(
            espn_known_logos.direct_full_default_url("mlb", "San Diego Padres"),
            "https://a.espncdn.com/i/teamlogos/mlb/500/sd.png",
        )

    def test_known_mlb_path_is_primary_and_avoids_catalog_failure(self):
        event = {"league_id": "mlb", "sport_id": "baseball"}
        with patch(
            "sports.feeds.espn_team_logos.espn_full_default_url",
            side_effect=AssertionError("MLB should not need the catalog"),
        ) as catalog, patch(
            "sports.feeds.espn_known_logos.direct_full_default_url",
            return_value="https://a.espncdn.com/i/teamlogos/mlb/500/sd.png",
        ) as direct:
            result = feeds._espn_team_logo(event, team_name="San Diego Padres")

        self.assertEqual(
            result,
            "https://a.espncdn.com/i/teamlogos/mlb/500/sd.png",
        )
        direct.assert_called_once_with("mlb", "San Diego Padres")
        catalog.assert_not_called()

    def test_non_mlb_does_not_invent_a_direct_path(self):
        self.assertEqual(
            espn_known_logos.direct_full_default_url("ncaaf-fbs", "Penn State"),
            "",
        )


if __name__ == "__main__":
    unittest.main()
