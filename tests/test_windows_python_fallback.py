from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "installer" / "windows-python" / "install.py"
SPEC = importlib.util.spec_from_file_location("windows_python_fallback", MODULE_PATH)
assert SPEC and SPEC.loader
fallback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fallback)


class WindowsPythonFallbackTests(unittest.TestCase):
    def test_fresh_directory_goes_directly_to_clean_install(self):
        with tempfile.TemporaryDirectory() as temporaryH:
            missing = Path(temporaryH) / "not-installed"
            with patch("builtins.input", side_effect=AssertionError("fresh install must not prompt")):
                self.assertEqual(fallback.choose_install_mode(missing), fallback.CLEAN)

    def test_existing_install_accepts_upgrade_short_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(fallback.choose_install_mode(Path(temporary), "up"), fallback.UPGRADE)

    def test_clean_requires_full_confirmation(self):
        with patch("builtins.input", return_value="no"):
            with self.assertRaises(SystemExit):
                fallback.confirm_clean()
        with patch("builtins.input", return_value="clean"):
            fallback.confirm_clean()

    def test_defaults_do_not_use_program_files_or_docker_port(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}):
            self.assertEqual(
                fallback.default_root(),
                Path(r"C:\Users\Test\AppData\Local") / "m3u-web-picker",
            )
        self.assertEqual(fallback.WEB_URL, "http://localhost:9999")

    def test_github_archive_removes_single_root_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            output = root / "output"
            output.mkdir()
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("m3u-web-picker-main/src/app.py", "app = object()")
                target.writestr("m3u-web-picker-main/requirements.txt", "Flask\n")
            fallback.extract_github_archive(archive, output)
            self.assertTrue((output / "src" / "app.py").is_file())
            self.assertTrue((output / "requirements.txt").is_file())

    def test_zip_target_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                fallback.safe_zip_target(Path(temporary), "../outside.txt")

    def test_ffmpeg_archive_requires_both_executables(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "ffmpeg.zip"
            output = root / "tools"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("ffmpeg-build/bin/ffmpeg.exe", b"ffmpeg")
            with self.assertRaisesRegex(RuntimeError, "ffprobe.exe"):
                fallback.extract_ffmpeg_tools(archive, output)

    def test_ffmpeg_archive_extracts_ffmpeg_and_ffprobe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "ffmpeg.zip"
            output = root / "tools"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("ffmpeg-build/bin/ffmpeg.exe", b"ffmpeg")
                target.writestr("ffmpeg-build/bin/ffprobe.exe", b"ffprobe")
            fallback.extract_ffmpeg_tools(archive, output)
            self.assertEqual((output / "ffmpeg.exe").read_bytes(), b"ffmpeg")
            self.assertEqual((output / "ffprobe.exe").read_bytes(), b"ffprobe")

    def test_host_environment_uses_absolute_host_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            dvr = root / "recordings"
            with patch.object(fallback, "detect_lan_ipv4", return_value="192.168.1.50"):
                path = fallback.write_host_env(root, root / "app", dvr)
            text = path.read_text(encoding="utf-8")
            self.assertIn(f"M3U_DATA_DIR={root / 'data'}", text)
            self.assertIn(f"M3U_DVR_CONTAINER_DIR={dvr}", text)
            self.assertIn(f"M3U_DVR_HOST_DIR={dvr}", text)
            self.assertIn("M3U_PORT=9999", text)
            self.assertIn("M3U_LAN_HOST=192.168.1.50", text)

    def test_generated_launcher_uses_private_environment_and_host_runtime(self):
        self.assertIn('ROOT / "venv" / "Scripts" / "pythonw.exe"', fallback.LAUNCHER)
        self.assertIn('"PYTHONPATH"', fallback.LAUNCHER)
        self.assertIn('str(APP / "src")', fallback.LAUNCHER)
        self.assertIn('"-m", "host_runtime"', fallback.LAUNCHER)
        self.assertIn('ROOT / "host.env"', fallback.LAUNCHER)


if __name__ == "__main__":
    unittest.main()
