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


class RefactorStructureTests(unittest.TestCase):
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

    def test_shared_ffmpeg_normalization_preserves_option_order(self):
        with patch("media.ffmpeg.shutil.which", return_value="/usr/bin/ffmpeg"):
            args = ffmpeg.normalized_live_input_args(
                "http://provider.test/live.ts",
                video_extra=("-force_key_frames", "expr:gte(t,n_forced*2)"),
            )
        self.assertEqual(args[0], "/usr/bin/ffmpeg")
        self.assertLess(args.index("-pix_fmt"), args.index("-force_key_frames"))
        self.assertLess(args.index("-force_key_frames"), args.index("-c:a"))
        self.assertIn("http://provider.test/live.ts", args)

    def test_roku_adapter_rejects_public_ip(self):
        with self.assertRaises(ValueError):
            roku.normalize_host("8.8.8.8")
        self.assertEqual(roku.normalize_host("10.0.0.2"), "10.0.0.2")


if __name__ == "__main__":
    unittest.main()
