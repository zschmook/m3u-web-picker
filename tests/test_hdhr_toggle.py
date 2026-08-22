from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import hdhr_config
from tools import hdhr_discovery_host


class HdHomeRunSupportToggleTests(unittest.TestCase):
    def test_missing_toggle_state_preserves_existing_enabled_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            with patch.object(hdhr_config, "HDHR_CONFIG_PATH", path):
                self.assertTrue(hdhr_config.is_enabled())

    def test_toggle_state_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            with patch.object(hdhr_config, "HDHR_CONFIG_PATH", path):
                self.assertFalse(hdhr_config.set_enabled(False))
                self.assertFalse(hdhr_config.is_enabled())
                self.assertTrue(hdhr_config.set_enabled(True))
                self.assertTrue(hdhr_config.is_enabled())

    def test_toggle_preserves_other_global_config_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text('{"master_update": {"enabled": true}}', encoding="utf-8")
            with patch.object(hdhr_config, "HDHR_CONFIG_PATH", path):
                hdhr_config.set_enabled(False)

            payload = hdhr_config.app_config.load(path)
            self.assertEqual(payload["master_update"], {"enabled": True})
            self.assertEqual(payload["hdhr"], {"enabled": False})

    def test_legacy_toggle_file_migrates_into_global_config(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            legacy_path = Path(temp) / "hdhr.json"
            legacy_path.write_text('{"enabled": false}', encoding="utf-8")
            with patch.object(hdhr_config, "HDHR_CONFIG_PATH", config_path), patch.object(
                hdhr_config, "LEGACY_HDHR_CONFIG_PATH", legacy_path
            ), patch.object(hdhr_config.app_config, "CONFIG_PATH", config_path):
                self.assertFalse(hdhr_config.is_enabled())

            self.assertEqual(hdhr_config.app_config.section("hdhr", path=config_path), {"enabled": False})

    def test_host_discovery_helper_reads_web_toggle(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{"enabled": false}'
        with patch(
            "tools.hdhr_discovery_host.urllib.request.urlopen",
            return_value=response,
        ) as opened:
            enabled = hdhr_discovery_host._remote_support_enabled(
                "http://10.0.0.22:10000"
            )
        self.assertFalse(enabled)
        request = opened.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://10.0.0.22:10000/api/hdhr/status",
        )

    def test_host_discovery_helper_keeps_last_state_on_web_failure(self):
        with patch(
            "tools.hdhr_discovery_host.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            self.assertIsNone(
                hdhr_discovery_host._remote_support_enabled(
                    "http://10.0.0.22:10000"
                )
            )


if __name__ == "__main__":
    unittest.main()
