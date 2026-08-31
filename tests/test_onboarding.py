from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import onboarding


class OnboardingTests(unittest.TestCase):
    def test_finish_refresh_closes_write_connection_before_reloading_state(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_ONBOARDING_ENABLED": "true"},
            clear=True,
        ):
            db_path = Path(temp_dir) / "picker.db"
            onboarding.get_state(db_path, provider_configured=True)
            real_connect = onboarding._connect
            active_connections = 0
            peak_connections = 0

            class TrackedConnection:
                def __init__(self, connection):
                    nonlocal active_connections, peak_connections
                    self.connection = connection
                    active_connections += 1
                    peak_connections = max(peak_connections, active_connections)

                def __getattr__(self, name):
                    return getattr(self.connection, name)

                def close(self):
                    nonlocal active_connections
                    self.connection.close()
                    active_connections -= 1

            with patch.object(onboarding, "_connect", side_effect=lambda path: TrackedConnection(real_connect(path))):
                onboarding.finish_initial_refresh(
                    db_path,
                    provider_configured=True,
                    success=False,
                )

            self.assertEqual(active_connections, 0)
            self.assertEqual(peak_connections, 1)

    def test_fresh_database_requires_onboarding_and_progress_persists(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_ONBOARDING_ENABLED": "true"},
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
            self.assertTrue(completed["answers"]["initial_refresh_required"])
            self.assertTrue(
                onboarding.setup_required(
                    db_path,
                    provider_configured=True,
                )
            )

            self.assertTrue(onboarding.claim_initial_refresh(db_path, provider_configured=True))
            onboarding.finish_initial_refresh(
                db_path,
                provider_configured=True,
                success=True,
            )
            self.assertFalse(
                onboarding.setup_required(
                    db_path,
                    provider_configured=True,
                )
            )

    def test_existing_provider_does_not_start_first_run_wizard(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_ONBOARDING_ENABLED": "true"},
            clear=False,
        ):
            db_path = Path(temp_dir) / "picker.db"
            state = onboarding.get_state(db_path, provider_configured=True)
            self.assertFalse(state["required"])

    def test_disabled_runtime_never_requires_onboarding(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_ONBOARDING_ENABLED": "false"},
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

    def test_removed_dev_flag_does_not_enable_onboarding(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"M3U_DEV_ONBOARDING": "true"},
            clear=True,
        ):
            db_path = Path(temp_dir) / "picker.db"
            self.assertFalse(onboarding.onboarding_enabled())
            self.assertFalse(onboarding.get_state(db_path, provider_configured=False)["required"])


if __name__ == "__main__":
    unittest.main()
