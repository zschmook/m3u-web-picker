from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import tempfile
from types import SimpleNamespace
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
    def test_upgrade_uses_persisted_vpn_network_and_updated_env_image(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            path = folder / 'runtime/vpn/docker-compose.vpn.json'
            path.parent.mkdir(parents=True)
            path.write_text('{}')
            (folder / '.env').write_text('M3U_IMAGE=ghcr.io/zschmook/m3u-web-picker:v42\n')
            command = installer.compose_command('docker', folder)
            self.assertEqual(command, ['docker', 'compose', '--env-file', str(folder/'.env'), '-f', str(path)])

    def setUp(self):
        for name in ('prepare_helper','stop_helper','install_helper'):
            helper=patch.object(installer,name)
            helper.start();self.addCleanup(helper.stop)
        detected=patch.object(installer,"detect_lan_subnet",return_value="192.168.1.0/24")
        detected.start();self.addCleanup(detected.stop)
    def compose_config(self, _command, *, cwd, **_kwargs):
        volumes = [
            dict(type="bind", source=str(cwd / "runtime" / "backups"), target="/backups"),
            dict(type="bind", source=str(cwd / "runtime" / "jellyfin-cache-disabled"), target="/jellyfin-cache"),
            dict(type="bind", source=installer.dotenv_value(cwd / ".env", "M3U_DVR_DIR"), target="/recordings"),
            dict(type="volume", source="m3u-picker-data", target="/app/data"),
        ]
        return SimpleNamespace(stdout=json.dumps({"services": {"m3u-picker": {"volumes": volumes}}}))

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
                output.writestr("m3u-web-picker-main/docker-compose.release.yml", "services: {}\n")
                output.writestr("m3u-web-picker-main/docker-compose.gpu.yml", "services: {}\n")
                output.writestr(
                    "m3u-web-picker-main/.env.example",
                    "M3U_HOST_PORT=9999\nM3U_DVR_DIR=./runtime/recordings\n",
                )

            def copy_download(_url: str, destination: Path) -> None:
                shutil.copy2(source_archive, destination)

            dvr = root / "recordings"
            def check_folders(_command, directory, *_arguments):
                self.assertTrue((directory / "runtime" / "backups").is_dir())
                self.assertTrue((directory / "runtime" / "jellyfin-cache-disabled").is_dir())
                self.assertTrue(dvr.is_dir())
            with (
                patch.object(installer, "require_docker", return_value="docker"),
                patch.object(installer, "production_container_status", return_value=""),
                patch.object(installer, "download", side_effect=copy_download),
                patch.object(installer, "detect_lan_ipv4", return_value="10.0.0.8"),
                patch.object(installer, "default_dvr_dir", return_value=dvr),
                patch.object(installer, "compose_command", return_value=["docker", "compose"]),
                patch.object(installer.subprocess, "run", side_effect=self.compose_config),
                patch.object(installer, "run_compose", side_effect=check_folders) as run_compose,
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
                [("pull",), ("down", "-v"), ("up", "-d", "--no-build"), ("ps",)],
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
                output.writestr("m3u-web-picker-main/docker-compose.release.yml", "services: {}\n")
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
                patch.object(installer.subprocess, "run", side_effect=self.compose_config),
                patch.object(installer, "run_compose"),
                patch.object(installer, "wait_for_setup_page"),
                patch.object(installer.webbrowser, "open"),
            ):
                installer.install(install_dir, "main", installer.CLEAN)

            self.assertNotIn("SAVED_SETTING=yes", (install_dir / ".env").read_text(encoding="utf-8"))
            self.assertFalse((install_dir / "runtime" / "saved.json").exists())

    def test_latest_release_pins_configuration_and_image_to_same_tag(self):
        response = io.BytesIO(json.dumps({"tag_name": "v32"}).encode())
        with patch.object(installer.urllib.request, "urlopen", return_value=response):
            url, tag = installer.resolve_source("latest")
        self.assertTrue(url.endswith("/zip/refs/tags/v32"))
        self.assertEqual(tag, "v32")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(installer, "detect_lan_ipv4", return_value=""), \
                    patch.object(installer, "default_dvr_dir", return_value=root / "recordings"):
                installer.prepare_install_environment(root, tag)
            self.assertIn("M3U_IMAGE=ghcr.io/zschmook/m3u-web-picker:v32", (root / ".env").read_text())

    def test_failed_pull_preserves_existing_source_and_never_stops_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "install"
            (current / "src").mkdir(parents=True)
            (current / "src/app.py").write_text("original")
            (current / "docker-compose.yml").write_text("services: {}")
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("repo/docker-compose.release.yml", "services: {}")
            def download(_url, destination):
                shutil.copy2(archive, destination)
            with patch.object(installer, "require_docker", return_value="docker"), \
                    patch.object(installer, "production_container_status", return_value="running"), \
                    patch.object(installer, "download", side_effect=download), \
                    patch.object(installer, "prepare_install_environment"), \
                    patch.object(installer, "prepare_bind_mount_directories"), \
                    patch.object(installer, "compose_command", return_value=["docker", "compose"]), \
                    patch.object(installer, "run_compose", side_effect=RuntimeError("pull failed")) as compose:
                with self.assertRaisesRegex(RuntimeError, "pull failed"):
                    installer.install(current, "v32", installer.UPGRADE)
            self.assertEqual((current / "src/app.py").read_text(), "original")
            self.assertEqual([call.args[2:] for call in compose.call_args_list], [("pull",)])

    def test_bind_folders_use_resolved_compose_paths_and_keep_existing_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "AppData" / "m3u-web-picker"
            install.mkdir(parents=True)
            external = root / "Backup Folder"
            external.mkdir()
            (external / "saved.zip").write_bytes(b"keep")
            config = {"services": {"m3u-picker": {"volumes": [
                dict(type="bind", source=str(external), target="/backups"),
                dict(type="bind", source="runtime/jellyfin-cache-disabled", target="/jellyfin-cache"),
                dict(type="volume", source="m3u-picker-data", target="/app/data"),
            ]}, "other": {"volumes": [dict(type="bind", source=str(root / "other"), target="/other")]}}}
            with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(config))) as compose:
                installer.prepare_bind_mount_directories(["docker", "compose"], install)
            self.assertTrue((install / "runtime" / "jellyfin-cache-disabled").is_dir())
            self.assertEqual((external / "saved.zip").read_bytes(), b"keep")
            self.assertFalse((install / "m3u-picker-data").exists())
            self.assertFalse((root / "other").exists())
            self.assertEqual(compose.call_args.args[0], ["docker", "compose", "config", "--format", "json"])

    def test_invalid_mount_folder_is_reported_with_its_role_and_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blocked = root / "backups"
            blocked.write_text("existing file")
            config = {"services": {"m3u-picker": {"volumes": [
                dict(type="bind", source=str(blocked), target="/backups"),
            ]}}}
            with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(config))):
                with self.assertRaisesRegex(RuntimeError, r"host folder for /backups") as failure:
                    installer.prepare_bind_mount_directories(["docker", "compose"], root)
            self.assertIn(str(blocked), str(failure.exception))
            self.assertEqual(blocked.read_text(), "existing file")

    def test_mount_preparation_failure_leaves_running_installation_and_source_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "install"
            (current / "src").mkdir(parents=True)
            (current / "src/app.py").write_text("original")
            (current / "docker-compose.yml").write_text("services: {}")
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("repo/docker-compose.release.yml", "services: {}")
            def download(_url, destination):
                shutil.copy2(archive, destination)
            with patch.object(installer, "require_docker", return_value="docker"), \
                    patch.object(installer, "production_container_status", return_value="running"), \
                    patch.object(installer, "download", side_effect=download), \
                    patch.object(installer, "prepare_install_environment"), \
                    patch.object(installer, "prepare_bind_mount_directories", side_effect=RuntimeError("host folder denied")), \
                    patch.object(installer, "compose_command", return_value=["docker", "compose"]), \
                    patch.object(installer, "run_compose") as compose:
                with self.assertRaisesRegex(RuntimeError, "host folder denied"):
                    installer.install(current, "v32", installer.UPGRADE)
            self.assertEqual((current / "src/app.py").read_text(), "original")
            compose.assert_not_called()

    def test_release_workflow_builds_all_three_from_common_source(self):
        workflow = (ROOT / ".github" / "workflows" / "package-installers.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("python installer/docker/build.py"), 3)
        self.assertIn("M3U-Web-Picker-Windows-Setup.exe", workflow)
        self.assertIn("M3U-Web-Picker-macOS.dmg", workflow)
        self.assertIn("M3U-Web-Picker-Linux.tar.gz", workflow)
        self.assertNotIn("python installer/windows-python/build.py", workflow)


if __name__ == "__main__":
    unittest.main()

class LanSubnetDetectionTests(unittest.TestCase):
    def check(self, system, address, stdout, expected):
        with patch.object(installer,'host_system',return_value=system),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=stdout)):
            self.assertEqual(installer.detect_lan_subnet(address),expected)
    def test_windows_matches_host_address_and_uses_real_prefix(self):
        self.check('Windows','192.168.49.9',json.dumps([{'IPAddress':'172.20.0.1','PrefixLength':16},{'IPAddress':'192.168.49.9','PrefixLength':20}]),'192.168.48.0/20')
    def test_linux_matches_host_address_not_docker_bridge(self):
        self.check('Linux','10.0.5.17',json.dumps([{'addr_info':[{'local':'172.18.0.1','prefixlen':16}]},{'addr_info':[{'local':'10.0.5.17','prefixlen':23}]}]),'10.0.4.0/23')
    def test_macos_hex_netmask(self):
        self.check('Darwin','10.0.5.17','\tinet 10.0.5.17 netmask 0xfffffc00 broadcast 10.0.7.255\n','10.0.4.0/22')
    def test_unknown_interface_does_not_guess(self):
        self.check('Linux','10.0.0.18','[]','')
    def test_tailnet_and_public_addresses_are_not_home_lan(self):
        for address in ['100.80.123.46','8.8.8.8','127.0.0.1']:
            with patch.object(installer.subprocess,'run') as command:
                self.assertEqual(installer.detect_lan_subnet(address),'');command.assert_not_called()
    def test_missing_tools_fall_back_without_blocking_install(self):
        with patch.object(installer.subprocess,'run',side_effect=FileNotFoundError):
            self.assertEqual(installer.detect_lan_subnet('10.0.0.18'),'')
