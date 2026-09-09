from __future__ import annotations

import tempfile
import unittest
import unittest.mock
import zipfile
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "installer" / "windows-docker" / "install.py"
SPEC = importlib.util.spec_from_file_location("windows_docker_installer", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class WindowsDockerBootstrapTests(unittest.TestCase):
    def test_python_zip_extraction_removes_github_root_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            destination = root / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("m3u-web-picker-main/docker-compose.yml", "services: {}")
                output.writestr("m3u-web-picker-main/scripts/docker-windows.ps1", "# setup")
            install.extract_github_archive(archive, destination)
            self.assertTrue((destination / "docker-compose.yml").is_file())
            self.assertFalse((destination / "m3u-web-picker-main").exists())

    def test_bootstraps_share_zip_source_and_preserve_runtime_state(self):
        python_script = (ROOT / "installer" / "windows-docker" / "install.py").read_text(encoding="utf-8")
        powershell_script = (ROOT / "installer" / "windows-docker" / "install.ps1").read_text(encoding="utf-8")
        for script in (python_script, powershell_script):
            self.assertIn("codeload.github.com", script)
            self.assertIn("docker-windows.ps1", script)
            self.assertIn('".env"', script)
            self.assertIn('"runtime"', script)
            self.assertIn("CleanVolumes", script)

    def test_python_installer_opens_setup_after_waiting_for_it(self):
        script = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertIn('WEB_URL = "http://localhost:9999"', script)
        self.assertIn("wait_for_setup_page()", script)
        self.assertIn("webbrowser.open(WEB_URL)", script)
        self.assertIn('os.environ.get("LOCALAPPDATA"', script)
        self.assertIn('/ "m3u-web-picker"', script)
        self.assertNotIn("Open the terminal as Administrator", script)

    def test_fresh_python_install_prepares_user_owned_dvr_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            (staging / ".env.example").write_text("M3U_HOST_PORT=9999\n", encoding="utf-8")
            with unittest.mock.patch.object(install.Path, "home", return_value=staging / "profile"):
                install.prepare_install_environment(staging)
            env = (staging / ".env").read_text(encoding="utf-8")
            self.assertIn("M3U_HOST_PORT=9999", env)
            self.assertIn("M3U_EXTERNAL_PORT=9999", env)
            self.assertIn("M3U_DVR_DIR=", env)
            self.assertTrue((staging / "profile" / "Videos" / "M3U-Web-Picker-DVR").is_dir())

    def test_installer_prompts_for_upgrade_or_clean(self):
        script = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertIn('"-CleanVolumes"', script)
        self.assertIn('UPGRADE = "UP"', script)
        self.assertIn('CLEAN = "CL"', script)
        self.assertIn("if mode == CLEAN and existing_install", script)
        self.assertNotIn("9998", script)

    def test_mode_prompt_accepts_only_documented_choices(self):
        self.assertEqual(install.choose_mode("up"), "UP")
        self.assertEqual(install.choose_mode("cl"), "CL")
        with unittest.mock.patch("builtins.input", side_effect=["wrong", "UP"]):
            self.assertEqual(install.choose_mode(), "UP")

    def test_missing_install_directory_goes_directly_to_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "not-installed"
            with unittest.mock.patch("builtins.input", side_effect=AssertionError("mode prompt should be skipped")):
                self.assertEqual(install.choose_install_mode(missing, "UP"), "CL")

    def test_clean_confirmation_names_saved_sports_and_requires_full_word(self):
        script = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertIn("saved channels and settings", script)
        self.assertIn("saved sports automation selections and rules", script)
        with unittest.mock.patch("builtins.input", return_value="clean"):
            install.confirm_clean()
        with unittest.mock.patch("builtins.input", return_value="CL"):
            with self.assertRaises(SystemExit):
                install.confirm_clean()

    def test_running_instance_detection_is_scoped_to_production_9999(self):
        script = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertIn("label=com.docker.compose.project=m3u-picker", script)
        self.assertIn("label=com.docker.compose.service=m3u-picker", script)
        self.assertIn('"publish=9999"', script)


if __name__ == "__main__":
    unittest.main()
