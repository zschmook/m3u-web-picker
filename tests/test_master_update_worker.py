from __future__ import annotations

import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()
