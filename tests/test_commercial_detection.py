from __future__ import annotations

import io
import unittest
from datetime import timedelta
import tempfile
from pathlib import Path
from unittest.mock import patch

import commercial_detection
import commercial_profiles
from flask import Flask
from api.commercial_detection import register_commercial_detection_routes


class CommercialDetectionTests(unittest.TestCase):
    def tearDown(self):
        commercial_detection.set_manual(False)
        with commercial_detection._LOCK:
            commercial_detection._STATE.update(
                logo_state="idle",
                last_logo_at=None,
            )

    def test_manual_test_start_and_end(self):
        started = commercial_detection.set_manual(True)
        self.assertTrue(started["active"])
        self.assertEqual(started["source"], "manual")
        ended = commercial_detection.set_manual(False)
        self.assertFalse(ended["active"])
        self.assertEqual(ended["source"], "idle")

    def test_elapsed_timer_uses_break_start(self):
        commercial_detection.set_manual(True)
        with patch("commercial_detection._now") as now:
            now.return_value = commercial_detection._STATE["started_at"] + timedelta(seconds=5)
            self.assertEqual(commercial_detection.payload()["elapsed_seconds"], 5)

    def test_logo_state_drives_detection_but_not_manual_override(self):
        detected = commercial_detection.apply_logo_state(True)
        self.assertTrue(detected["active"])
        self.assertEqual(detected["source"], "logo")

        commercial_detection.set_manual(True)
        observed = commercial_detection.apply_logo_state(False)
        self.assertTrue(observed["active"])
        self.assertEqual(observed["source"], "manual")
        self.assertEqual(observed["logo_state"], "program")

    def test_logo_state_is_discarded_when_stream_ends(self):
        commercial_detection.apply_logo_state(True)
        cleared = commercial_detection.clear_logo_state()
        self.assertFalse(cleared["active"])
        self.assertEqual(cleared["source"], "idle")
        self.assertEqual(cleared["logo_state"], "idle")
        self.assertIsNone(cleared["last_logo_at"])

    def test_manual_state_is_discarded_when_last_stream_ends(self):
        commercial_detection.set_manual(True)

        cleared = commercial_detection.clear_logo_state()

        self.assertFalse(cleared["active"])
        self.assertEqual(cleared["source"], "idle")

    def test_ui_is_manual_and_not_added_to_ffmpeg_commands(self):
        root = Path(__file__).resolve().parents[1]
        ui = (root / "static/js/ui_commercial_test.js").read_text(encoding="utf-8")
        sidebar = (root / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        ffmpeg = (root / "media/ffmpeg.py").read_text(encoding="utf-8")
        source = (root / "api/commercial_detection.py").read_text(encoding="utf-8")

        self.assertIn("Start Commercial", sidebar)
        self.assertIn("End Commercial", ui)
        self.assertNotIn("SCTE-35", sidebar)
        self.assertNotIn("SCTE-35", ui)
        self.assertIn("Logo detector idle", sidebar)
        self.assertIn("Learning broadcast logo", ui)
        self.assertIn("This Is Program", sidebar)
        self.assertIn("This Is a Commercial", sidebar)
        self.assertIn('id="uiChannelModelChart"', sidebar)
        self.assertIn('id="uiChannelCutLine"', sidebar)
        self.assertIn('id="uiChannelColorLine"', sidebar)
        self.assertIn('id="uiChannelGraphicLine"', sidebar)
        self.assertIn('id="uiChannelBugLine"', sidebar)
        self.assertIn('id="uiChannelConfidenceLine"', sidebar)
        self.assertIn("Bug confidence", sidebar)
        self.assertIn("Commercial confidence", sidebar)
        self.assertIn("Learned:", ui)
        self.assertIn("Last seen:", ui)
        self.assertIn('uiChannelBugLine: "bug_identity_confidence"', ui)
        self.assertIn('uiChannelConfidenceLine: "commercial_confidence"', ui)
        self.assertIn("CHART_HISTORY_MINUTES = 30", ui)
        self.assertIn("minutes=30", source)
        self.assertIn("renderChannelChart(profile)", ui)
        self.assertIn("/api/commercial-break/feedback", ui)
        self.assertNotIn("commercial-in-progress-preview.gif", sidebar)
        self.assertIn("Preview reconnecting", sidebar)
        self.assertNotIn("commercial_detection", ffmpeg)

    def test_feedback_records_user_labeled_channel_sample(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)
        snapshot = {
            "channel_identity": "tvg:nbc.example",
            "sports_generated": False,
            "features": {"cut_density": 0.4, "color_volatility": 0.7},
            "detector_state": "program",
            "commercial_reason": "",
        }
        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ) as db_path, patch(
            "api.commercial_detection.mpegts.active_stream_profile_snapshot",
            return_value=snapshot,
        ):
            response = app.test_client().post(
                "/api/commercial-break/feedback",
                json={"label": "commercial"},
            )
            profile = commercial_profiles.profile(db_path, "tvg:nbc.example")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(profile["commercial_samples"], 1)
        self.assertEqual(profile["user_confirmed_commercial_samples"], 1)

    def test_feedback_rejects_missing_non_sports_stream(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)
        with patch(
            "api.commercial_detection.mpegts.active_stream_profile_snapshot",
            return_value={},
        ):
            response = app.test_client().post(
                "/api/commercial-break/feedback",
                json={"label": "program"},
            )
        self.assertEqual(response.status_code, 409)

    def test_status_exposes_bounded_channel_history_for_dashboard(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)
        snapshot = {
            "channel_identity": "tvg:nbc.example",
            "sports_generated": False,
            "features": {
                "cut_density": 0.25,
                "color_volatility": 0.5,
                "program_graphics_confidence": 0.75,
            },
        }
        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ) as db_path, patch(
            "api.commercial_detection.mpegts.active_stream_profile_snapshot",
            return_value=snapshot,
        ), patch(
            "api.commercial_detection.mpegts.commercial_status",
            return_value={"eligible_streams": 1, "streams": []},
        ):
            commercial_profiles.record(
                db_path,
                "tvg:nbc.example",
                label="program",
                source="detector",
                features=snapshot["features"],
            )
            response = app.test_client().get("/api/commercial-break")

        self.assertEqual(response.status_code, 200)
        profile = response.get_json()["channel_profile"]
        self.assertEqual(profile["channel_identity"], "tvg:nbc.example")
        self.assertEqual(profile["retention_days"], 14)
        self.assertEqual(profile["program_samples"], 1)
        self.assertEqual(len(profile["history"]), 1)
        self.assertEqual(profile["history"][0]["features"]["cut_density"], 0.25)

    @patch(
        "api.commercial_detection.mpegts.active_stream_profile_snapshot",
        return_value={
            "channel_identity": "tvg:nbc.example",
            "sports_generated": False,
            "features": {},
        },
    )
    def test_feedback_accepts_stream_identity_selector(self, active_snapshot):
        app = Flask(__name__)
        register_commercial_detection_routes(app)

        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ), patch(
            "api.commercial_detection.mpegts.apply_program_feedback",
            return_value=True,
        ) as apply_feedback:
            app.test_client().post(
                "/api/commercial-break/feedback",
                json={"label": "program", "stream_identity": "manual:1"},
            )

        active_snapshot.assert_called_once_with("manual:1")
        apply_feedback.assert_called_once_with("manual:1")

    def test_export_profiles_returns_observations(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)

        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ) as db_path:
            commercial_profiles.record(
                db_path,
                "tvg:nbc.example",
                label="program",
                source="detector",
                features={"cut_density": 0.2, "color_volatility": 0.1},
            )
            commercial_profiles.record(
                db_path,
                "tvg:nbc.example",
                label="commercial",
                source="detector",
                features={"cut_density": 0.9, "color_volatility": 0.8},
            )
            response = app.test_client().get(
                "/api/commercial-break/profiles/export?passphrase=keep-me"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data)
        self.assertIn("commercial-profiles.pickle", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"\xfd7zXZ\x00"))
        with tempfile.TemporaryDirectory() as target_parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(target_parent) / "target.db"
        ):
            result = commercial_profiles.load_profile_blob(
                Path(target_parent) / "target.db",
                response.data,
                passphrase="keep-me",
            )
        self.assertEqual(result["inserted"], 2)

    def test_export_can_filter_by_channel(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)

        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ) as db_path:
            commercial_profiles.record(
                db_path,
                "tvg:nbc.example",
                label="program",
                source="detector",
                features={"cut_density": 0.2, "color_volatility": 0.1},
            )
            commercial_profiles.record(
                db_path,
                "tvg:cnn.example",
                label="commercial",
                source="detector",
                features={"cut_density": 0.9, "color_volatility": 0.8},
            )
            response = app.test_client().get(
                "/api/commercial-break/profiles/export?"
                "channel_identity=tvg:nbc.example&passphrase=keep-me"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("commercial-profiles.pickle", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"\xfd7zXZ\x00"))
        with tempfile.TemporaryDirectory() as target_parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(target_parent) / "target.db"
        ):
            result = commercial_profiles.load_profile_blob(
                Path(target_parent) / "target.db",
                response.data,
                passphrase="keep-me",
            )
        self.assertEqual(result["inserted"], 1)

    def test_import_profiles_replays_observations(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)
        with tempfile.TemporaryDirectory() as source_parent, tempfile.TemporaryDirectory() as target_parent:
            source_db = Path(source_parent) / "source.db"
            commercial_profiles.record(
                source_db,
                "tvg:nbc.example",
                label="program",
                source="detector",
                features={"cut_density": 0.2, "color_volatility": 0.1},
            )
            commercial_profiles.record(
                source_db,
                "tvg:nbc.example",
                label="commercial",
                source="detector",
                features={"cut_density": 0.9, "color_volatility": 0.8},
            )
            blob = commercial_profiles.dump_profile_blob(
                source_db,
                passphrase="keep-me",
            )
            with patch(
                "api.commercial_detection.core.DB_PATH", Path(target_parent) / "profile.db"
            ) as target_db:
                response = app.test_client().post(
                    "/api/commercial-break/profiles/import?passphrase=keep-me",
                    data={
                        "file": (io.BytesIO(blob), "commercial-profiles.pickle"),
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 200)
                loaded = commercial_profiles.profile(target_db, "tvg:nbc.example")
                self.assertTrue(response.get_json()["ok"])
                self.assertEqual(response.get_json()["imported"], 2)
                self.assertEqual(loaded["program_samples"], 1)
                self.assertEqual(loaded["commercial_samples"], 1)

    def test_import_rejects_invalid_payload(self):
        app = Flask(__name__)
        register_commercial_detection_routes(app)

        with tempfile.TemporaryDirectory() as parent, patch(
            "api.commercial_detection.core.DB_PATH", Path(parent) / "profile.db"
        ):
            response = app.test_client().post(
                "/api/commercial-break/profiles/import?passphrase=keep-me",
                data={"passphrase": "keep-me"},
            )

        self.assertEqual(response.status_code, 400)

    def test_profile_blob_wrong_passphrase_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            source_db = Path(parent) / "profile.db"
            commercial_profiles.record(
                source_db,
                "tvg:nbc.example",
                label="program",
                source="detector",
                features={"cut_density": 0.2, "color_volatility": 0.1},
            )
            blob = commercial_profiles.dump_profile_blob(
                source_db,
                passphrase="correct-pass",
            )

        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(ValueError):
                commercial_profiles.load_profile_blob(
                    Path(parent) / "target.db",
                    blob,
                    passphrase="wrong-pass",
                )


if __name__ == "__main__":
    unittest.main()
