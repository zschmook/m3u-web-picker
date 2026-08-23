from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_config
import network_config


class NetworkConfigTests(unittest.TestCase):
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
