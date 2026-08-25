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
            self.assertFalse(media_pipeline.settings()["commercial_detection_enabled"])

    def test_commercial_detection_requires_encoding(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ):
            with self.assertRaisesRegex(ValueError, "Enable FFmpeg"):
                media_pipeline.save({"commercial_detection_enabled": True})

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

    def test_commercial_filtering_rejects_silent_auto_cpu_fallback(self):
        result = {
            "ok": True,
            "hardware_available": False,
            "active_encoder": "libx264",
            "mode": "cpu",
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            with self.assertRaisesRegex(ValueError, "Select CPU"):
                media_pipeline.save({
                    "enabled": True,
                    "warning_acknowledged": True,
                    "encoder": "auto",
                    "commercial_detection_enabled": True,
                })

    def test_commercial_filtering_allows_explicit_cpu_opt_in(self):
        result = {
            "ok": True,
            "hardware_available": False,
            "active_encoder": "libx264",
            "mode": "cpu",
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            saved = media_pipeline.save({
                "enabled": True,
                "warning_acknowledged": True,
                "encoder": "libx264",
                "commercial_detection_enabled": True,
            })
        self.assertTrue(saved["commercial_detection_enabled"])

    def test_commercial_filtering_allows_tested_hardware(self):
        result = {
            "ok": True,
            "hardware_available": True,
            "active_encoder": "h264_nvenc",
            "mode": "hardware",
        }
        with tempfile.TemporaryDirectory() as temp, patch.object(
            media_pipeline.app_config, "CONFIG_PATH", Path(temp) / "config.json"
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            saved = media_pipeline.save({
                "enabled": True,
                "warning_acknowledged": True,
                "encoder": "auto",
                "commercial_detection_enabled": True,
            })
        self.assertTrue(saved["commercial_detection_enabled"])

    def test_session_limit_is_enforced(self):
        with patch.object(media_pipeline, "settings", return_value={**media_pipeline.DEFAULTS, "max_sessions": 1}):
            token = media_pipeline.acquire_session("browser")
            with self.assertRaisesRegex(RuntimeError, "stream limit"):
                media_pipeline.acquire_session("mpegts")
            media_pipeline.release_session(token)

    def test_stale_hardware_encoder_choice_falls_back_to_cpu_when_no_longer_functional(self):
        """A saved non-auto encoder must not outlive the environment it was validated in."""
        result = {
            "ok": True,
            "hardware_available": False,
            "active_encoder": media_pipeline.CPU_ENCODER,
            "mode": "cpu",
            "attempts": [
                {"encoder": "h264_nvenc", "ok": False, "duration_seconds": 0.1, "error": "no device"},
                {"encoder": media_pipeline.CPU_ENCODER, "ok": True, "duration_seconds": 0.1, "error": ""},
            ],
        }
        with patch.object(
            media_pipeline,
            "settings",
            return_value={**media_pipeline.DEFAULTS, "enabled": True, "encoder": "h264_nvenc"},
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            self.assertEqual(media_pipeline.active_encoder(), media_pipeline.CPU_ENCODER)

    def test_hardware_encoder_choice_is_honored_when_still_functional(self):
        result = {
            "ok": True,
            "hardware_available": True,
            "active_encoder": "h264_nvenc",
            "mode": "hardware",
            "attempts": [{"encoder": "h264_nvenc", "ok": True, "duration_seconds": 0.1, "error": ""}],
        }
        with patch.object(
            media_pipeline,
            "settings",
            return_value={**media_pipeline.DEFAULTS, "enabled": True, "encoder": "h264_nvenc"},
        ), patch.object(media_pipeline, "capability_test", return_value=result):
            self.assertEqual(media_pipeline.active_encoder(), "h264_nvenc")


if __name__ == "__main__":
    unittest.main()
