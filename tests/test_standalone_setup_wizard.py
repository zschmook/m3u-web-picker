from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import setup_wizard


ROOT = Path(__file__).resolve().parents[1]


class StandaloneSetupWizardTests(unittest.TestCase):
    def test_testing_mode_forces_optional_integrations_off(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = setup_wizard.save_choices(
                "testing",
                {"dvr": True, "jellyfin": True, "sports_api": True},
                path,
            )
        self.assertEqual(state["mode"], "testing")
        self.assertEqual(
            state["features"],
            {"dvr": False, "jellyfin": False, "sports_api": False},
        )
        self.assertEqual(state["current_step"], "channels")

    def test_provider_mode_starts_without_optional_features(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = setup_wizard.save_choices("provider", path=path)
            self.assertEqual(
                state["features"],
                {"dvr": False, "jellyfin": False, "sports_api": False},
            )
            self.assertEqual(state["current_step"], "provider")
            self.assertEqual(state["media_server"], {"type": "none"})

    def test_preview_contains_only_selected_optional_mounts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            setup_wizard.save_choices("provider", path=path)
            state = setup_wizard.save_state(
                {"features": {"dvr": True, "jellyfin": False, "sports_api": False}},
                path=path,
            )
            state["dvr"]["host_path"] = "C:/DVR"
            preview = setup_wizard.build_preview(state, Path(folder) / "output")
        self.assertIn('M3U_DVR_DIR="C:/DVR"', preview["env"])
        self.assertIn('source: "C:/DVR"', preview["compose"])
        self.assertNotIn("jellyfin-cache", preview["compose"])

    def test_host_path_validation_rejects_drive_root(self):
        with self.assertRaisesRegex(ValueError, "entire drive"):
            setup_wizard.normalize_host_path("C:/", label="DVR")

    def test_setup_compose_isolated_from_live_runtime(self):
        compose = (ROOT / "docker-compose.setup.yml").read_text(encoding="utf-8")
        self.assertIn('"9998:9998"', compose)
        self.assertIn("m3u-picker-setup-data", compose)
        self.assertIn("src.setup_runtime:application", compose)
        self.assertIn("m3u-picker-setup-recordings", compose)
        self.assertIn("target: /recordings", compose)
        self.assertNotIn("docker.sock", compose)

    def test_setup_ui_matches_requested_flow(self):
        script = (ROOT / "static/js/setup_wizard.js").read_text(encoding="utf-8")
        self.assertIn("Just Testing", script)
        self.assertIn("Use My Provider", script)
        self.assertIn("Save Channels", script)
        self.assertIn("Hide SD / Low Bandwidth channels", script)
        self.assertIn("setup-channel-status", script)
        self.assertIn("hide_sd", script)
        self.assertIn("shown selected", script)
        self.assertIn("total selected", script)
        self.assertNotIn("Choose optional features", script)
        self.assertIn("Set up DVR", script)
        self.assertIn("syncDvrFields", script)
        self.assertIn("Sports Automation", script)
        self.assertIn("Add Sports API schedules?", script)
        self.assertIn("Choose a media server", script)
        self.assertIn("No media server", script)
        self.assertIn("Jellyfin", script)
        self.assertIn("Plex", script)
        self.assertLess(script.index('{id: "sports"'), script.index('{id: "api"'))
        self.assertLess(script.index('{id: "api"'), script.index('{id: "dvr"'))
        self.assertLess(script.index('{id: "dvr"'), script.index('{id: "media"'))
        self.assertIn('href="https://api-sports.io"', script)
        self.assertIn("Sign up with API-SPORTS", script)
        self.assertLess(
            script.index('href="https://api-sports.io"'),
            script.index('id="sportsApiEnabled"'),
        )
        self.assertIn("Build & Restart", script)
        self.assertIn("Preparing your guide", script)
        self.assertIn("/api/setup/build-status", script)
        self.assertIn("window.location.replace", script)
        stylesheet = (ROOT / "static" / "css" / "setup_wizard.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("[hidden] { display: none !important; }", stylesheet)

    def test_setup_build_starts_and_waits_for_initial_master_update(self):
        source = (ROOT / "src" / "setup_app.py").read_text(encoding="utf-8")
        self.assertIn('master_update_worker.start(trigger="setup")', source)
        self.assertIn('@app.get("/api/setup/build-status")', source)
        self.assertIn("The initial guide update was interrupted", source)
        self.assertIn("Wait for it to finish before starting over", source)
        self.assertIn('media_server.get("type") == "plex"', source)
        self.assertIn('"completed": False', source)
        self.assertIn('"completed": True', source)


if __name__ == "__main__":
    unittest.main()
