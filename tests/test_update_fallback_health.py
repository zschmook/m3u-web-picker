import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import core
from api import ui_status


class UpdateHealthTests(unittest.TestCase):
    def health(self, output_exists=True, fallbacks=None):
        primary = {"role": "primary", "last_error": "HTTP 522"}
        report = {"status": "failed", "started_at": "2026-09-23T18:00:00-04:00", "finished_at": "2026-09-23T18:02:00-04:00"}
        with ExitStack() as stack:
            values = {
                "core.provider_sources_payload": [primary, *(fallbacks or [])],
                "core.epg_sources_payload": [], "core.public_epg_payload": {},
                "core.manual_fallback_usage": {"checked": 5, "used": 2},
                "core.enrich_sports_status": {"settings": {"enabled": True}, "last_scan": {"status": "failed"}},
                "sports.status_payload": {}, "master_update_reports.latest": report,
                "master_update_worker.payload": {"running": False},
            }
            for name, value in values.items():
                stack.enter_context(patch(f"api.ui_status.{name}", return_value=value))
            stack.enter_context(patch.object(core, "PLAYLIST_PATH", Mock(exists=lambda: output_exists)))
            stack.enter_context(patch.object(core, "COMBINED_EPG_PATH", Mock(exists=lambda: output_exists)))
            return ui_status._update_health()

    def test_retained_outputs_make_upstream_failures_yellow(self):
        result = self.health()
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["warning_count"], 2)

    def test_missing_outputs_stay_red(self):
        self.assertEqual(self.health(False)["status"], "failed")

    def test_checked_and_used_are_distinct(self):
        result = self.health(fallbacks=[
            {"role": "fallback", "name": "Checked", "last_refresh_attempt": "2026-09-23T18:01:00-04:00"},
            {"role": "fallback", "name": "Waiting", "deferred": True},
        ])
        self.assertEqual(result["fallbacks"]["checked"], 1)
        self.assertEqual(result["fallbacks"]["refreshed"], 1)
        self.assertEqual(result["fallbacks"]["manual_channels_using_fallback"], 2)
        self.assertTrue(any(stage["name"] == "Fallbacks checked / used" for stage in result["stages"]))

    def test_primary_failure_still_refreshes_every_fallback(self):
        primary = {"role": "primary", "id": "p"}
        backups = [{"role": "fallback", "id": "b1"}, {"role": "fallback", "id": "b2"}]
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(core, channels=[{"id": 1}], source_mode="url", provider_sources=[primary, *backups]))
            stack.enter_context(patch("core.sports.get_settings", return_value={"enabled": True}))
            stack.enter_context(patch("core.refresh_master_from_url", return_value=(False, "offline")))
            refresh = stack.enter_context(patch("core.refresh_provider_source", return_value=(True, "ok", [])))
            for name in ["begin_scan_state", "update_scan_stage", "record_scan_failure", "finish_scan_state", "refresh_schedule_api_if_due"]:
                stack.enter_context(patch(f"core.sports.{name}", return_value={}))
            stack.enter_context(patch("core.sports_provider_channel_sets", return_value=[]))
            with self.assertRaises(core.SportsScanError):
                core.run_sports_scan()
            self.assertEqual([call.args[0] for call in refresh.call_args_list], backups)


if __name__ == "__main__":
    unittest.main()
