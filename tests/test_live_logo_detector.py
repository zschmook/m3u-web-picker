from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

import commercial_profiles
from media.logo_detector import (
    BUG_RETURN_CONFIDENCE,
    BUG_RETURN_CONFIRMATIONS,
    BUG_RELOCATION_SAVE_TICKS,
    COUNTDOWN_THRESHOLD,
    COUNTDOWN_WINDOW_FRAMES,
    LEARNING_FRAMES,
    EVIDENCE_FRAMES,
    MISSING_CONFIRMATIONS,
    RETURN_CONFIRMATIONS,
    STRONG_PROGRAM_RETURN_CONFIRMATIONS,
    LiveLogoDetector,
    _edge_map,
    _countdown_signature,
    _regions,
)


class LiveLogoDetectorTests(unittest.TestCase):
    def test_program_feedback_immediately_releases_commercial_and_holds(self):
        callback = Mock()
        detector = LiveLogoDetector(Path("."), Path("frame-%06d.jpg"), callback)
        detector.last_commercial = True
        detector.state = "commercial"
        detector.overall_commercial_confidence = 1.0

        detector.apply_program_feedback()

        self.assertFalse(detector.last_commercial)
        self.assertEqual(detector.state, "program")
        self.assertEqual(detector.overall_commercial_confidence, 0.0)
        self.assertTrue(detector._manual_program_hold())
        callback.assert_called_once_with(False)

    @staticmethod
    def _countdown_maps(*, counting: bool) -> list[tuple[int, ...]]:
        fixed = {10 * 48 + x for x in range(8, 20)}
        first_digit = {y * 48 + x for y in range(8, 13) for x in (21, 22)}
        second_digit = {y * 48 + x for y in range(8, 13) for x in (24, 25)}
        frames = []
        for index in range(COUNTDOWN_WINDOW_FRAMES):
            pixels = set(fixed)
            if counting:
                pixels.update(first_digit if (index // 2) % 2 == 0 else second_digit)
            frames.append(tuple(1 if pixel in pixels else 0 for pixel in range(48 * 24)))
        return frames

    def test_countdown_signature_requires_periodic_digit_changes(self):
        confidence, reference = _countdown_signature(self._countdown_maps(counting=True))
        self.assertGreaterEqual(confidence, COUNTDOWN_THRESHOLD)
        self.assertIsNotNone(reference)

    def test_static_corner_graphic_is_not_a_countdown(self):
        confidence, _reference = _countdown_signature(self._countdown_maps(counting=False))
        self.assertLess(confidence, COUNTDOWN_THRESHOLD)

    def test_countdown_cannot_latch_during_network_bug_probation(self):
        detector = LiveLogoDetector(Path("."), Path("frame-%06d.jpg"), Mock())
        blank = tuple(0 for _ in range(48 * 24))
        counting = self._countdown_maps(counting=True)

        for frame in counting:
            maps = {name: blank for name in detector._countdown_samples}
            maps["top-right"] = frame
            detector._update_countdown_detector(maps, fallback_allowed=False)

        self.assertFalse(detector.bugless_countdown_mode)
        for frame in counting:
            maps = {name: blank for name in detector._countdown_samples}
            maps["top-right"] = frame
            detector._update_countdown_detector(maps, fallback_allowed=True)

        self.assertTrue(detector.bugless_countdown_mode)
        self.assertEqual(detector.countdown_region, "top-right")

    @staticmethod
    def _frame(
        path: Path,
        *,
        logo: bool,
        scoreboard: bool = False,
        shade: int = 20,
        logo_region: str = "top-right",
        logo_offset: tuple[int, int] = (0, 0),
    ) -> None:
        image = Image.new("RGB", (640, 360), (shade, shade, shade))
        if logo:
            draw = ImageDraw.Draw(image)
            if logo_region == "bottom-left":
                box, line = (39, 294, 134, 339), (54, 329, 119, 304)
            else:
                box, line = (520, 20, 615, 65), (535, 55, 600, 30)
            offset_x, offset_y = logo_offset
            box = tuple(
                value + (offset_x if index % 2 == 0 else offset_y)
                for index, value in enumerate(box)
            )
            line = tuple(
                value + (offset_x if index % 2 == 0 else offset_y)
                for index, value in enumerate(line)
            )
            draw.rectangle(box, fill="white", outline="black", width=5)
            draw.line(line, fill="black", width=5)
        if scoreboard:
            draw = ImageDraw.Draw(image)
            draw.rectangle((210, 285, 430, 345), fill="white", outline="black", width=5)
            draw.line((235, 305, 405, 305), fill="black", width=5)
            draw.line((235, 328, 405, 328), fill="black", width=5)
        image.save(path, "JPEG", quality=90)

    @staticmethod
    def _cut_frame(
        path: Path,
        *,
        index: int,
        scoreboard: bool,
    ) -> None:
        colors = ((205, 35, 45), (20, 80, 210), (225, 185, 25), (35, 175, 85))
        image = Image.new("RGB", (640, 360), colors[index % len(colors)])
        draw = ImageDraw.Draw(image)
        if index % 2:
            draw.ellipse((120, 60, 490, 330), fill=colors[(index + 1) % len(colors)])
        else:
            draw.polygon(((40, 320), (310, 25), (610, 320)), fill=colors[(index + 2) % len(colors)])
        draw.rectangle((520, 20, 615, 65), fill="white", outline="black", width=5)
        draw.line((535, 55, 600, 30), fill="black", width=5)
        if scoreboard:
            draw.rectangle((210, 285, 430, 345), fill="white", outline="black", width=5)
            draw.line((235, 305, 405, 305), fill="black", width=5)
            draw.line((235, 328, 405, 328), fill="black", width=5)
        image.save(path, "JPEG", quality=90)

    @staticmethod
    def _replacement_bug_frame(path: Path, *, shade: int = 70) -> None:
        image = Image.new("RGB", (640, 360), (shade, shade, shade))
        draw = ImageDraw.Draw(image)
        draw.ellipse((65, 18, 125, 72), fill="white", outline="black", width=5)
        draw.line((76, 45, 114, 45), fill="black", width=5)
        draw.line((95, 28, 95, 62), fill="black", width=5)
        image.save(path, "JPEG", quality=90)

    @staticmethod
    def _alternate_program_bug_frame(path: Path, *, shade: int = 70) -> None:
        image = Image.new("RGB", (640, 360), (shade, shade, shade))
        draw = ImageDraw.Draw(image)
        draw.ellipse((505, 285, 585, 345), fill="white", outline="black", width=5)
        draw.line((520, 315, 570, 315), fill="black", width=5)
        draw.line((545, 295, 545, 335), fill="black", width=5)
        image.save(path, "JPEG", quality=90)

    def test_top_right_logo_disappearance_and_return(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"learn-{index}.jpg"
                self._frame(path, logo=True, shade=15 + index)
                detector._process(path)
            self.assertEqual(detector.region, "top-right")
            self.assertEqual(detector.state, "program")

            for index in range(MISSING_CONFIRMATIONS):
                path = directory / f"missing-{index}.jpg"
                self._frame(path, logo=False, shade=70 + index)
                detector._process(path)
            callback.assert_called_once_with(True)
            self.assertEqual(detector.state, "commercial")

            for index in range(BUG_RETURN_CONFIRMATIONS):
                path = directory / f"return-{index}.jpg"
                self._frame(path, logo=True, shade=100 + index)
                detector._process(path)
            self.assertEqual(callback.call_args_list[-1].args, (False,))
            self.assertEqual(detector.state, "program")

    def test_weak_logo_resemblance_does_not_end_commercial_between_ads(self):
        detector = LiveLogoDetector(Path("frames"), Path("frames/frame-%09d.jpg"), Mock())
        reference = tuple([1] * 24 + [0] * (48 * 24 - 24))
        maps = {name: reference for name in ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")}
        detector._references = {"top-right": reference}
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"

        with patch.object(
            detector,
            "_best_bug_match",
            return_value=(BUG_RETURN_CONFIDENCE - 0.05, 0.60, "top-right", "top-right", "session:test", reference),
        ):
            for _ in range(BUG_RETURN_CONFIRMATIONS + 2):
                detector._observe(maps, {}, tuple([0.0] * 64))

        self.assertTrue(detector.last_commercial)
        self.assertEqual(detector.state, "commercial")

    def test_changing_ad_graphics_do_not_accumulate_bug_return_votes(self):
        detector = LiveLogoDetector(Path("frames"), Path("frames/frame-%09d.jpg"), Mock())
        reference = tuple([1] * 24 + [0] * (48 * 24 - 24))
        maps = {
            name: reference
            for name in ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
        }
        detector._references = {"top-right": reference}
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"
        regions = iter(
            ("top-left", "bottom-right")
            * (BUG_RETURN_CONFIRMATIONS + 2)
        )

        def changing_ad_match(_maps):
            region = next(regions)
            return (0.95, 0.95, region, "top-right", "session:test", reference)

        with patch.object(detector, "_best_bug_match", side_effect=changing_ad_match):
            for _ in range(BUG_RETURN_CONFIRMATIONS + 2):
                detector._observe(maps, {}, tuple([0.0] * 64))

        self.assertTrue(detector.last_commercial)
        self.assertEqual(detector.state, "commercial")

    def test_strong_bug_and_learned_program_model_return_quickly(self):
        callback = Mock()
        detector = LiveLogoDetector(Path("frames"), Path("frames/frame-%09d.jpg"), callback)
        reference = tuple([1] * 24 + [0] * (48 * 24 - 24))
        maps = {
            name: reference
            for name in ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
        }
        detector._references = {"top-right": reference}
        detector._channel_profile = {"ready": True}
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"

        with patch.object(
            detector,
            "_best_bug_match",
            return_value=(0.92, 0.90, "top-right", "top-right", "session:test", reference),
        ), patch(
            "media.logo_detector.commercial_profiles.score_features",
            return_value={"ready": True, "score": 0.20},
        ):
            for _ in range(STRONG_PROGRAM_RETURN_CONFIRMATIONS):
                detector._observe(maps, {}, tuple([0.0] * 64))

        callback.assert_called_once_with(False)
        self.assertFalse(detector.last_commercial)
        self.assertEqual(detector.state, "program")

    def test_commercial_side_model_keeps_conservative_bug_return_delay(self):
        callback = Mock()
        detector = LiveLogoDetector(Path("frames"), Path("frames/frame-%09d.jpg"), callback)
        reference = tuple([1] * 24 + [0] * (48 * 24 - 24))
        maps = {
            name: reference
            for name in ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
        }
        detector._references = {"top-right": reference}
        detector._channel_profile = {"ready": True}
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"

        with patch.object(
            detector,
            "_best_bug_match",
            return_value=(0.92, 0.90, "top-right", "top-right", "session:test", reference),
        ), patch(
            "media.logo_detector.commercial_profiles.score_features",
            return_value={"ready": True, "score": 0.80},
        ):
            for _ in range(STRONG_PROGRAM_RETURN_CONFIRMATIONS):
                detector._observe(maps, {}, tuple([0.0] * 64))
            self.assertTrue(detector.last_commercial)
            for _ in range(BUG_RETURN_CONFIRMATIONS - STRONG_PROGRAM_RETURN_CONFIRMATIONS):
                detector._observe(maps, {}, tuple([0.0] * 64))

        callback.assert_called_once_with(False)
        self.assertFalse(detector.last_commercial)

    def test_status_does_not_expose_working_path(self):
        detector = LiveLogoDetector(Path("secret"), Path("secret/frame-%09d.jpg"), Mock())
        self.assertNotIn("secret", str(detector.status()))

    def test_overall_confidence_tracks_final_commercial_state(self):
        detector = LiveLogoDetector(Path("frames"), Path("frames/frame-%09d.jpg"), Mock())
        detector._references = {"top-right": tuple([1] * (48 * 24))}
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"
        maps = {
            name: tuple([0] * (48 * 24))
            for name in ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
        }

        for _ in range(20):
            detector._observe(maps, {}, tuple([0.0] * 64))
        self.assertGreater(detector.overall_commercial_confidence, 0.98)

        detector.last_commercial = False
        detector.state = "program"
        detector._commercial_reason = ""
        program_maps = {name: detector._references["top-right"] for name in maps}
        detector._observe(program_maps, {}, tuple([0.0] * 64))
        self.assertLess(detector.overall_commercial_confidence, 0.40)
        for _ in range(20):
            detector._observe(program_maps, {}, tuple([0.0] * 64))
        self.assertLess(detector.overall_commercial_confidence, 0.02)

    def test_learned_logo_can_move_between_valid_regions(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"learn-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)
            self.assertEqual(detector.region, "top-right")

            moved = directory / "moved.jpg"
            self._frame(moved, logo=True, shade=80, logo_region="bottom-left")
            detector._process(moved)
            self.assertEqual(detector.region, "bottom-left")
            self.assertEqual(detector.state, "program")
            callback.assert_not_called()
            self.assertLess(detector.status()["commercial_confidence"], 65)
            self.assertLess(
                detector.status()["bug_identity_confidence"],
                detector.status()["channel_features"]["program_graphics_confidence"],
            )

    def test_shifted_news_bug_relocation_does_not_trigger_commercial(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            db_path = directory / "profiles.db"
            callback = Mock()
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                callback,
                channel_identity="tvg:news.example",
                profile_db_path=db_path,
            )
            for index in range(LEARNING_FRAMES):
                path = directory / f"learn-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)

            for index in range(BUG_RELOCATION_SAVE_TICKS + 20):
                path = directory / f"weather-layout-{index}.jpg"
                self._frame(
                    path,
                    logo=True,
                    shade=70 + index,
                    logo_region="bottom-left",
                    logo_offset=(9, 6),
                )
                detector._process(path)

            self.assertEqual(detector.state, "program")
            self.assertFalse(detector.last_commercial)
            self.assertNotIn(True, [call.args[0] for call in callback.call_args_list])
            self.assertIn("bottom-left", detector.status()["logo_candidates"])

    def test_channel_can_recognize_multiple_persisted_program_bugs(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            db_path = directory / "profiles.db"
            alternate_path = directory / "alternate-reference.jpg"
            self._alternate_program_bug_frame(alternate_path)
            with Image.open(alternate_path) as image:
                alternate_reference = _edge_map(_regions(image)["bottom-right"])
            commercial_profiles.save_trusted_bug(
                db_path,
                "tvg:fox.example",
                region="bottom-right",
                fingerprint=alternate_reference,
                observed_ticks=120,
            )

            callback = Mock()
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                callback,
                channel_identity="tvg:fox.example",
                profile_db_path=db_path,
            )
            for index in range(8):
                path = directory / f"alternate-{index}.jpg"
                self._alternate_program_bug_frame(path, shade=70 + index)
                detector._process(path)

            self.assertEqual(detector.state, "program")
            self.assertFalse(detector.last_commercial)
            self.assertEqual(detector.region, "bottom-right")
            self.assertGreater(detector.status()["bug_identity_confidence"], 50)
            callback.assert_not_called()

    def test_persisted_bug_reacquires_immediately_in_a_new_region(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            db_path = directory / "profiles.db"
            original = directory / "original.jpg"
            self._frame(original, logo=True, shade=40, logo_region="top-right")
            with Image.open(original) as image:
                reference = _edge_map(_regions(image)["top-right"])
            commercial_profiles.save_trusted_bug(
                db_path,
                "tvg:wgal.example",
                region="top-right",
                fingerprint=reference,
                observed_ticks=120,
            )
            callback = Mock()
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                callback,
                channel_identity="tvg:wgal.example",
                profile_db_path=db_path,
            )

            moved = directory / "bottom-left.jpg"
            self._frame(moved, logo=True, shade=80, logo_region="bottom-left")
            detector._process(moved)

            self.assertEqual(detector.state, "program")
            self.assertFalse(detector.last_commercial)
            self.assertEqual(detector.region, "bottom-left")
            self.assertGreater(detector.status()["bug_identity_confidence"], 50)
            callback.assert_not_called()

    def test_stale_logo_position_decays_out_of_rolling_evidence(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", Mock())
            for index in range(LEARNING_FRAMES):
                path = directory / f"old-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)
            self.assertIn("top-right", detector.status()["logo_candidates"])

            for index in range(EVIDENCE_FRAMES):
                path = directory / f"new-{index}.jpg"
                self._frame(path, logo=True, shade=80 + (index % 20), logo_region="bottom-left")
                detector._process(path)
            self.assertNotIn("top-right", detector.status()["logo_candidates"])
            self.assertIn("bottom-left", detector.status()["logo_candidates"])

    def test_commercial_graphic_is_not_learned_as_broadcast_logo(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", Mock())
            for index in range(LEARNING_FRAMES):
                path = directory / f"program-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)

            for index in range(EVIDENCE_FRAMES):
                path = directory / f"drug-ad-{index}.jpg"
                image = Image.new("RGB", (640, 360), (80 + (index % 20),) * 3)
                draw = ImageDraw.Draw(image)
                draw.rectangle((100, 295, 220, 338), fill="white", outline="black", width=4)
                draw.line((110, 307, 210, 327), fill="black", width=3)
                draw.line((110, 327, 210, 307), fill="black", width=3)
                image.save(path, "JPEG", quality=90)
                detector._process(path)

            self.assertTrue(detector.last_commercial)
            self.assertEqual(detector.status()["logo_candidates"], ["top-right"])

    def test_scoreboard_uses_two_half_second_samples_after_logo(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            detector = LiveLogoDetector(
                directory, directory / "frame-%09d.jpg", Mock(), sports_generated=True
            )
            for index in range(LEARNING_FRAMES):
                path = directory / f"logo-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)
            self.assertTrue(detector.status()["logo_detected"])
            self.assertIsNotNone(detector.status()["logo_detected_at"])
            self.assertIsNotNone(detector.status()["logo_last_seen_at"])
            self.assertFalse(detector.status()["scoreboard_detected"])

            for index in range(2):
                path = directory / f"scoreboard-{index}.jpg"
                self._frame(path, logo=True, scoreboard=True, shade=80 + index)
                detector._process(path)
            self.assertTrue(detector.status()["scoreboard_detected"])
            self.assertIsNotNone(detector.status()["scoreboard_detected_at"])
            self.assertIn(detector.scoreboard_region, {"bottom-left", "bottom-center", "bottom-right"})

    def test_fast_cuts_without_program_layout_detect_local_commercial(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(
                directory, directory / "frame-%09d.jpg", callback, sports_generated=True
            )
            for index in range(LEARNING_FRAMES):
                path = directory / f"logo-{index}.jpg"
                self._frame(path, logo=True, shade=30 + index)
                detector._process(path)
            for index in range(20):
                path = directory / f"program-{index}.jpg"
                self._frame(path, logo=True, scoreboard=True, shade=70 + index)
                detector._process(path)

            for index in range(40):
                path = directory / f"local-ad-{index}.jpg"
                self._cut_frame(path, index=index, scoreboard=False)
                detector._process(path)
                if detector.last_commercial:
                    break

            callback.assert_called_once_with(True)
            self.assertEqual(detector.status()["commercial_reason"], "local-layout")
            self.assertGreater(detector.status()["color_volatility"], 0)

            for index in range(RETURN_CONFIRMATIONS):
                path = directory / f"local-return-{index}.jpg"
                self._frame(path, logo=True, scoreboard=True, shade=110 + index)
                detector._process(path)
            self.assertEqual(callback.call_args_list[-1].args, (False,))
            self.assertEqual(detector.state, "program")

    def test_fast_program_cuts_do_not_trigger_when_scoreboard_remains(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(
                directory, directory / "frame-%09d.jpg", callback, sports_generated=True
            )
            for index in range(LEARNING_FRAMES):
                path = directory / f"logo-{index}.jpg"
                self._frame(path, logo=True, shade=30 + index)
                detector._process(path)
            for index in range(40):
                path = directory / f"program-cut-{index}.jpg"
                self._cut_frame(path, index=index, scoreboard=True)
                detector._process(path)

            callback.assert_not_called()
            self.assertFalse(detector.last_commercial)

    def test_non_sports_stream_uses_color_but_not_scoreboard_for_local_break(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"logo-{index}.jpg"
                self._frame(path, logo=True, shade=30 + index)
                detector._process(path)
            for index in range(LEARNING_FRAMES + 5):
                path = directory / f"ordinary-program-{index}.jpg"
                self._frame(path, logo=True, shade=70)
                detector._process(path)
            for index in range(40):
                path = directory / f"ordinary-cut-{index}.jpg"
                self._cut_frame(path, index=index, scoreboard=False)
                detector._process(path)
                if detector.last_commercial:
                    break

            callback.assert_called_once_with(True)
            self.assertFalse(detector.status()["scoreboard_applicable"])
            self.assertFalse(detector.status()["scoreboard_detected"])
            self.assertEqual(detector.status()["commercial_reason"], "local-color")

    def test_moderate_normal_show_changes_stay_below_local_break_threshold(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"logo-{index}.jpg"
                self._frame(path, logo=True, shade=30 + index)
                detector._process(path)
            for index in range(LEARNING_FRAMES + 5):
                path = directory / f"ordinary-program-{index}.jpg"
                self._frame(path, logo=True, shade=70)
                detector._process(path)
            for index in range(60):
                path = directory / f"credits-{index}.jpg"
                self._cut_frame(path, index=index // 4, scoreboard=False)
                detector._process(path)

            callback.assert_not_called()
            self.assertFalse(detector.last_commercial)

    def test_no_logo_keeps_learning_until_logo_is_seen(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"commercial-{index}.jpg"
                self._frame(path, logo=False, shade=20 + index)
                detector._process(path)
            for index in range(MISSING_CONFIRMATIONS - 1):
                path = directory / f"confirm-commercial-{index}.jpg"
                self._frame(path, logo=False, shade=80 + index)
                detector._process(path)
            self.assertIsNone(detector.last_commercial)
            self.assertEqual(detector.state, "learning")
            callback.assert_not_called()

    def test_stable_replacement_bug_recovers_a_stuck_logo_break(self):
        with tempfile.TemporaryDirectory() as parent, patch(
            "media.logo_detector.RECOVERY_DELAY_FRAMES", 2
        ), patch("media.logo_detector.RECOVERY_STABLE_FRAMES", 4), patch(
            "media.logo_detector.RECOVERY_COLOR_LIMIT", 1.0
        ):
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"initial-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)
            for index in range(MISSING_CONFIRMATIONS):
                path = directory / f"commercial-{index}.jpg"
                self._frame(path, logo=False, shade=90)
                detector._process(path)
            self.assertTrue(detector.last_commercial)

            for index in range(8):
                path = directory / f"replacement-{index}.jpg"
                self._replacement_bug_frame(path, shade=70)
                detector._process(path)
                if detector.last_commercial is False:
                    break

            self.assertEqual([call.args for call in callback.call_args_list], [(True,), (False,)])
            self.assertEqual(detector.state, "program")
            self.assertEqual(detector.status()["recovery_state"], "replacement-accepted")

    def test_normal_stream_fails_open_instead_of_holding_overlay_forever(self):
        with tempfile.TemporaryDirectory() as parent, patch(
            "media.logo_detector.RECOVERY_DELAY_FRAMES", 1000
        ), patch("media.logo_detector.NORMAL_MAXIMUM_HOLD_FRAMES", 3):
            directory = Path(parent)
            callback = Mock()
            detector = LiveLogoDetector(directory, directory / "frame-%09d.jpg", callback)
            for index in range(LEARNING_FRAMES):
                path = directory / f"initial-{index}.jpg"
                self._frame(path, logo=True, shade=20 + index)
                detector._process(path)
            for index in range(MISSING_CONFIRMATIONS + 3):
                path = directory / f"missing-{index}.jpg"
                self._frame(path, logo=False, shade=90)
                detector._process(path)

            self.assertEqual([call.args for call in callback.call_args_list], [(True,), (False,)])
            self.assertFalse(detector.last_commercial)
            self.assertEqual(detector.state, "learning")
            self.assertEqual(detector.status()["recovery_state"], "safety-release")

    def test_non_sports_detector_persists_channel_samples_and_scores_in_shadow_mode(self):
        with tempfile.TemporaryDirectory() as parent, patch(
            "media.logo_detector.PROFILE_SAMPLE_FRAMES", 1
        ):
            directory = Path(parent)
            db_path = directory / "profiles.db"
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                Mock(),
                channel_identity="tvg:nbc.example",
                profile_db_path=db_path,
            )
            for index in range(LEARNING_FRAMES + 35):
                path = directory / f"program-{index}.jpg"
                self._frame(path, logo=True, shade=40 + (index % 10))
                detector._process(path)
            for index in range(MISSING_CONFIRMATIONS + 12):
                path = directory / f"commercial-{index}.jpg"
                self._frame(path, logo=False, shade=130 + (index % 10))
                detector._process(path)

            profile = commercial_profiles.profile(db_path, "tvg:nbc.example")
            self.assertGreaterEqual(profile["program_samples"], 30)
            self.assertGreaterEqual(profile["commercial_samples"], 3)
            self.assertTrue(detector.status()["channel_model_ready"])
            self.assertIn("cut_density", detector.status()["channel_features"])

    def test_non_sports_channel_transition_records_state_snapshot(self):
        with tempfile.TemporaryDirectory() as parent, patch(
            "media.logo_detector.PROFILE_SAMPLE_FRAMES", 1000
        ), patch("media.logo_detector.commercial_profiles.record") as record:
            directory = Path(parent)
            db_path = directory / "profiles.db"
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                Mock(),
                channel_identity="tvg:nbc.example",
                profile_db_path=db_path,
            )
            for index in range(LEARNING_FRAMES):
                path = directory / f"program-{index}.jpg"
                self._frame(path, logo=True, shade=30 + (index % 5))
                detector._process(path)

            for index in range(MISSING_CONFIRMATIONS):
                path = directory / f"commercial-{index}.jpg"
                self._frame(path, logo=False, shade=90 + index)
                detector._process(path)

            for index in range(BUG_RETURN_CONFIRMATIONS):
                path = directory / f"return-{index}.jpg"
                self._frame(path, logo=True, shade=100 + index)
                detector._process(path)

            transition_calls = [
                call for call in record.call_args_list
                if call.kwargs.get("source") == "state-transition"
            ]
            self.assertEqual(len(transition_calls), 2)
            self.assertEqual(transition_calls[0].kwargs["label"], "commercial")
            self.assertEqual(transition_calls[1].kwargs["label"], "program")
            for call in transition_calls:
                self.assertEqual(call.kwargs["source"], "state-transition")

    def test_sports_detector_does_not_write_non_sports_channel_profile(self):
        with tempfile.TemporaryDirectory() as parent, patch(
            "media.logo_detector.PROFILE_SAMPLE_FRAMES", 1
        ):
            directory = Path(parent)
            db_path = directory / "profiles.db"
            detector = LiveLogoDetector(
                directory,
                directory / "frame-%09d.jpg",
                Mock(),
                sports_generated=True,
                channel_identity="tvg:sports.example",
                profile_db_path=db_path,
            )
            for index in range(LEARNING_FRAMES + 5):
                path = directory / f"sports-{index}.jpg"
                self._frame(path, logo=True, scoreboard=True, shade=40 + index)
                detector._process(path)

            self.assertFalse(db_path.exists())
            self.assertFalse(detector.status()["channel_model_ready"])

    def test_epg_program_boundary_suppresses_local_color_and_releases_overlay(self):
        callback = Mock()
        detector = LiveLogoDetector(
            Path("frames"),
            Path("frames/frame-%09d.jpg"),
            callback,
            channel_identity="tvg:nbc.example",
            epg_path=Path("epg.xml"),
        )
        detector._current_program_key = "old-start|old-stop|Chicago Fire"
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "local-color"
        detector._program_color_changes.extend([0.1, 0.2])
        detector._color_changes.extend([0.3, 0.4])
        with patch(
            "guide_epg.programme_window",
            return_value={
                "current": {
                    "start": "new-start",
                    "stop": "new-stop",
                    "title": "Next Show",
                }
            },
        ):
            detector._refresh_programme_boundary()

        callback.assert_called_once_with(False)
        self.assertEqual(detector.state, "program")
        self.assertTrue(detector.status()["program_boundary_suppressed"])
        self.assertEqual(detector._program_color_changes, [])
        self.assertEqual(detector._color_changes, [])

    def test_epg_program_boundary_gives_replacement_bug_a_short_grace_window(self):
        callback = Mock()
        detector = LiveLogoDetector(
            Path("frames"),
            Path("frames/frame-%09d.jpg"),
            callback,
            channel_identity="tvg:fox.example",
            epg_path=Path("epg.xml"),
        )
        detector._current_program_key = "old|stop|FOX NFL"
        detector.last_commercial = True
        detector.state = "commercial"
        detector._commercial_reason = "logo-missing"
        with patch(
            "guide_epg.programme_window",
            return_value={
                "current": {
                    "start": "new",
                    "stop": "later",
                    "title": "Local FOX News",
                }
            },
        ):
            detector._refresh_programme_boundary()

        callback.assert_called_once_with(False)
        self.assertFalse(detector.last_commercial)
        self.assertTrue(detector.status()["bug_transition_grace"])
        self.assertEqual(detector.status()["recovery_state"], "program-transition-grace")


if __name__ == "__main__":
    unittest.main()
