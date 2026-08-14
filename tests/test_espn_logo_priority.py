from __future__ import annotations

import ast
import unittest
from pathlib import Path

import espn_team_logos


ROOT = Path(__file__).resolve().parents[1]


class EspnLogoPriorityTests(unittest.TestCase):
    def test_espn_index_uses_only_full_default_variant(self):
        payload = {
            "sports": [
                {
                    "leagues": [
                        {
                            "teams": [
                                {
                                    "team": {
                                        "displayName": "Philadelphia Eagles",
                                        "shortDisplayName": "Eagles",
                                        "abbreviation": "PHI",
                                        "slug": "philadelphia-eagles",
                                        "location": "Philadelphia",
                                        "name": "Eagles",
                                        "logos": [
                                            {
                                                "href": "https://example.test/scoreboard.png",
                                                "rel": ["full", "default", "scoreboard"],
                                            },
                                            {
                                                "href": "https://example.test/dark.png",
                                                "rel": ["full", "default", "dark"],
                                            },
                                            {
                                                "href": "https://example.test/default.png",
                                                "rel": ["full", "default"],
                                            },
                                        ],
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        index = espn_team_logos._build_index(payload)
        self.assertEqual(
            index[espn_team_logos._normalize("Philadelphia Eagles")],
            "https://example.test/default.png",
        )

    def test_known_leagues_still_use_direct_espn_mapping(self):
        self.assertEqual(espn_team_logos.ESPN_LEAGUES["nfl"], ("football", "nfl"))
        self.assertEqual(
            espn_team_logos.ESPN_LEAGUES["ncaaf-fbs"],
            ("football", "college-football"),
        )

    def test_unlisted_generated_sport_uses_dynamic_espn_discovery(self):
        original_dynamic = espn_team_logos._dynamic_candidates
        original_index = espn_team_logos._team_index
        try:
            espn_team_logos._dynamic_candidates = lambda league_id, sport_id: [
                ("lacrosse", "pll")
            ]
            espn_team_logos._team_index = lambda sport, league: {
                espn_team_logos._normalize("Maryland Whipsnakes"): "https://example.test/pll.png"
            }
            self.assertEqual(
                espn_team_logos.espn_full_default_url(
                    "pll",
                    "Maryland Whipsnakes",
                    "lacrosse",
                ),
                "https://example.test/pll.png",
            )
        finally:
            espn_team_logos._dynamic_candidates = original_dynamic
            espn_team_logos._team_index = original_index

    def test_dynamic_discovery_is_conservative(self):
        original_sports = espn_team_logos._available_espn_sports
        original_leagues = espn_team_logos._league_slugs_for_sport
        try:
            espn_team_logos._available_espn_sports = lambda: {"lacrosse"}
            espn_team_logos._league_slugs_for_sport = lambda sport: {
                "college-lacrosse",
                "pll",
                "nll",
            }
            candidates = espn_team_logos._dynamic_candidates(
                "college-lacrosse",
                "lacrosse",
            )
            self.assertEqual(candidates[0], ("lacrosse", "college-lacrosse"))
        finally:
            espn_team_logos._available_espn_sports = original_sports
            espn_team_logos._league_slugs_for_sport = original_leagues

    def test_generated_feed_prefers_espn_and_keeps_provider_fallback(self):
        source = (ROOT / "sports" / "feeds.py").read_text(encoding="utf-8")
        self.assertIn("espn_team_logos.espn_full_default_url", source)
        self.assertIn('event.get("sport_id") or ""', source)
        self.assertIn("away_fallback_logo_url=away_catalog_logo or away_api_logo", source)
        self.assertIn("home_fallback_logo_url=home_catalog_logo or home_api_logo", source)
        self.assertLess(
            source.index("away_espn_logo"),
            source.index("away_catalog_logo,\n            away_api_logo"),
        )

    def test_event_compositor_is_cache_first_then_fallback_capable(self):
        source = (ROOT / "event_logos.py").read_text(encoding="utf-8")
        ast.parse(source, filename="event_logos.py")
        self.assertIn('"fallback_url"', source)
        self.assertIn("_cached_payload_for_digest(registered_digest)", source)
        self.assertIn('add(team.get("source_url"), "event-logo:preferred")', source)
        self.assertIn('add(team.get("fallback_url"), "event-logo:provider-fallback")', source)

    def test_img_cache_status_counts_payloads_and_event_composites(self):
        route_source = (ROOT / "api" / "logo_cache_status.py").read_text(encoding="utf-8")
        ui_source = (ROOT / "static" / "js" / "ui_img_cache_status.js").read_text(encoding="utf-8")
        self.assertIn('root.glob("*.bin")', route_source)
        self.assertIn('event_dir.glob("*.png")', route_source)
        self.assertIn("IMG Cache", ui_source)
        self.assertIn("/api/logo-cache/status", ui_source)


if __name__ == "__main__":
    unittest.main()
