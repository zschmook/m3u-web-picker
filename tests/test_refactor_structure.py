from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from media import ffmpeg
from playback import roku
from runtime_state import RuntimeState
from settings import load_settings
import sports
import sports_taxonomy


ROOT = Path(__file__).resolve().parents[1]


class RefactorStructureTests(unittest.TestCase):
    def test_windows_docker_setup_creates_complete_env_before_lan_override(self):
        script = (ROOT / "scripts" / "docker-windows.ps1").read_text(encoding="utf-8")
        self.assertIn('$envExamplePath = Join-Path $repoRoot ".env.example"', script)
        self.assertIn("Copy-Item -LiteralPath $envExamplePath -Destination $envPath", script)
        self.assertLess(
            script.index("Copy-Item -LiteralPath $envExamplePath"),
            script.index('Set-DotEnvValue -Path $envPath -Name "M3U_LAN_HOST"'),
        )

    def test_windows_docker_setup_applies_gpu_override_when_nvidia_is_available(self):
        script = (ROOT / "scripts" / "docker-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-Command nvidia-smi -ErrorAction SilentlyContinue", script)
        self.assertIn('$composeArgs += @("-f", "docker-compose.gpu.yml")', script)
        self.assertIn("docker compose @composeArgs up -d --build", script)

    def test_windows_docker_setup_defaults_dvr_to_c_drive_folder(self):
        shell_setup = (ROOT / "scripts" / "docker-setup.sh").read_text(encoding="utf-8")
        powershell_setup = (ROOT / "scripts" / "docker-windows.ps1").read_text(encoding="utf-8")
        self.assertIn('M3U_DVR_DIR=C:/DVR', shell_setup)
        self.assertIn('mkdir -p /c/DVR', shell_setup)
        self.assertIn('$dvrPath = "C:/DVR"', powershell_setup)
        self.assertIn('New-Item -ItemType Directory -Path "C:\\DVR"', powershell_setup)

    def test_cross_platform_installers_describe_gpu_behavior(self):
        docker_setup = (ROOT / "scripts" / "docker-setup.sh").read_text(encoding="utf-8")
        macos_setup = (ROOT / "installer" / "macos" / "install.command").read_text(encoding="utf-8")
        self.assertIn('compose_files="$compose_files -f docker-compose.gpu.yml"', docker_setup)
        self.assertIn("GPU passthrough is not supported yet for Docker installs on macOS", docker_setup)
        self.assertIn("GPU acceleration is not supported yet by the macOS installer", macos_setup)

    def test_core_database_schema_is_created_by_database_module(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            conn = database.connect(path)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()
        self.assertTrue({"selections", "custom_groups", "group_channels"} <= tables)

    def test_sports_taxonomy_stays_exported_through_sports_facade(self):
        self.assertIs(sports.SPORT_DEFINITIONS, sports_taxonomy.SPORT_DEFINITIONS)
        self.assertIs(sports.LEAGUE_DEFINITIONS, sports_taxonomy.LEAGUE_DEFINITIONS)
        self.assertEqual(sports.DEFAULT_SETTINGS, sports_taxonomy.DEFAULT_SETTINGS)

    def test_runtime_state_owns_transient_locks_and_progress(self):
        state = RuntimeState()
        self.assertFalse(state.provider_progress["active"])
        self.assertFalse(state.master_update["running"])
        self.assertFalse(state.scan_cancel_event.is_set())

    def test_settings_reads_dynamic_lan_override(self):
        with patch.dict(
            "os.environ",
            {"M3U_LAN_HOST": "10.0.0.22", "M3U_EXTERNAL_PORT": "1000"},
            clear=False,
        ):
            settings = load_settings()
        self.assertEqual(settings.lan_host, "10.0.0.22")
        self.assertEqual(settings.external_port, 1000)

    def test_settings_reads_saved_external_port_override(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "config.json").write_text(
                '{"network": {"external_port": 9997}}', encoding="utf-8"
            )
            with patch.dict(
                "os.environ",
                {"M3U_DATA_DIR": temp, "M3U_EXTERNAL_PORT": "9999"},
                clear=False,
            ):
                settings = load_settings()
        self.assertEqual(settings.external_port, 9997)

    def test_shared_ffmpeg_normalization_preserves_option_order(self):
        with (
            patch("media.ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("media.ffmpeg.media_pipeline.active_encoder", return_value="h264_nvenc"),
        ):
            args = ffmpeg.normalized_live_input_args(
                "http://provider.test/live.ts",
                video_extra=("-force_key_frames", "expr:gte(t,n_forced*2)"),
            )
        self.assertEqual(args[0], "/usr/bin/ffmpeg")
        self.assertLess(args.index("-pix_fmt"), args.index("-vf"))
        self.assertEqual(args[args.index("-vf") + 1], "setpts=PTS-STARTPTS")
        self.assertLess(args.index("-vf"), args.index("-force_key_frames"))
        self.assertLess(args.index("-force_key_frames"), args.index("-c:a"))
        self.assertEqual(args[args.index("-preset") + 1], "p1")
        self.assertEqual(args[args.index("-tune") + 1], "ll")
        self.assertEqual(args[args.index("-zerolatency") + 1], "1")
        self.assertEqual(args[args.index("-delay") + 1], "0")
        self.assertEqual(args[args.index("-bf") + 1], "0")
        self.assertIn("http://provider.test/live.ts", args)

    def test_roku_adapter_rejects_public_ip(self):
        with self.assertRaises(ValueError):
            roku.normalize_host("8.8.8.8")
        self.assertEqual(roku.normalize_host("10.0.0.2"), "10.0.0.2")


if __name__ == "__main__":
    unittest.main()
