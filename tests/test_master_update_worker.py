from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import master_update_worker


class MasterUpdateWorkerTests(unittest.TestCase):
    def tearDown(self):
        # Never leak a background worker into another test.
        master_update_worker.wait_for_idle(timeout=2.0)

    def test_start_returns_while_worker_is_running_and_rejects_duplicate(self):
        entered = threading.Event()
        release = threading.Event()

        def fake_run_master_update(*, trigger="manual"):
            entered.set()
            release.wait(timeout=2.0)
            return {"ok": True, "trigger": trigger}

        idle_payload = {
            "running": False,
            "started_at": None,
            "trigger": None,
            "elapsed_seconds": None,
        }

        with patch.object(
            master_update_worker.core,
            "master_update_payload",
            return_value=idle_payload,
        ), patch.object(
            master_update_worker.core,
            "run_master_update",
            side_effect=fake_run_master_update,
        ):
            started, payload = master_update_worker.start(trigger="manual")
            self.assertTrue(started)
            self.assertTrue(entered.wait(timeout=1.0))
            self.assertTrue(payload["running"])
            self.assertEqual(payload["trigger"], "manual")
            self.assertEqual(payload["phase"], "starting")

            duplicate_started, duplicate_payload = master_update_worker.start(trigger="manual")
            self.assertFalse(duplicate_started)
            self.assertTrue(duplicate_payload["running"])

            release.set()
            self.assertTrue(master_update_worker.wait_for_idle(timeout=2.0))
            self.assertFalse(master_update_worker.payload()["running"])

    def test_existing_core_update_prevents_new_worker(self):
        with patch.object(
            master_update_worker.core,
            "master_update_payload",
            return_value={
                "running": True,
                "started_at": "2026-08-15T17:00:00-04:00",
                "trigger": "scheduled",
                "elapsed_seconds": 12,
            },
        ):
            started, payload = master_update_worker.start(trigger="manual")

        self.assertFalse(started)
        self.assertTrue(payload["running"])
        self.assertEqual(payload["trigger"], "scheduled")
        self.assertEqual(payload["phase"], "running")

    def test_onboarding_guide_ready_requires_public_epg_cache_and_combined_xmltv(self):
        with TemporaryDirectory() as temp_dir:
            combined = Path(temp_dir) / "epg.xml"
            combined.write_text("<tv></tv>\n", encoding="utf-8")
            public_payload = {
                "countries": [
                    {
                        "code": "US",
                        "enabled": True,
                        "cached": True,
                        "filtered_bytes": 1234,
                    }
                ]
            }
            with patch.object(
                master_update_worker.core,
                "public_epg_payload",
                return_value=public_payload,
            ), patch.object(
                master_update_worker.core,
                "COMBINED_EPG_PATH",
                combined,
            ):
                ready, error = master_update_worker._onboarding_guide_ready()

            self.assertTrue(ready)
            self.assertEqual(error, "")

    def test_onboarding_guide_ready_rejects_uncached_enabled_public_epg(self):
        with TemporaryDirectory() as temp_dir:
            combined = Path(temp_dir) / "epg.xml"
            combined.write_text("<tv></tv>\n", encoding="utf-8")
            public_payload = {
                "countries": [
                    {
                        "code": "US",
                        "enabled": True,
                        "cached": False,
                        "filtered_bytes": 0,
                    }
                ]
            }
            with patch.object(
                master_update_worker.core,
                "public_epg_payload",
                return_value=public_payload,
            ), patch.object(
                master_update_worker.core,
                "COMBINED_EPG_PATH",
                combined,
            ):
                ready, error = master_update_worker._onboarding_guide_ready()

            self.assertFalse(ready)
            self.assertIn("US", error)


if __name__ == "__main__":
    unittest.main()
