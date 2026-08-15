from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from api.epg import register_epg_routes


class MasterUpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        register_epg_routes(self.app)
        self.client = self.app.test_client()
        self.master = {
            "running": True,
            "started_at": "2026-08-15T17:30:00-04:00",
            "trigger": "manual",
            "elapsed_seconds": 0,
            "phase": "starting",
        }

    def test_run_endpoint_returns_202_without_waiting_for_update_result(self):
        with patch(
            "api.epg.master_update_worker.start",
            return_value=(True, dict(self.master)),
        ) as start:
            response = self.client.post("/api/master-update/run")

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["started"])
        self.assertTrue(payload["master_update"]["running"])
        start.assert_called_once_with(trigger="manual")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(response.headers.get("Pragma"), "no-cache")

    def test_run_endpoint_rejects_duplicate_worker(self):
        with patch(
            "api.epg.master_update_worker.start",
            return_value=(False, dict(self.master)),
        ):
            response = self.client.post("/api/master-update/run")

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertFalse(payload["started"])
        self.assertTrue(payload["master_update"]["running"])

    def test_status_endpoint_is_live_and_not_cacheable(self):
        status = dict(self.master)
        status["elapsed_seconds"] = 42
        status["phase"] = "running"
        with patch(
            "api.epg.master_update_worker.payload",
            return_value=status,
        ):
            response = self.client.get("/api/master-update")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["master_update"]["elapsed_seconds"], 42)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertEqual(response.headers.get("Expires"), "0")


if __name__ == "__main__":
    unittest.main()
