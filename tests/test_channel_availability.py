import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["M3U_DISABLE_SCHEDULER"] = "true"
import core
from channel_availability import AvailabilityCache, candidate_urls, probe_stream


class AvailabilityTests(unittest.TestCase):
    def test_primary_failure_uses_backup_and_recovery_retries_primary(self):
        healthy = {"backup"}
        calls = []
        def probe(url):
            calls.append(url)
            return url in healthy
        cache = AvailabilityCache(probe)
        try:
            cache._check(("primary", "backup"))
            self.assertEqual(cache.lookup(["primary", "backup"]), (True, "backup"))
            self.assertEqual(calls, ["primary", "backup"])
            healthy.clear()
            cache._check(("primary", "backup"))
            self.assertEqual(cache.lookup(["primary", "backup"]), (False, ""))
            healthy.add("primary")
            cache._check(("primary", "backup"))
            self.assertEqual(cache.lookup(["primary", "backup"]), (True, "primary"))
        finally:
            cache.worker.shutdown()

    def test_missing_unknown_and_asynchronous_refresh(self):
        cache = AvailabilityCache(lambda url: None)
        try:
            self.assertEqual(cache.lookup([]), (False, ""))
            self.assertEqual(cache.lookup(["primary"]), (None, "primary"))
            cache.worker.shutdown(wait=True)
            self.assertEqual(cache.lookup(["primary"]), (None, "primary"))
        finally:
            cache.worker.shutdown()

    def test_matching_does_not_substitute_different_local_affiliate(self):
        channel = {"key": "manual:one", "name": "Local NBC", "tvg_id": "WGAL.us"}
        rows = [
            {"name": "Local NBC", "tvg_id": "Other.us", "url": "wrong"},
            {"name": "WGAL HD", "tvg_id": "WGAL.us", "url": "correct"},
        ]
        self.assertEqual(candidate_urls(channel, [], [rows], core.channel_key), ["correct"])

    def test_stale_offline_status_stays_disabled_until_recheck_finishes(self):
        cache = AvailabilityCache(lambda url: False)
        try:
            cache.results[("primary",)] = (0, False, "")
            with patch.object(cache.worker, "submit") as submit:
                self.assertEqual(cache.lookup(["primary"]), (False, ""))
                self.assertEqual(cache.lookup(["primary"]), (False, ""))
                self.assertEqual(submit.call_count, 1)
        finally:
            cache.worker.shutdown()

    def test_probe_requires_media_packet_and_handles_missing_tool(self):
        with patch("channel_availability.shutil.which", return_value=None):
            self.assertIsNone(probe_stream("http://example.test"))
        with patch("channel_availability.shutil.which", return_value="ffprobe"), patch("channel_availability.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b""
            self.assertFalse(probe_stream("http://example.test"))
            run.return_value.stdout = b'{"packets":[{"size":"188","side_data_list":[{"side_data_type":"MPEGTS Stream ID"}]}]}'
            self.assertTrue(probe_stream("http://example.test"))


class SelectionRetentionTests(unittest.TestCase):
    def test_missing_rows_survive_update_restart_and_recover_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.multiple(core, DB_PATH=Path(directory) / "test.db",
                                PLAYLIST_PATH=Path(directory) / "custom.m3u",
                                channels=[], selected_ids=set(), provider_sources=[]), \
                    patch("core.sports.generated_rows", return_value=[]), \
                    patch.object(core.availability, "lookup", return_value=(False, "")):
                core.db_connect().close()
                original = core.parse_m3u_text('#EXTM3U\n#EXTINF:-1 tvg-id="one",One\nhttp://example.test/one\n#EXTINF:-1 tvg-id="two",Two\nhttp://example.test/two\n')
                core.channels = original
                core.selected_ids = {item["id"] for item in original}
                core.write_current_playlist()
                saved_keys = core.load_selected_keys_from_db()
                core.channels = [original[1]]
                core.apply_saved_selections_to_loaded_channels()
                core.write_current_playlist()
                self.assertEqual(core.load_selected_keys_from_db(), saved_keys)
                self.assertIn("/stream/channel/manual/" + core.channel_key(original[0]).split(":", 1)[1] + "/mpegts", core.PLAYLIST_PATH.read_text())
                guide = core.curated_channels_for_guide()
                self.assertEqual([(row["number"], row["name"]) for row in guide], [(1, "One"), (2, "Two")])
                self.assertFalse(guide[0]["available"])
                # Simulate a restart before a provider cache is available.
                core.channels = []
                core.selected_ids = set()
                core.write_current_playlist()
                self.assertEqual(core.load_selected_keys_from_db(), saved_keys)
                core.channels = original
                core.apply_saved_selections_to_loaded_channels()
                core.write_current_playlist()
                self.assertEqual(core.selected_ids, {item["id"] for item in original})
                self.assertEqual(core.load_selected_keys_from_db(), saved_keys)
                # Explicitly deselecting a loaded channel still works.
                core.selected_ids = {original[1]["id"]}
                core.write_current_playlist()
                self.assertEqual(core.load_selected_keys_from_db(), {core.channel_key(original[1])})


if __name__ == "__main__":
    unittest.main()
