from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import core  # noqa: E402
import sports  # noqa: E402


class SportsCatalogHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "sports.db"
        sports.init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_event_channels_are_not_discovered_as_team_rows(self):
        channels = core.parse_m3u_text(
            """#EXTM3U
#EXTINF:-1 tvg-logo="https://example.test/event.png" group-title="MLB",MLB ALT 17 | Houston Astros vs San Francisco Giants Aug 10 09:45 PM (Giants Feed)
http://provider.test/event.ts
#EXTINF:-1 tvg-logo="https://example.test/arizona.png" group-title="MLB",MLB Arizona Diamondbacks
http://provider.test/arizona.ts
"""
        )

        sports.discover_catalog_from_channels(self.db_path, channels)
        mlb_teams = [
            item
            for item in sports.catalog_payload(self.db_path, scope_type="team")
            if item.get("league_id") == "mlb"
        ]

        names = {item["name"] for item in mlb_teams}
        self.assertNotIn(
            "ALT 17 | Houston Astros vs San Francisco Giants Aug 10 09:45 PM (Giants Feed)",
            names,
        )
        self.assertIn("Arizona Diamondbacks", names)

    def test_existing_provider_event_rows_are_pruned_on_discovery(self):
        with sports.closing(sports._connect(self.db_path)) as conn:
            sports._upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id="mlb:alt-18-kansas-city-royals-vs-los-angeles-dodgers-aug-10-10-10-pm",
                display_name="ALT 18 | Kansas City Royals vs Los Angeles Dodgers Aug 10 10:10 PM",
                subtitle="MLB team • home and away games",
                league_id="mlb",
                aliases=["Royals", "Dodgers"],
                logo_url="https://example.test/event.png",
                metadata={"sport_id": "baseball", "family": "Baseball"},
                source="provider",
            )
            conn.commit()

        sports.discover_catalog_from_channels(self.db_path, [])

        names = {
            item["name"]
            for item in sports.catalog_payload(self.db_path, scope_type="team")
        }
        self.assertNotIn(
            "ALT 18 | Kansas City Royals vs Los Angeles Dodgers Aug 10 10:10 PM",
            names,
        )

    def test_api_sports_team_rows_are_never_pruned_by_provider_cleanup(self):
        with sports.closing(sports._connect(self.db_path)) as conn:
            sports._upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id="mlb:api-test-team",
                display_name="API Test Team",
                subtitle="MLB team • home and away games",
                league_id="mlb",
                aliases=["API Test Team"],
                logo_url="https://example.test/api-team.png",
                metadata={"sport_id": "baseball", "family": "Baseball"},
                source="api-sports",
            )
            conn.commit()

        sports.discover_catalog_from_channels(self.db_path, [])

        names = {
            item["name"]
            for item in sports.catalog_payload(self.db_path, scope_type="team")
        }
        self.assertIn("API Test Team", names)


if __name__ == "__main__":
    unittest.main()
