from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import master_update_reports


class MasterUpdateReportTests(unittest.TestCase):
    def test_successful_wrapped_update_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "test.db"
            core = SimpleNamespace(
                DB_PATH=db_path,
                run_master_update=lambda *, trigger="manual": {
                    "ok": True,
                    "message": "Update complete.",
                    "provider_warnings": [],
                    "cycle_check": {"ok": True},
                    "guide_check": {"ok": True},
                },
                redact_url_credentials=lambda value: value,
            )
            master_update_reports.install(core)
            result = core.run_master_update(trigger="manual")
            report = master_update_reports.latest(db_path)

        self.assertTrue(result["ok"])
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["trigger"], "manual")
        self.assertEqual(report["summary"], "Update complete.")

    def test_failed_wrapped_update_records_attempt_before_reraising(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "test.db"

            def fail(*, trigger="manual"):
                raise RuntimeError("provider refresh failed")

            core = SimpleNamespace(
                DB_PATH=db_path,
                run_master_update=fail,
                redact_url_credentials=lambda value: value,
            )
            master_update_reports.install(core)
            with self.assertRaises(RuntimeError):
                core.run_master_update(trigger="scheduled")
            report = master_update_reports.latest(db_path)

        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["trigger"], "scheduled")
        self.assertIn("provider refresh failed", report["summary"])
        self.assertGreaterEqual(report["duration_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
