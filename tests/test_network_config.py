from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import app_config
import network_config


class NetworkConfigTests(unittest.TestCase):
    def test_media_origin_uses_configured_lan_address_for_local_requests(self):
        with patch.object(network_config, "load_settings", return_value=SimpleNamespace(lan_host="10.0.0.18", external_port=9999)):
            self.assertEqual(network_config.media_origin("http://localhost:9998/"), "http://10.0.0.18:9999")

    def test_media_origin_falls_back_to_request_when_no_lan_host_is_configured(self):
        with patch.object(network_config, "load_settings", return_value=SimpleNamespace(lan_host="", external_port=9999)):
            self.assertEqual(network_config.media_origin("https://tv.example.test/"), "https://tv.example.test")

    def test_public_port_persists_and_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            with patch.object(app_config, "CONFIG_PATH", config_path), patch.dict(
                "os.environ",
                {"M3U_DATA_DIR": temp, "M3U_EXTERNAL_PORT": "9999"},
                clear=False,
            ):
                saved = network_config.save({"external_port": 9997})
            self.assertEqual(saved["external_port"], 9997)
            self.assertEqual(app_config.section("network", path=config_path), {"external_port": 9997})

    def test_public_port_validation_rejects_invalid_values(self):
        for value in (None, "", 0, 65536, "not-a-port"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                network_config.save({"external_port": value})


if __name__ == "__main__":
    unittest.main()
