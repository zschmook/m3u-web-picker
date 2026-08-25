from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MediaPipelineContractTests(unittest.TestCase):
    def test_settings_and_wizard_expose_encoding_controls(self):
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        settings = (ROOT / "static/js/ui_encoding_settings.js").read_text(encoding="utf-8")
        onboarding = (ROOT / "static/js/onboarding.js").read_text(encoding="utf-8")
        self.assertIn("Settings → Encoding", onboarding)
        self.assertIn("uiEncodingEnabled", sidebar)
        self.assertIn("uiCommercialDetectionEnabled", sidebar)
        self.assertIn("commercial_detection_enabled", settings)
        self.assertIn("channel analysis and learning remain active", settings)
        self.assertIn("/api/media-pipeline/test", settings)
        self.assertIn("/playlist/channels.direct.m3u", sidebar)

    def test_output_routes_include_encoded_and_direct_playlists(self):
        outputs = (ROOT / "api/outputs.py").read_text(encoding="utf-8")
        self.assertIn('"/playlist/channels.direct.m3u"', outputs)
        self.assertIn('"/stream/channel/<kind>/<identity>/mpegts"', outputs)
        self.assertIn('encoded=media_pipeline.settings()["enabled"]', outputs)


if __name__ == "__main__":
    unittest.main()
