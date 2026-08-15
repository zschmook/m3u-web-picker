from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import onboarding


class DevOnboardingTests(unittest.TestCase):
    def test_fresh_dev_database_requires_onboarding_and_progress_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_DEV_ONBOARDING": "true"},
            clear=False,
        ):
            db_path = Path(temp_dir) / "picker.db"

            initial = onboarding.get_state(db_path, provider_configured=False)
            self.assertTrue(initial["required"])
            self.assertFalse(initial["completed"])
            self.assertEqual(initial["current_step"], 1)

            updated = onboarding.update_state(
                db_path,
                provider_configured=True,
                current_step=3,
                answers={"provider_configured": True, "sports_enabled": True},
            )
            self.assertTrue(updated["required"])
            self.assertEqual(updated["current_step"], 3)
            self.assertTrue(updated["answers"]["provider_configured"])

            resumed = onboarding.get_state(db_path, provider_configured=True)
            self.assertTrue(resumed["required"])
            self.assertEqual(resumed["current_step"], 3)

            completed = onboarding.mark_complete(
                db_path,
                provider_configured=True,
            )
            self.assertTrue(completed["completed"])
            self.assertFalse(
                onboarding.setup_required(
                    db_path,
                    provider_configured=True,
                )
            )

    def test_existing_provider_does_not_start_first_run_wizard(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_DEV_ONBOARDING": "true"},
            clear=False,
        ):
            db_path = Path(temp_dir) / "picker.db"
            state = onboarding.get_state(db_path, provider_configured=True)
            self.assertFalse(state["required"])

    def test_non_dev_runtime_never_requires_dev_onboarding(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_DEV_ONBOARDING": "false"},
            clear=False,
        ):
            db_path = Path(temp_dir) / "picker.db"
            state = onboarding.get_state(db_path, provider_configured=False)
            self.assertFalse(state["required"])
            self.assertFalse(
                onboarding.setup_required(
                    db_path,
                    provider_configured=False,
                )
            )


if __name__ == "__main__":
    unittest.main()
