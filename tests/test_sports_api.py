from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

import core  # noqa: E402
import sports  # noqa: E402

try:
    from app import app  # noqa: E402
except ModuleNotFoundError as exc:  # The lightweight source-test environment may omit Flask.
    if exc.name != "flask":
        raise
    app = None


@unittest.skipIf(app is None, "Flask is installed inside the Docker image")
class SportsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db_path = core.DB_PATH
        self.original_channels = core.channels
        self.original_source_mode = core.source_mode
        self.original_source_url = core.last_source_url
        core.DB_PATH = Path(self.temp.name) / "api.db"
        core.channels = []
        core.source_mode = ""
        core.last_source_url = ""
        sports.init_db(core.DB_PATH)
        self.client = app.test_client()

    def tearDown(self):
        core.DB_PATH = self.original_db_path
        core.channels = self.original_channels
        core.source_mode = self.original_source_mode
        core.last_source_url = self.original_source_url
        self.temp.cleanup()

    def test_fresh_api_has_no_sports_rules(self):
        response = self.client.get("/api/sports/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["rules"], [])

    def test_refresh_time_validation_is_friendly_and_non_destructive(self):
        good = self.client.patch("/api/sports/settings", json={"refresh_time": "04:15"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.get_json()["settings"]["refresh_time"], "04:15")

        bad = self.client.patch("/api/sports/settings", json={"refresh_time": "29:88"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("valid time", bad.get_json()["error"])
        self.assertEqual(sports.get_settings(core.DB_PATH)["refresh_time"], "04:15")

    def test_batch_add_then_remove_all_stays_empty(self):
        added = self.client.post(
            "/api/sports/rules",
            json={
                "items": [
                    {"scope_type": "league", "scope_id": "nfl"},
                    {"scope_type": "sport", "scope_id": "cornhole"},
                ]
            },
        )
        self.assertEqual(added.status_code, 200)
        rules = added.get_json()["rules"]
        self.assertEqual(len(rules), 2)
        for rule in rules:
            deleted = self.client.delete(f"/api/sports/rules/{rule['id']}")
            self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/sports/settings").get_json()["rules"], [])

    def test_update_now_requires_master_switch_but_not_auto_update(self):
        disabled = self.client.post("/api/sports/scan")
        self.assertEqual(disabled.status_code, 409)
        self.assertIn("Turn on Sports Automation", disabled.get_json()["error"])

        self.client.patch(
            "/api/sports/settings",
            json={"enabled": True, "auto_update": False},
        )
        no_source = self.client.post("/api/sports/scan")
        self.assertEqual(no_source.status_code, 409)
        self.assertIn("Load an M3U source", no_source.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
