from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "installer" / "docker" / "install.py"
SPEC = importlib.util.spec_from_file_location("docker_installer", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class CrossPlatformDockerInstallerTests(unittest.TestCase):
    def test_package_names_are_selected_from_the_build_host(self):
        build_path = ROOT / "installer" / "docker" / "build.py"
        build_spec = importlib.util.spec_from_file_location("docker_installer_build", build_path)
        assert build_spec is not None and build_spec.loader is not None
        build = importlib.util.module_from_spec(build_spec)
        build_spec.loader.exec_module(build)
        expected = {
            "Windows": "M3U-Web-Picker-Windows-Setup",
            "Darwin": "M3U-Web-Picker-macOS-Setup",
            "Linux": "M3U-Web-Picker-Linux-Setup",
        }
        for system, name in expected.items():
            with self.subTest(system=system), patch.object(build.platform, "system", return_value=system):
                self.assertEqual(build.artifact_name(), name)

    def test_platform_install_locations_are_user_owned(self):
        with patch.object(installer, "host_system", return_value="Windows"):
            with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}):
                self.assertEqual(
                    installer.default_install_dir(),
                    Path(r"C:\Users\Test\AppData\Local") / "m3u-web-picker",
                )
        with patch.object(installer, "host_system", return_value="Darwin"):
            self.assertEqual(
                installer.default_install_dir(),
                Path.home() / "Library" / "Application Support" / "m3u-web-picker",
            )
        with patch.object(installer, "host_system", return_value="Linux"):
            with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/user-data"}):
                self.assertEqual(installer.default_install_dir(), Path("/tmp/user-data/m3u-web-picker"))

    def test_fresh_install_skips_mode_and_clean_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with patch("builtins.input", side_effect=AssertionError("fresh install must not prompt")):
                self.assertEqual(installer.choose_install_mode(missing, installer.UPGRADE), installer.CLEAN)

    def test_unrelated_existing_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            unrelated = Path(temporary) / "not-web-picker"
            unrelated.mkdir()
            (unrelated / "family-photos.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "not a Docker M3U Web Picker installation"):
                installer.validate_existing_install(unrelated)
            self.assertTrue((unrelated / "family-photos.txt").is_file())

    def test_valid_existing_install_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary)
            (existing / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (existing / "src").mkdir()
            (existing / "src" / "app.py").write_text("app = object()\n", encoding="utf-8")
            installer.validate_existing_install(existing)

    def test_existing_install_accepts_upgrade_and_confirms_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(installer.choose_install_mode(Path(temporary), "up"), installer.UPGRADE)
        with patch("builtins.input", return_value="clean"):
            installer.confirm_clean()
        with patch("builtins.input", return_value="CL"):
            with self.assertRaises(SystemExit):
                installer.confirm_clean()

    def test_archive_extraction_removes_root_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            destination = root / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("m3u-web-picker-main/docker-compose.yml", "services: {}")
                output.writestr("m3u-web-picker-main/src/app.py", "app = object()")
            installer.extract_github_archive(archive, destination)
            self.assertTrue((destination / "docker-compose.yml").is_file())
            self.assertFalse((destination / "m3u-web-picker-main").exists())

            bad_archive = root / "bad.zip"
            with zipfile.ZipFile(bad_archive, "w") as output:
                output.writestr("m3u-web-picker-main/../../outside.txt", "bad")
            with self.assertRaises(RuntimeError):
                installer.extract_github_archive(bad_archive, destination)

    def test_environment_is_port_9999_and_uses_external_dvr_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            (staging / ".env.example").write_text(
                "M3U_HOST_PORT=9999\nM3U_DVR_DIR=./runtime/recordings\n", encoding="utf-8"
            )
            dvr = staging / "Videos" / "M3U-Web-Picker-DVR"
            with (
                patch.object(installer, "default_dvr_dir", return_value=dvr),
                patch.object(installer, "detect_lan_ipv4", return_value="192.168.1.50"),
            ):
                installer.prepare_install_environment(staging)
            env = (staging / ".env").read_text(encoding="utf-8")
            self.assertIn("M3U_HOST_PORT=9999", env)
            self.assertIn("M3U_EXTERNAL_PORT=9999", env)
            self.assertIn("M3U_LAN_HOST=192.168.1.50", env)
            self.assertIn(f"M3U_DVR_DIR={dvr.as_posix()}", env)
            self.assertTrue(dvr.is_dir())

    def test_installer_never_targets_development_port(self):
        script = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("9998", script)
        self.assertIn("docker-compose.gpu.yml", script)
        readme = (ROOT / "installer" / "docker" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Docker Compose v2", readme)

    def test_fresh_install_downloads_source_and_runs_confirmed_clean_stack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_dir = root / "install"
            source_archive = root / "source.zip"
            with zipfile.ZipFile(source_archive, "w") as output:
                output.writestr("m3u-web-picker-main/docker-compose.yml", "services: {}\n")
                output.writestr("m3u-web-picker-main/docker-compose.gpu.yml", "services: {}\n")
                output.writestr(
                    "m3u-web-picker-main/.env.example",
                    "M3U_HOST_PORT=9999\nM3U_DVR_DIR=./runtime/recordings\n",
                )

            def copy_download(_url: str, destination: Path) -> None:
                shutil.copy2(source_archive, destination)

            dvr = root / "recordings"
            with (
                patch.object(installer, "require_docker", return_value="docker"),
                patch.object(installer, "production_container_status", return_value=""),
                patch.object(installer, "download", side_effect=copy_download),
                patch.object(installer, "detect_lan_ipv4", return_value="10.0.0.8"),
                patch.object(installer, "default_dvr_dir", return_value=dvr),
                patch.object(installer, "compose_command", return_value=["docker", "compose"]),
                patch.object(installer, "run_compose") as run_compose,
                patch.object(installer, "wait_for_setup_page"),
                patch.object(installer.webbrowser, "open"),
            ):
                installer.install(install_dir, "main", "")

            self.assertTrue((install_dir / "docker-compose.yml").is_file())
            env = (install_dir / ".env").read_text(encoding="utf-8")
            self.assertIn("M3U_HOST_PORT=9999", env)
            self.assertIn("M3U_LAN_HOST=10.0.0.8", env)
            self.assertIn(f"M3U_DVR_DIR={dvr.as_posix()}", env)
            self.assertEqual(
                [call.args[2:] for call in run_compose.call_args_list],
                [("down", "-v"), ("up", "-d", "--build"), ("ps",)],
            )

    def test_clean_does_not_carry_saved_app_data_into_new_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_dir = root / "install"
            (install_dir / "src").mkdir(parents=True)
            (install_dir / "src" / "app.py").write_text("old\n", encoding="utf-8")
            (install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (install_dir / ".env").write_text("SAVED_SETTING=yes\n", encoding="utf-8")
            (install_dir / "runtime").mkdir()
            (install_dir / "runtime" / "saved.json").write_text("{}\n", encoding="utf-8")
            source_archive = root / "source.zip"
            with zipfile.ZipFile(source_archive, "w") as output:
                output.writestr("m3u-web-picker-main/docker-compose.yml", "services: {}\n")
                output.writestr("m3u-web-picker-main/src/app.py", "new\n")
                output.writestr(
                    "m3u-web-picker-main/.env.example",
                    "M3U_HOST_PORT=9999\nM3U_DVR_DIR=./runtime/recordings\n",
                )

            def copy_download(_url: str, destination: Path) -> None:
                shutil.copy2(source_archive, destination)

            with (
                patch.object(installer, "require_docker", return_value="docker"),
                patch.object(installer, "production_container_status", return_value=""),
                patch.object(installer, "confirm_clean"),
                patch.object(installer, "download", side_effect=copy_download),
                patch.object(installer, "detect_lan_ipv4", return_value=""),
                patch.object(installer, "default_dvr_dir", return_value=root / "recordings"),
                patch.object(installer, "compose_command", return_value=["docker", "compose"]),
                patch.object(installer, "run_compose"),
                patch.object(installer, "wait_for_setup_page"),
                patch.object(installer.webbrowser, "open"),
            ):
                installer.install(install_dir, "main", installer.CLEAN)

            self.assertNotIn("SAVED_SETTING=yes", (install_dir / ".env").read_text(encoding="utf-8"))
            self.assertFalse((install_dir / "runtime" / "saved.json").exists())

    def test_release_workflow_builds_all_three_from_common_source(self):
        workflow = (ROOT / ".github" / "workflows" / "package-installers.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("python installer/docker/build.py"), 3)
        self.assertIn("M3U-Web-Picker-Windows-Setup.exe", workflow)
        self.assertIn("M3U-Web-Picker-macOS.dmg", workflow)
        self.assertIn("M3U-Web-Picker-Linux.tar.gz", workflow)
        self.assertNotIn("python installer/windows-python/build.py", workflow)


if __name__ == "__main__":
    unittest.main()
