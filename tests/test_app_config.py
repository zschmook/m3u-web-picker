from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app_config


class AppConfigTests(unittest.TestCase):
    def test_section_updates_preserve_the_global_document(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            app_config.update({"provider_sources": [{"id": "primary"}]}, path=path)
            section = app_config.update_section("media_pipeline", {"enabled": False}, path=path)
            payload = app_config.load(path)

        self.assertEqual(section, {"enabled": False})
        self.assertEqual(payload["provider_sources"], [{"id": "primary"}])
        self.assertEqual(payload["media_pipeline"], {"enabled": False})

    def test_invalid_json_recovers_as_an_empty_document(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(app_config.load(path), {})


if __name__ == "__main__":
    unittest.main()
