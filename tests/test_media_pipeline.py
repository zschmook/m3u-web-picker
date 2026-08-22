from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import media_pipeline


class MediaPipelineTests(unittest.TestCase):
    def setUp(self):
        media_pipeline._last_test = {}
        media_pipeline._sessions.clear()

    def test_defaults_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ):
            self.assertFalse(media_pipeline.settings()["enabled"])

    def test_enable_requires_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "Acknowledge"):
            media_pipeline.save({"enabled": True, "warning_acknowledged": False})

    def test_cpu_fallback_can_be_enabled_after_real_test_and_acknowledgement(self):
        result = {"ok": True, "hardware_available": False, "active_encoder": "libx264", "mode": "cpu"}
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            saved = media_pipeline.save({"enabled": True, "warning_acknowledged": True})
            self.assertTrue(saved["enabled"])

    def test_session_limit_is_enforced(self):
        with patch.object(media_pipeline, "settings", return_value={**media_pipeline.DEFAULTS, "max_sessions": 1}):
            token = media_pipeline.acquire_session("browser")
            with self.assertRaisesRegex(RuntimeError, "stream limit"):
                media_pipeline.acquire_session("mpegts")
            media_pipeline.release_session(token)


if __name__ == "__main__":
    unittest.main()
