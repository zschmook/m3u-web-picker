from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jellyfin_cache


class JellyfinCacheTests(unittest.TestCase):
    def _env(self, container_path: Path, host_path: str):
        return patch.dict(
            os.environ,
            {
                "M3U_JELLYFIN_CACHE_CONTAINER_DIR": str(container_path),
                "M3U_JELLYFIN_CACHE_HOST_DIR": host_path,
            },
            clear=False,
        )

    def test_path_must_match_runtime_mount(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "cache"
            cache_dir.mkdir()
            with self._env(cache_dir, "/Users/test/Library/Caches/jellyfin"):
                result = jellyfin_cache.validate_host_path(
                    "/Users/test/Library/Caches/other-jellyfin"
                )
            self.assertFalse(result["ok"])
            self.assertIn("does not match", result["message"])

    def test_acknowledgement_is_required_before_cleanup_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "picker.db"
            cache_dir = Path(temp_dir) / "jellyfin-cache"
            cache_dir.mkdir()
            host_path = "/Users/test/Library/Caches/jellyfin"
            with self._env(cache_dir, host_path):
                with self.assertRaises(ValueError):
                    jellyfin_cache.update_settings(
                        db_path,
                        using_jellyfin=True,
                        acknowledged=False,
                        cleanup_enabled=True,
                        host_path=host_path,
                    )

    def test_successful_cleanup_removes_contents_but_keeps_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "picker.db"
            cache_dir = Path(temp_dir) / "jellyfin-cache"
            cache_dir.mkdir()
            (cache_dir / "image.jpg").write_bytes(b"image")
            nested = cache_dir / "nested"
            nested.mkdir()
            (nested / "metadata.json").write_text("{}", encoding="utf-8")
            host_path = "/Users/test/Library/Caches/jellyfin"

            with self._env(cache_dir, host_path):
                settings = jellyfin_cache.update_settings(
                    db_path,
                    using_jellyfin=True,
                    acknowledged=True,
                    cleanup_enabled=True,
                    host_path=host_path,
                )
                self.assertTrue(settings["cleanup_enabled"])
                result = jellyfin_cache.clear_configured_cache(db_path)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["deleted_entries"], 2)
            self.assertTrue(cache_dir.exists())
            self.assertEqual(list(cache_dir.iterdir()), [])

    def test_cleanup_runs_only_after_wrapped_update_returns_successfully(self):
        calls = []

        def successful_update(*, trigger="manual"):
            calls.append(("update", trigger))
            return {"ok": True, "provider_warnings": []}

        core = SimpleNamespace(
            DB_PATH=Path("/tmp/not-used.db"),
            run_master_update=successful_update,
        )
        cleanup_result = {
            "status": "success",
            "deleted_entries": 3,
            "message": "done",
        }
        with patch.object(jellyfin_cache, "clear_configured_cache", return_value=cleanup_result) as cleanup:
            jellyfin_cache.install(core)
            result = core.run_master_update(trigger="scheduled")

        self.assertEqual(calls, [("update", "scheduled")])
        cleanup.assert_called_once_with(core.DB_PATH)
        self.assertEqual(result["jellyfin_cache_cleanup"], cleanup_result)

    def test_failed_update_never_clears_cache(self):
        def failed_update(*, trigger="manual"):
            raise RuntimeError("provider failed")

        core = SimpleNamespace(
            DB_PATH=Path("/tmp/not-used.db"),
            run_master_update=failed_update,
        )
        with patch.object(jellyfin_cache, "clear_configured_cache") as cleanup:
            jellyfin_cache.install(core)
            with self.assertRaises(RuntimeError):
                core.run_master_update(trigger="manual")

        cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
