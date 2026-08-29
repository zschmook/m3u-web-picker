from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import commercial_signatures


def signature_points(duration: int, *, variant: int = 0):
    points = []
    for index in range(duration):
        center_hash = ((index + 1) * 0x0102040810204081) & ((1 << 64) - 1)
        center_hash ^= variant
        tile_hashes = tuple(
            (center_hash ^ (tile * 0x0101010101010101)) & ((1 << 64) - 1)
            for tile in range(commercial_signatures.TILE_COUNT)
        )
        colors = tuple(
            (index * 13 + tile * 7 + channel * 19 + variant) % 256
            for tile in range(commercial_signatures.TILE_COUNT)
            for channel in range(3)
        )
        points.append((center_hash, tile_hashes, colors))
    return points


class CommercialSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "signatures.db"
        commercial_signatures._KNOWN_CACHE.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_three_independent_sightings_promote_one_probable_commercial(self):
        points = signature_points(6)

        for event_id in ("break-1", "break-2", "break-3"):
            commercial_signatures.record_episode(
                self.db_path,
                "tvg:nbc.example",
                event_id,
                points,
            )

        stats = commercial_signatures.library_stats(self.db_path)
        self.assertEqual(stats["classified"], 1)
        self.assertEqual(stats["candidates"], 0)
        self.assertEqual(stats["occurrences"], 3)

    def test_overlapping_windows_in_one_break_cannot_promote_themselves(self):
        repeated = signature_points(6)
        commercial_signatures.record_episode(
            self.db_path,
            "tvg:nbc.example",
            "same-break",
            repeated + repeated + repeated,
        )

        stats = commercial_signatures.library_stats(self.db_path)
        self.assertEqual(stats["classified"], 0)
        self.assertGreater(stats["candidates"], 0)
        with closing(sqlite3.connect(self.db_path)) as conn:
            maximum = conn.execute(
                "SELECT MAX(occurrence_count) FROM commercial_ad_signatures_v2"
            ).fetchone()[0]
        self.assertEqual(maximum, 1)

    def test_user_confirmation_promotes_current_episode_immediately(self):
        result = commercial_signatures.record_episode(
            self.db_path,
            "tvg:cbs.example",
            "manual-break",
            signature_points(12),
            user_confirmed=True,
        )

        self.assertGreaterEqual(result["promoted"], 1)
        self.assertGreaterEqual(
            commercial_signatures.library_stats(self.db_path)["classified"], 1
        )

    def test_repeat_match_tolerates_one_second_boundary_drift(self):
        original = signature_points(6)
        prefix = signature_points(2, variant=5)
        shifted = prefix + original[:4]

        commercial_signatures.record_episode(
            self.db_path,
            "tvg:nbc.example",
            "break-1",
            original,
        )
        result = commercial_signatures.record_episode(
            self.db_path,
            "tvg:nbc.example",
            "break-2",
            shifted,
        )

        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(
            commercial_signatures.library_stats(self.db_path)["occurrences"],
            2,
        )

    def test_validated_local_color_episode_can_seed_candidates(self):
        result = commercial_signatures.record_episode(
            self.db_path,
            "tvg:news.example",
            "local-color-1",
            signature_points(12),
            trigger_reason="local-color",
        )

        self.assertGreater(result["windows"], 0)
        self.assertGreater(len(result["signature_ids"]), 0)
        self.assertGreater(
            commercial_signatures.library_stats(self.db_path)["occurrences"], 0
        )

    def test_manual_confirmation_can_promote_local_color_episode(self):
        result = commercial_signatures.record_episode(
            self.db_path,
            "tvg:news.example",
            "local-color-manual",
            signature_points(6),
            trigger_reason="local-color",
            user_confirmed=True,
        )

        self.assertEqual(result["promoted"], 1)
        self.assertEqual(len(result["signature_ids"]), 1)

    def test_countdown_episode_is_never_added_to_ad_library(self):
        result = commercial_signatures.record_episode(
            self.db_path,
            "tvg:fast.example",
            "clock-break-1",
            signature_points(12),
        )

        self.assertEqual(result["skipped"], "countdown-clock")
        self.assertEqual(commercial_signatures.library_stats(self.db_path)["occurrences"], 0)

    def test_live_match_requires_two_ordered_three_second_anchors(self):
        points = signature_points(12)
        for index in range(5):
            commercial_signatures.record_episode(
                self.db_path,
                "tvg:abc.example",
                f"break-{index}",
                points,
            )

        result = commercial_signatures.match_live(
            self.db_path, "tvg:abc.example", points[-10:]
        )

        self.assertTrue(result["matched"])
        self.assertAlmostEqual(result["score"], 1.0)
        self.assertEqual(len(result["signature_ids"]), 2)
        self.assertEqual(result["seconds_remaining"], 2)
        self.assertGreaterEqual(result["confidence"], 0.70)

    def test_known_ad_does_not_cross_seed_another_channel(self):
        points = signature_points(10)
        for event_id in ("break-1", "break-2", "break-3"):
            commercial_signatures.record_episode(
                self.db_path,
                "tvg:nbc.example",
                event_id,
                points,
            )

        result = commercial_signatures.match_live(
            self.db_path,
            "tvg:cbs.example",
            points[-10:],
        )

        self.assertFalse(result["matched"])
        self.assertEqual(
            commercial_signatures.library_stats(
                self.db_path, "tvg:cbs.example"
            )["classified"],
            0,
        )

    def test_sequence_match_tolerates_small_visual_variation(self):
        original = signature_points(6)
        variant = signature_points(6, variant=1)

        self.assertGreater(
            commercial_signatures.sequence_similarity(original, variant),
            commercial_signatures.FULL_SEQUENCE_MATCH_THRESHOLD,
        )

    def test_live_coarse_gate_keeps_one_second_boundary_drift(self):
        original = signature_points(6)
        shifted = signature_points(2, variant=99) + original[:4]

        self.assertGreater(
            commercial_signatures._coarse_center_similarity(original, shifted),
            commercial_signatures.LIVE_COARSE_MATCH_THRESHOLD,
        )

    def test_compact_three_second_signature_is_under_one_kilobyte(self):
        points = signature_points(6)
        payload = commercial_signatures._pack(points)

        self.assertLess(len(payload) + len(commercial_signatures._aggregate_histogram(points)), 1024)

    def test_program_feedback_demotes_a_bad_signature(self):
        points = signature_points(10)
        for event_id in ("break-1", "break-2", "break-3"):
            commercial_signatures.record_episode(
                self.db_path,
                "tvg:nbc.example",
                event_id,
                points,
            )
        match = commercial_signatures.match_live(
            self.db_path, "tvg:nbc.example", points[-10:]
        )

        self.assertEqual(
            commercial_signatures.mark_false_positives(
                self.db_path, match["signature_ids"]
            ),
            len(set(match["signature_ids"])),
        )

        stats = commercial_signatures.library_stats(self.db_path)
        self.assertEqual(stats["classified"], 0)
        self.assertEqual(stats["candidates"], len(set(match["signature_ids"])))

    def test_stale_candidates_expire_before_classified_signatures(self):
        anchor = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        commercial_signatures.record_episode(
            self.db_path,
            "tvg:nbc.example",
            "old-candidate",
            signature_points(6),
            observed_at=anchor - timedelta(days=4),
        )
        commercial_signatures.record_episode(
            self.db_path,
            "tvg:nbc.example",
            "old-classified",
            signature_points(6, variant=20),
            user_confirmed=True,
            observed_at=anchor - timedelta(days=4),
        )

        commercial_signatures.prune(self.db_path, now=anchor)

        stats = commercial_signatures.library_stats(self.db_path)
        self.assertEqual(stats["candidates"], 0)
        self.assertGreaterEqual(stats["classified"], 1)


if __name__ == "__main__":
    unittest.main()
