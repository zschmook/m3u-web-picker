from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import logo_registry


class LogoRegistryTests(unittest.TestCase):
    def test_known_good_cache_survives_candidate_url_changes_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            key = "team:nfl:philadelphia-eagles"

            logo_registry.observe(
                db_path,
                key,
                "https://provider.example/eagles-v1.png",
                "provider",
            )
            logo_registry.record_success(
                db_path,
                key,
                source_url="https://provider.example/eagles-v1.png",
                source_kind="provider",
                cache_digest="a" * 64,
                content_type="image/png",
            )

            logo_registry.observe(
                db_path,
                key,
                "https://provider.example/eagles-v2.png",
                "provider",
            )
            logo_registry.record_failure(
                db_path,
                key,
                source_url="https://provider.example/eagles-v2.png",
                source_kind="provider",
            )

            row = logo_registry.lookup(db_path, key)
            self.assertIsNotNone(row)
            self.assertEqual(row["source_url"], "https://provider.example/eagles-v2.png")
            self.assertEqual(row["cache_digest"], "a" * 64)
            self.assertEqual(row["content_type"], "image/png")
            self.assertEqual(row["failure_count"], 1)
            self.assertTrue(row["last_success_at"])
            self.assertTrue(row["last_failure_at"])

    def test_channel_identity_prefers_tvg_id(self):
        identity = logo_registry.channel_identity(
            {
                "tvg_id": "WCAU.US",
                "name": "NBC 10 Philadelphia",
                "group": "Local",
            }
        )
        self.assertEqual(identity, "tvg:wcau.us")

    def test_batch_observation_deduplicates_identity_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            count = logo_registry.observe_many(
                db_path,
                [
                    ("team:nfl:eagles", "https://one.example/logo.png", "provider"),
                    ("team:nfl:eagles", "https://two.example/logo.png", "provider"),
                    ("team:nfl:ravens", "https://three.example/logo.png", "provider"),
                ],
            )
            self.assertEqual(count, 2)
            self.assertEqual(
                logo_registry.lookup(db_path, "team:nfl:eagles")["source_url"],
                "https://one.example/logo.png",
            )


if __name__ == "__main__":
    unittest.main()
