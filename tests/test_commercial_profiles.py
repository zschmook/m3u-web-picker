from __future__ import annotations

import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import commercial_profiles


class CommercialProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "profiles.db"
        commercial_profiles._LAST_PRUNE.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_user_program_sample_has_strong_statistical_weight(self):
        commercial_profiles.record(
            self.db_path,
            "tvg:weighted.example",
            label="program",
            source="detector",
            features={"cut_density": 0.0},
        )
        commercial_profiles.record(
            self.db_path,
            "tvg:weighted.example",
            label="program",
            source="user",
            features={"cut_density": 1.0},
        )

        profile = commercial_profiles.profile(self.db_path, "tvg:weighted.example")

        self.assertEqual(profile["program_samples"], 2)
        self.assertEqual(
            profile["effective_program_samples"],
            commercial_profiles.USER_SAMPLE_WEIGHT + 1,
        )
        self.assertGreater(profile["program"]["cut_density"]["mean"], 0.90)

    def test_records_channel_specific_program_and_commercial_baselines(self):
        for index in range(30):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="program",
                features={"cut_density": 0.10, "color_volatility": 0.20},
                observed_at=datetime(2026, 8, 20, 12, 0, index, tzinfo=timezone.utc),
            )
        for index in range(3):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="commercial",
                source="user",
                features={"cut_density": 0.55, "color_volatility": 0.80},
                observed_at=datetime(2026, 8, 20, 12, 1, index, tzinfo=timezone.utc),
            )

        profile = commercial_profiles.profile(self.db_path, "tvg:nbc.example")

        self.assertTrue(profile["ready"])
        self.assertEqual(profile["program_samples"], 30)
        self.assertEqual(profile["commercial_samples"], 3)
        self.assertEqual(profile["user_confirmed_commercial_samples"], 3)
        self.assertAlmostEqual(profile["program"]["cut_density"]["mean"], 0.10)
        self.assertAlmostEqual(profile["commercial"]["color_volatility"]["mean"], 0.80)

    def test_prunes_raw_observations_older_than_two_weeks(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        with patch("commercial_profiles.maybe_prune", return_value=0):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="program",
                features={},
                observed_at=now - timedelta(days=15),
            )
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="commercial",
                features={},
                observed_at=now - timedelta(days=13),
            )

        self.assertEqual(commercial_profiles.prune(self.db_path, now=now), 1)
        profile = commercial_profiles.profile(self.db_path, "tvg:nbc.example")
        self.assertEqual(profile["program_samples"], 0)
        self.assertEqual(profile["commercial_samples"], 1)

    def test_invalid_identity_is_not_recorded(self):
        self.assertFalse(
            commercial_profiles.record(self.db_path, "", label="program", features={})
        )

    def test_channel_reliability_weights_separating_features_more_heavily(self):
        for index in range(30):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="program",
                features={
                    "cut_density": 0.10,
                    "color_volatility": 0.20,
                    "mean_brightness": 0.50 + ((index % 2) * 0.01),
                },
            )
        for index in range(3):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="commercial",
                features={
                    "cut_density": 0.60,
                    "color_volatility": 0.80,
                    "mean_brightness": 0.51,
                },
            )
        profile = commercial_profiles.profile(self.db_path, "tvg:nbc.example")

        result = commercial_profiles.score_features(
            profile,
            {"cut_density": 0.58, "color_volatility": 0.77, "mean_brightness": 0.50},
        )

        self.assertTrue(result["ready"])
        self.assertGreater(result["score"], 0.70)
        self.assertGreater(result["weights"]["cut_density"], result["weights"]["mean_brightness"])

    def test_prune_compacts_overfull_channels(self):
        with patch("commercial_profiles.MAX_OBSERVATIONS_PER_CHANNEL", 8):
            for index in range(20):
                commercial_profiles.record(
                    self.db_path,
                    "tvg:nbc.example",
                    label="program",
                    features={"cut_density": 0.2},
                    observed_at=datetime(2026, 8, 24, 12, 0, index, tzinfo=timezone.utc),
                )
            removed = commercial_profiles.prune(self.db_path, now=datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc))

        with sqlite3.connect(self.db_path) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM commercial_channel_observations"
            ).fetchone()[0]

        # The write path now enforces the cap continuously, so the explicit
        # prune pass has nothing left to remove.
        self.assertEqual(removed, 0)
        self.assertEqual(remaining, 8)

    def test_recent_returns_bounded_chronological_graph_points(self):
        anchor = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        with patch("commercial_profiles.maybe_prune", return_value=0):
            for index in range(5):
                commercial_profiles.record(
                    self.db_path,
                    "tvg:nbc.example",
                    label="commercial" if index == 4 else "program",
                    features={"cut_density": index / 10, "color_volatility": index / 5},
                    observed_at=anchor + timedelta(seconds=index * 10),
                )

        points = commercial_profiles.recent(self.db_path, "tvg:nbc.example", limit=3)

        self.assertEqual(len(points), 3)
        self.assertLess(points[0]["observed_at"], points[-1]["observed_at"])
        self.assertEqual(points[-1]["label"], "commercial")
        self.assertAlmostEqual(points[-1]["features"]["color_volatility"], 0.8)

    def test_recent_can_return_a_time_bounded_graph_window(self):
        now = datetime.now(timezone.utc)
        with patch("commercial_profiles.maybe_prune", return_value=0):
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="program",
                features={"cut_density": 0.1},
                observed_at=now - timedelta(minutes=31),
            )
            commercial_profiles.record(
                self.db_path,
                "tvg:nbc.example",
                label="commercial",
                features={"cut_density": 0.8},
                observed_at=now - timedelta(minutes=29),
            )

        points = commercial_profiles.recent(
            self.db_path,
            "tvg:nbc.example",
            limit=288,
            minutes=30,
        )

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["label"], "commercial")

    def test_trusted_bug_profiles_are_channel_specific_and_merge_repeat_sightings(self):
        fingerprint = tuple([1] * 24 + [0] * 24)
        near_match = tuple([1] * 22 + [0] * 26)

        self.assertTrue(
            commercial_profiles.save_trusted_bug(
                self.db_path,
                "tvg:fox.example",
                region="top-right",
                fingerprint=fingerprint,
                observed_ticks=60,
            )
        )
        self.assertTrue(
            commercial_profiles.save_trusted_bug(
                self.db_path,
                "tvg:fox.example",
                region="top-right",
                fingerprint=near_match,
                observed_ticks=12,
            )
        )

        fox_bugs = commercial_profiles.trusted_bugs(self.db_path, "tvg:fox.example")
        other_bugs = commercial_profiles.trusted_bugs(self.db_path, "tvg:other.example")

        self.assertEqual(len(fox_bugs), 1)
        self.assertEqual(fox_bugs[0]["region"], "top-right")
        self.assertEqual(fox_bugs[0]["observed_ticks"], 72)
        self.assertEqual(other_bugs, [])

    def test_trusted_bug_identity_merges_across_positions(self):
        fingerprint = tuple([0] * 80 + [1] * 24 + [0] * (48 * 24 - 104))

        commercial_profiles.save_trusted_bug(
            self.db_path,
            "tvg:wgal.example",
            region="top-right",
            fingerprint=fingerprint,
            observed_ticks=60,
        )
        commercial_profiles.save_trusted_bug(
            self.db_path,
            "tvg:wgal.example",
            region="bottom-left",
            fingerprint=fingerprint,
            observed_ticks=12,
        )

        bugs = commercial_profiles.trusted_bugs(self.db_path, "tvg:wgal.example")
        self.assertEqual(len(bugs), 1)
        self.assertEqual(bugs[0]["observed_ticks"], 72)
        self.assertEqual(set(bugs[0]["regions"]), {"top-right", "bottom-left"})

    def test_clear_learning_data_preserves_unrelated_settings(self):
        commercial_profiles.save_trusted_bug(
            self.db_path,
            "tvg:wgal.example",
            region="bottom-left",
            fingerprint=tuple([1] * 24 + [0] * (48 * 24 - 24)),
            observed_ticks=60,
        )
        commercial_profiles.record(
            self.db_path,
            "tvg:wgal.example",
            label="program",
            features={},
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE app_settings (name TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO app_settings VALUES ('port', '9999')")
            conn.commit()

        removed = commercial_profiles.clear_learning_data(self.db_path)

        self.assertEqual(removed["observations"], 1)
        self.assertEqual(removed["bugs"], 1)
        self.assertEqual(commercial_profiles.trusted_bugs(self.db_path, "tvg:wgal.example"), [])
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT value FROM app_settings WHERE name = 'port'").fetchone()[0],
                "9999",
            )

    def test_recent_includes_bug_identity_confidence_for_red_graph_line(self):
        commercial_profiles.record(
            self.db_path,
            "tvg:fox.example",
            label="program",
            features={"bug_identity_confidence": 0.82},
        )

        point = commercial_profiles.recent(self.db_path, "tvg:fox.example", limit=1)[0]

        self.assertAlmostEqual(point["features"]["bug_identity_confidence"], 0.82)

    def test_recent_includes_overall_commercial_confidence_for_white_graph_line(self):
        commercial_profiles.record(
            self.db_path,
            "tvg:fox.example",
            label="commercial",
            features={"commercial_confidence": 0.76},
        )

        point = commercial_profiles.recent(self.db_path, "tvg:fox.example", limit=1)[0]

        self.assertAlmostEqual(point["features"]["commercial_confidence"], 0.76)

    def test_short_event_can_be_reclassified_as_program_feedback(self):
        commercial_profiles.record(
            self.db_path,
            "tvg:nbc.example",
            label="commercial",
            source="state-transition",
            event_id="logo-short-1",
            features={"color_volatility": 0.9},
        )

        changed = commercial_profiles.relabel_event_as_false_positive(
            self.db_path,
            "tvg:nbc.example",
            "logo-short-1",
        )

        self.assertEqual(changed, 1)
        profile = commercial_profiles.profile(self.db_path, "tvg:nbc.example")
        self.assertEqual(profile["commercial_samples"], 0)
        self.assertEqual(profile["program_samples"], 1)


if __name__ == "__main__":
    unittest.main()
