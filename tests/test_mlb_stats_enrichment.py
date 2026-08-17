import unittest

from sports import mlb_stats_enrichment


class MlbStatsEnrichmentTests(unittest.TestCase):
    def test_parse_standings_reads_games_back(self):
        payload = {
            "records": [
                {
                    "teamRecords": [
                        {
                            "team": {"name": "Tampa Bay Rays", "abbreviation": "TB"},
                            "gamesBack": "-",
                        },
                        {
                            "team": {"name": "Baltimore Orioles", "abbreviation": "BAL"},
                            "gamesBack": "12.5",
                        },
                    ]
                }
            ]
        }
        by_abbr, by_name = mlb_stats_enrichment._parse_standings(payload)
        self.assertEqual(by_abbr["TB"], "—")
        self.assertEqual(by_abbr["BAL"], "12.5")
        self.assertEqual(by_name["tampa bay rays"], "—")

    def test_batting_team_handles_live_and_between_innings(self):
        state = {
            "away": {"abbr": "BAL"},
            "home": {"abbr": "TB"},
            "status": "Top 1st",
        }
        self.assertEqual(mlb_stats_enrichment._batting_abbr(state), "BAL")

        state["status"] = "Middle 1st"
        self.assertEqual(mlb_stats_enrichment._batting_abbr(state), "TB")

        state["status"] = "Bottom 1st"
        self.assertEqual(mlb_stats_enrichment._batting_abbr(state), "TB")

        state["status"] = "End 1st"
        self.assertEqual(mlb_stats_enrichment._batting_abbr(state), "BAL")

    def test_enrich_state_exposes_batting_team(self):
        state = {
            "away": {"abbr": "BAL", "name": "Baltimore Orioles"},
            "home": {"abbr": "TB", "name": "Tampa Bay Rays"},
            "status": "Middle 1st",
        }
        # Avoid a network lookup; this test only exercises the batting-team
        # normalization contract.
        original_date = mlb_stats_enrichment._CACHE_DATE
        original_abbr = dict(mlb_stats_enrichment._CACHE_BY_ABBR)
        original_name = dict(mlb_stats_enrichment._CACHE_BY_NAME)
        try:
            mlb_stats_enrichment._CACHE_DATE = mlb_stats_enrichment._today()
            mlb_stats_enrichment._CACHE_BY_ABBR = {}
            mlb_stats_enrichment._CACHE_BY_NAME = {}
            enriched = mlb_stats_enrichment.enrich_state(state)
        finally:
            mlb_stats_enrichment._CACHE_DATE = original_date
            mlb_stats_enrichment._CACHE_BY_ABBR = original_abbr
            mlb_stats_enrichment._CACHE_BY_NAME = original_name
        self.assertEqual(enriched["batting_team"], "TB")


if __name__ == "__main__":
    unittest.main()
