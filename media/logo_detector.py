from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageFilter

import commercial_profiles


POLL_SECONDS = 0.5
LEARNING_FRAMES = 30
EVIDENCE_FRAMES = 90
EVIDENCE_HALF_LIFE_SECONDS = 10.0
COLOR_WINDOW_SECONDS = 10.0
COLOR_WINDOW_FRAMES = int(COLOR_WINDOW_SECONDS / POLL_SECONDS)
COLOR_MIN_SAMPLES = 8
COLOR_CUT_FLOOR = 0.24
PROGRAM_COLOR_BASELINE_FRAMES = 400
MISSING_CONFIRMATIONS = 6
RETURN_CONFIRMATIONS = 5
LOCAL_LAYOUT_CONFIRMATIONS = 12
LOCAL_BREAK_CONFIRMATIONS = 8
NORMAL_LOCAL_BREAK_CONFIRMATIONS = 12
RECOVERY_DELAY_SECONDS = 90.0
RECOVERY_DELAY_FRAMES = int(RECOVERY_DELAY_SECONDS / POLL_SECONDS)
RECOVERY_STABLE_SECONDS = 20.0
RECOVERY_STABLE_FRAMES = int(RECOVERY_STABLE_SECONDS / POLL_SECONDS)
RECOVERY_COLOR_LIMIT = 0.35
NORMAL_MAXIMUM_HOLD_SECONDS = 240.0
NORMAL_MAXIMUM_HOLD_FRAMES = int(NORMAL_MAXIMUM_HOLD_SECONDS / POLL_SECONDS)
PROFILE_SAMPLE_SECONDS = 10.0
PROFILE_SAMPLE_FRAMES = int(PROFILE_SAMPLE_SECONDS / POLL_SECONDS)
PROFILE_REFRESH_SAMPLES = 6
SHORT_FALSE_POSITIVE_SECONDS = 10.0
FALSE_POSITIVE_BUFFER_SECONDS = 20.0
FALSE_POSITIVE_SAMPLE_FRAMES = 4
MODEL_STRONG_PROGRAM_SCORE = 0.35
MODEL_LOCAL_COMMERCIAL_SCORE = 0.55
OVERALL_CONFIDENCE_RISE_ALPHA = 0.20
OVERALL_CONFIDENCE_FALL_ALPHA = 0.65
LOGO_ABSENCE_BOOST = 1.25
PROGRAM_BOUNDARY_SUPPRESSION_SECONDS = 120
BUG_DURATION_FULL_TRUST_TICKS = 12
BUG_PROMOTION_TICKS = 60
BUG_RELOCATION_WINDOW_TICKS = 4
BUG_RELOCATION_SAVE_TICKS = 12
BUG_RELOCATION_VISUAL_THRESHOLD = 0.70
BUG_RELOCATION_SHIFT_X = 4
BUG_RELOCATION_SHIFT_Y = 3
BUG_TRANSITION_GRACE_SECONDS = 20
EDGE_THRESHOLD = 35
COMMERCIAL_THRESHOLD = 0.65
BUG_RETURN_CONFIDENCE = 0.78
BUG_RETURN_CONFIRMATIONS = 8
STRONG_PROGRAM_RETURN_CONFIRMATIONS = 1
STRONG_PROGRAM_GRAPHIC_CONFIDENCE = 0.78
STRONG_PROGRAM_MODEL_MAX = 0.35
MANUAL_PROGRAM_HOLD_SECONDS = 30
MIN_PERSISTENT_EDGES = 16
COUNTDOWN_WINDOW_FRAMES = 12
COUNTDOWN_FALLBACK_PROBATION_SECONDS = 60.0
COUNTDOWN_FALLBACK_PROBATION_FRAMES = int(
    COUNTDOWN_FALLBACK_PROBATION_SECONDS / POLL_SECONDS
)
COUNTDOWN_CONFIRMATIONS = 2
COUNTDOWN_RELEASE_CONFIRMATIONS = 2
COUNTDOWN_THRESHOLD = 0.68
COUNTDOWN_MIN_PERSISTENT_EDGES = 6
REGION_NAMES = ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
COUNTDOWN_REGION_NAMES = ("top-left", "top-right", "bottom-left", "bottom-right")
SCOREBOARD_NAMES = (
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _regions(image: Image.Image) -> dict[str, Image.Image]:
    width, height = image.size
    rw, rh = max(1, int(width * 0.22)), max(1, int(height * 0.20))
    margin_x, margin_y = int(width * 0.015), int(height * 0.02)
    return {
        "top-left": image.crop((margin_x, margin_y, margin_x + rw, margin_y + rh)),
        "top-center": image.crop(((width - rw) // 2, margin_y, (width + rw) // 2, margin_y + rh)),
        "top-right": image.crop((width - margin_x - rw, margin_y, width - margin_x, margin_y + rh)),
        "bottom-left": image.crop((margin_x, height - margin_y - rh, margin_x + rw, height - margin_y)),
        "bottom-right": image.crop((width - margin_x - rw, height - margin_y - rh, width - margin_x, height - margin_y)),
    }


def _edge_map(image: Image.Image) -> tuple[int, ...]:
    edges = image.convert("L").resize((48, 24)).filter(ImageFilter.FIND_EDGES)
    values = list(edges.getdata())
    # FIND_EDGES treats the crop boundary as an edge. Exclude that artificial
    # frame so only graphics inside the sampled region can become the logo.
    return tuple(
        1 if 2 <= (index % 48) < 46 and 2 <= (index // 48) < 22 and value >= EDGE_THRESHOLD else 0
        for index, value in enumerate(values)
    )


def _scoreboard_regions(image: Image.Image) -> dict[str, Image.Image]:
    width, height = image.size
    boxes = {
        "top-left": (0.02, 0.03, 0.45, 0.32),
        "top-center": (0.25, 0.02, 0.75, 0.30),
        "top-right": (0.55, 0.03, 0.98, 0.32),
        "bottom-left": (0.02, 0.68, 0.45, 0.98),
        "bottom-center": (0.22, 0.68, 0.78, 0.98),
        "bottom-right": (0.55, 0.68, 0.98, 0.98),
    }
    return {
        name: image.crop(tuple(int(value * (width if index % 2 == 0 else height)) for index, value in enumerate(box)))
        for name, box in boxes.items()
    }


def _gaussian_structure(image: Image.Image) -> tuple[int, ...]:
    gray = image.convert("L").resize((64, 32))
    fine = gray.filter(ImageFilter.GaussianBlur(0.8))
    broad = gray.filter(ImageFilter.GaussianBlur(3.0))
    difference = ImageChops.difference(fine, broad)
    return tuple(1 if value >= 16 else 0 for value in difference.getdata())


def _color_features(image: Image.Image) -> tuple[tuple[float, ...], float, float]:
    """Return compact HSV distribution plus normalized saturation/brightness."""
    hsv = image.convert("HSV").resize((64, 36))
    bins = [0] * 64
    saturation_total = 0
    brightness_total = 0
    pixels = list(hsv.getdata())
    for hue, saturation, value in pixels:
        index = (hue // 32) * 8 + (saturation // 64) * 2 + (value // 128)
        bins[index] += 1
        saturation_total += saturation
        brightness_total += value
    total = float(sum(bins) or 1)
    histogram = tuple(count / total for count in bins)
    return (
        histogram,
        saturation_total / (total * 255.0),
        brightness_total / (total * 255.0),
    )


def _color_histogram(image: Image.Image) -> tuple[float, ...]:
    return _color_features(image)[0]


def _distribution_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    # Total variation distance: 0 is identical, 1 has no shared distribution.
    return 0.5 * sum(abs(a - b) for a, b in zip(left, right))


def _expand_mask(mask: tuple[int, ...], *, width: int = 48, radius: int = 2) -> tuple[int, ...]:
    """Expand sparse overlay edges so nearby changing digits remain in the mask."""
    height = len(mask) // width
    expanded = [0] * len(mask)
    for index, value in enumerate(mask):
        if not value:
            continue
        x, y = index % width, index // width
        for yy in range(max(0, y - radius), min(height, y + radius + 1)):
            for xx in range(max(0, x - radius), min(width, x + radius + 1)):
                expanded[yy * width + xx] = 1
    return tuple(expanded)


def _countdown_signature(
    history: list[tuple[int, ...]],
) -> tuple[float, tuple[int, ...] | None]:
    """Score a persistent corner graphic whose small details change near 1 Hz.

    The persistent portion identifies the clock overlay while the alternating
    change pulses distinguish counting digits from a static network bug. Full-
    motion ad imagery mostly falls outside the persistent overlay mask.
    """
    if len(history) < COUNTDOWN_WINDOW_FRAMES:
        return 0.0, None
    frames = history[-COUNTDOWN_WINDOW_FRAMES:]
    sample_count = len(frames)
    persistent = tuple(
        1 if sum(frame[index] for frame in frames) >= int(sample_count * 0.67) else 0
        for index in range(len(frames[0]))
    )
    persistent_count = sum(persistent)
    if persistent_count < COUNTDOWN_MIN_PERSISTENT_EDGES:
        return 0.0, None
    neighborhood = _expand_mask(persistent)
    pulses = [
        sum(
            1
            for left, right, nearby in zip(previous, current, neighborhood)
            if nearby and left != right
        )
        for previous, current in zip(frames, frames[1:])
    ]
    even = sum(pulses[::2]) / max(1, len(pulses[::2]))
    odd = sum(pulses[1::2]) / max(1, len(pulses[1::2]))
    high, low = max(even, odd), min(even, odd)
    periodicity = max(0.0, min(1.0, (high - low) / max(2.0, high)))
    pulse_strength = max(0.0, min(1.0, high / 10.0))
    structure = max(0.0, min(1.0, persistent_count / 24.0))
    # Periodicity is deliberately dominant: a static logo may have excellent
    # structure, but it must never look like a counting clock.
    confidence = 0.20 * structure + 0.55 * periodicity + 0.25 * pulse_strength
    if pulse_strength < 0.25 or periodicity < 0.40:
        confidence *= 0.50
    return max(0.0, min(1.0, confidence)), persistent


@dataclass
class LiveLogoDetector:
    directory: Path
    frame_pattern: Path
    callback: Callable[[bool], None]
    sports_generated: bool = False
    channel_identity: str = ""
    profile_db_path: Path | None = None
    epg_path: Path | None = None
    timezone_name: str = "America/New_York"
    state: str = "learning"
    region: str = ""
    scoreboard_region: str = ""
    logo_detected_at: str | None = None
    logo_last_seen_at: str | None = None
    scoreboard_detected_at: str | None = None
    last_commercial: bool | None = None
    last_decision_at: str | None = None
    error: str = ""
    commercial_confidence: float = 0.0
    overall_commercial_confidence: float = 0.0
    primary_confidence: float = 0.0
    local_break_confidence: float = 0.0
    color_volatility: float = 0.0
    scoreboard_confidence: float = 0.0
    countdown_confidence: float = 0.0
    countdown_region: str = ""
    countdown_detected_at: str | None = None
    bugless_countdown_mode: bool = False
    cut_density: float = 0.0
    mean_color_change: float = 0.0
    edge_density: float = 0.0
    mean_brightness: float = 0.0
    mean_saturation: float = 0.0
    logo_match_confidence: float = 0.0
    bug_identity_confidence: float = 0.0
    channel_model_score: float = 0.0
    channel_model_ready: bool = False
    _samples: dict[str, list[tuple[int, ...]]] = field(
        default_factory=lambda: {name: [] for name in REGION_NAMES}
    )
    _references: dict[str, tuple[int, ...]] = field(default_factory=dict)
    _trusted_bugs: list[dict] = field(default_factory=list)
    _trusted_bugs_loaded: bool = False
    _active_bug_key: str = ""
    _active_bug_ticks: int = 0
    _promotion_bug_key: str = ""
    _promotion_bug_ticks: int = 0
    _relocation_samples: dict[str, list[tuple[int, ...]]] = field(
        default_factory=lambda: {name: [] for name in REGION_NAMES}
    )
    _relocation_key: str = ""
    _relocation_region: str = ""
    _relocation_ticks: int = 0
    _relocation_visual_confidence: float = 0.0
    _relocation_detected_at: str | None = None
    _scoreboard_pair: list[dict[str, tuple[int, ...]]] = field(default_factory=list)
    _scoreboard_reference: tuple[int, ...] | None = None
    _countdown_samples: dict[str, list[tuple[int, ...]]] = field(
        default_factory=lambda: {name: [] for name in COUNTDOWN_REGION_NAMES}
    )
    _countdown_reference: tuple[int, ...] | None = None
    _countdown_candidate_count: int = 0
    _countdown_missing_count: int = 0
    _frames_observed: int = 0
    _missing_count: int = 0
    _present_count: int = 0
    _return_candidate_key: str = ""
    _secondary_missing_count: int = 0
    _local_candidate_count: int = 0
    _previous_color_histogram: tuple[float, ...] | None = None
    _color_changes: list[float] = field(default_factory=list)
    _program_color_changes: list[float] = field(default_factory=list)
    _commercial_reason: str = ""
    _commercial_frame_count: int = 0
    _commercial_event_id: str = ""
    _commercial_started_monotonic: float | None = None
    _commercial_episode_frame_count: int = 0
    _commercial_episode_features: list[dict[str, float]] = field(default_factory=list)
    _commercial_episode_feedback_expired: bool = False
    _recovery_samples: dict[str, list[tuple[int, ...]]] = field(
        default_factory=lambda: {name: [] for name in REGION_NAMES}
    )
    recovery_state: str = "idle"
    _profile_sample_frames: int = 0
    _profile_samples_since_refresh: int = 0
    _channel_profile: dict = field(default_factory=dict)
    _current_program_key: str = ""
    _boundary_suppressed_until: datetime | None = None
    _bug_transition_grace_until: datetime | None = None
    _manual_program_until: datetime | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    @classmethod
    def create(
        cls,
        callback: Callable[[bool], None],
        *,
        sports_generated: bool = False,
        channel_identity: str = "",
        profile_db_path: Path | None = None,
        epg_path: Path | None = None,
        timezone_name: str = "America/New_York",
    ) -> "LiveLogoDetector":
        root = str(os.environ.get("M3U_LOGO_DETECTOR_WORK_DIR", "") or "").strip() or None
        directory = Path(tempfile.mkdtemp(prefix="m3u-logo-detector-", dir=root))
        return cls(
            directory,
            directory / "frame-%09d.jpg",
            callback,
            sports_generated=sports_generated,
            channel_identity=commercial_profiles.normalize_identity(channel_identity),
            profile_db_path=profile_db_path,
            epg_path=epg_path,
            timezone_name=str(timezone_name or "America/New_York"),
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _refresh_references(self, maps: dict[str, tuple[int, ...]]) -> bool:
        for name, edge_map in maps.items():
            self._samples[name].append(edge_map)
            del self._samples[name][:-EVIDENCE_FRAMES]
        if len(self._samples[REGION_NAMES[0]]) < LEARNING_FRAMES:
            return False
        candidates: list[tuple[int, str, tuple[int, ...]]] = []
        for name, samples in self._samples.items():
            weights = [
                0.5 ** (((len(samples) - 1 - index) * POLL_SECONDS) / EVIDENCE_HALF_LIFE_SECONDS)
                for index in range(len(samples))
            ]
            required = sum(weights) * 0.62
            persistent = tuple(
                1 if sum(
                    weight for weight, sample in zip(weights, samples) if sample[index]
                ) >= required else 0
                for index in range(len(samples[0]))
            )
            candidates.append((sum(persistent), name, persistent))
        credible = {
            name: persistent
            for score, name, persistent in candidates
            if score >= MIN_PERSISTENT_EDGES
        }
        _, name, _ = max(candidates)
        previously_detected = bool(self._references)
        self._references = credible
        if credible:
            if not previously_detected:
                self.region = name
                self.logo_detected_at = _timestamp()
                if self.last_commercial is None:
                    self.last_commercial = False
                    self.state = "program"
                    self.last_decision_at = _timestamp()
        return True

    @staticmethod
    def _match_score(reference: tuple[int, ...] | None, edge_map: tuple[int, ...]) -> float:
        reference = reference or ()
        reference_count = sum(reference)
        return (
            sum(1 for expected, actual in zip(reference, edge_map) if expected and actual)
            / reference_count
            if reference_count else 0.0
        )

    def _load_trusted_bugs(self) -> None:
        if self._trusted_bugs_loaded:
            return
        self._trusted_bugs_loaded = True
        if self.sports_generated or not self.channel_identity or self.profile_db_path is None:
            return
        try:
            self._trusted_bugs = commercial_profiles.trusted_bugs(
                self.profile_db_path,
                self.channel_identity,
            )
        except (OSError, sqlite3.Error):
            self._trusted_bugs = []

    def _remember_trusted_bug(
        self,
        region: str,
        reference: tuple[int, ...],
        *,
        observed_ticks: int,
    ) -> None:
        if self.sports_generated or not region or sum(reference) < MIN_PERSISTENT_EDGES:
            return
        matched_existing = False
        for bug in self._trusted_bugs:
            known = tuple(bug.get("fingerprint") or ())
            if min(
                self._translated_match_score(known, reference),
                self._translated_match_score(reference, known),
            ) >= commercial_profiles.TRUSTED_BUG_MATCH_THRESHOLD:
                bug["observed_ticks"] = int(bug.get("observed_ticks") or 0) + observed_ticks
                regions = list(bug.get("regions") or [bug.get("region")])
                if region not in regions:
                    regions.append(region)
                bug["regions"] = [value for value in regions if value]
                matched_existing = True
                break
        if not matched_existing:
            bug = {
                "id": 0,
                "region": region,
                "regions": [region],
                "fingerprint": reference,
                "observed_ticks": observed_ticks,
            }
            self._trusted_bugs.append(bug)
        if not self.channel_identity or self.profile_db_path is None:
            return
        try:
            commercial_profiles.save_trusted_bug(
                self.profile_db_path,
                self.channel_identity,
                region=region,
                fingerprint=reference,
                observed_ticks=observed_ticks,
            )
        except (OSError, sqlite3.Error):
            return

    def _reference_entries(self) -> list[tuple[str, str, tuple[int, ...]]]:
        entries: list[tuple[str, str, tuple[int, ...]]] = []
        for index, bug in enumerate(self._trusted_bugs):
            reference = tuple(bug.get("fingerprint") or ())
            regions = list(bug.get("regions") or [bug.get("region")])
            for region in regions:
                region = str(region or "")
                if reference and region:
                    entries.append((f"trusted:{bug.get('id', index)}:{index}", region, reference))
        for region, reference in self._references.items():
            entries.append((f"session:{region}:{hash(reference)}", region, reference))
        return entries

    @staticmethod
    def _translated_match_score(
        reference: tuple[int, ...] | None,
        edge_map: tuple[int, ...],
    ) -> float:
        """Match a learned bug after a small layout-relative translation.

        The five detector regions are intentionally large enough for lower
        thirds and weather layouts.  A station bug can therefore move several
        edge-map cells inside the same logical corner even when its visual
        identity is unchanged.
        """
        reference = reference or ()
        if len(reference) != 48 * 24 or len(edge_map) != len(reference):
            return LiveLogoDetector._match_score(reference, edge_map)
        reference_count = sum(reference)
        if not reference_count:
            return 0.0
        best = 0.0
        for delta_y in range(-BUG_RELOCATION_SHIFT_Y, BUG_RELOCATION_SHIFT_Y + 1):
            for delta_x in range(-BUG_RELOCATION_SHIFT_X, BUG_RELOCATION_SHIFT_X + 1):
                matched = 0
                for index, expected in enumerate(reference):
                    if not expected:
                        continue
                    x = (index % 48) + delta_x
                    y = (index // 48) + delta_y
                    if 0 <= x < 48 and 0 <= y < 24 and edge_map[(y * 48) + x]:
                        matched += 1
                best = max(best, matched / reference_count)
        return best

    def _bug_family_regions(self, reference: tuple[int, ...]) -> set[str]:
        regions: set[str] = set()
        for _key, region, known in self._reference_entries():
            if not known:
                continue
            similarity = min(
                self._translated_match_score(reference, known),
                self._translated_match_score(known, reference),
            )
            if similarity >= commercial_profiles.TRUSTED_BUG_MATCH_THRESHOLD:
                if region:
                    regions.add(region)
        return regions

    def _bug_mobility_confidence(self) -> float:
        regions = {region for _key, region, _reference in self._reference_entries()}
        return min(1.0, max(0.0, (len(regions) - 1) / 2.0))

    def _clear_relocation_tracking(self, *, samples: bool = False) -> None:
        self._relocation_key = ""
        self._relocation_region = ""
        self._relocation_ticks = 0
        self._relocation_visual_confidence = 0.0
        if samples:
            self._relocation_samples = {name: [] for name in REGION_NAMES}

    def _relocated_bug_match(
        self,
        maps: dict[str, tuple[int, ...]],
    ) -> tuple[float, float, str, str, str, tuple[int, ...] | None]:
        """Confirm a known bug family that shifted inside another valid region."""
        for region, edge_map in maps.items():
            self._relocation_samples[region].append(edge_map)
            del self._relocation_samples[region][:-BUG_RELOCATION_WINDOW_TICKS]
        if len(self._relocation_samples[REGION_NAMES[0]]) < BUG_RELOCATION_WINDOW_TICKS:
            return 0.0, 0.0, self.region, self.region, "", None

        persistent_maps: dict[str, tuple[int, ...]] = {}
        required = BUG_RELOCATION_WINDOW_TICKS * 0.75
        for region, samples in self._relocation_samples.items():
            persistent_maps[region] = tuple(
                1 if sum(sample[index] for sample in samples) >= required else 0
                for index in range(len(samples[0]))
            )

        candidates = []
        for key, expected_region, reference in self._reference_entries():
            for current_region, persistent in persistent_maps.items():
                if current_region == expected_region:
                    continue
                visual = self._translated_match_score(reference, persistent)
                candidates.append(
                    (
                        visual,
                        current_region,
                        expected_region,
                        key,
                        reference,
                        persistent,
                    )
                )
        if not candidates:
            self._clear_relocation_tracking()
            return 0.0, 0.0, self.region, self.region, "", None
        visual, current_region, expected_region, key, reference, persistent = max(candidates)
        if visual < BUG_RELOCATION_VISUAL_THRESHOLD:
            self._clear_relocation_tracking()
            return 0.0, visual, current_region, expected_region, key, reference

        relocation_key = f"{key}:{current_region}"
        if relocation_key == self._relocation_key:
            self._relocation_ticks += 1
        else:
            self._relocation_key = relocation_key
            self._relocation_region = current_region
            self._relocation_ticks = 1
            self._relocation_detected_at = _timestamp()
        self._relocation_visual_confidence = visual
        if self._relocation_ticks == BUG_RELOCATION_SAVE_TICKS:
            self._remember_trusted_bug(
                current_region,
                persistent,
                observed_ticks=self._relocation_ticks,
            )
        # Four persistent samples have already supplied the duration evidence;
        # do not apply the cold-start duration penalty a second time.
        identity_score = visual
        return (
            identity_score,
            visual,
            current_region,
            expected_region,
            key,
            persistent,
        )

    def _best_bug_match(
        self,
        maps: dict[str, tuple[int, ...]],
    ) -> tuple[float, float, str, str, str, tuple[int, ...] | None]:
        candidates = []
        for key, expected_region, reference in self._reference_entries():
            for current_region, edge_map in maps.items():
                visual = self._translated_match_score(reference, edge_map)
                position_prior = 1 if current_region == expected_region else 0
                candidates.append(
                    (
                        visual,
                        position_prior,
                        visual,
                        current_region,
                        expected_region,
                        key,
                        reference,
                    )
                )
        if not candidates:
            self._active_bug_key = ""
            self._active_bug_ticks = 0
            return 0.0, 0.0, self.region, self.region, "", None
        base_score, _position_prior, visual, current_region, expected_region, key, reference = max(candidates)
        active_key = f"{key}:{current_region}"
        if active_key == self._active_bug_key and base_score >= 0.45:
            self._active_bug_ticks += 1
        else:
            self._active_bug_key = active_key
            self._active_bug_ticks = 1 if base_score >= 0.45 else 0
        duration = min(1.0, self._active_bug_ticks / BUG_DURATION_FULL_TRUST_TICKS)
        # A known visual identity is valid in every candidate region. Duration
        # adds only a small stability bonus; it must not make relocation look
        # like disappearance while the slower unknown-graphic learner runs.
        identity_score = base_score * (0.90 + 0.10 * duration)
        return (
            identity_score,
            visual,
            current_region,
            expected_region,
            key,
            reference,
        )

    def _track_bug_promotion(
        self,
        *,
        key: str,
        current_region: str,
        expected_region: str,
        reference: tuple[int, ...] | None,
        stable_program: bool,
    ) -> None:
        known_relocation = current_region != expected_region
        if (
            not stable_program
            or not key
            or reference is None
            or (key.startswith("trusted:") and not known_relocation)
        ):
            self._promotion_bug_key = ""
            self._promotion_bug_ticks = 0
            return
        promotion_key = f"{key}:{current_region}"
        if promotion_key == self._promotion_bug_key:
            self._promotion_bug_ticks += 1
        else:
            self._promotion_bug_key = promotion_key
            self._promotion_bug_ticks = 1
        required_ticks = (
            BUG_RELOCATION_SAVE_TICKS if known_relocation else BUG_PROMOTION_TICKS
        )
        if self._promotion_bug_ticks < required_ticks:
            return
        self._remember_trusted_bug(
            current_region,
            reference,
            observed_ticks=self._promotion_bug_ticks,
        )
        self._promotion_bug_key = ""
        self._promotion_bug_ticks = 0

    def _bug_transition_grace(self) -> bool:
        return bool(
            self._bug_transition_grace_until
            and datetime.now(timezone.utc) < self._bug_transition_grace_until
        )

    def _manual_program_hold(self) -> bool:
        return bool(
            self._manual_program_until
            and datetime.now(timezone.utc) < self._manual_program_until
        )

    def apply_program_feedback(self) -> None:
        """Immediately honor an explicit correction and prevent a fast rebound."""
        was_commercial = self.last_commercial is True
        self._manual_program_until = datetime.now(timezone.utc) + timedelta(
            seconds=MANUAL_PROGRAM_HOLD_SECONDS
        )
        self._commercial_reason = ""
        self.last_commercial = False
        self.state = "program"
        self.commercial_confidence = 0.0
        self.overall_commercial_confidence = 0.0
        self._missing_count = 0
        self._present_count = 0
        self._local_candidate_count = 0
        self._countdown_candidate_count = 0
        self._countdown_missing_count = 0
        self.last_decision_at = _timestamp()
        if was_commercial:
            self.callback(False)

    def _observe(
        self,
        maps: dict[str, tuple[int, ...]],
        scoreboard_maps: dict[str, tuple[int, ...]],
        color_histogram: tuple[float, ...],
    ) -> None:
        (
            best_logo_score,
            visual_logo_score,
            matched_region,
            expected_region,
            matched_key,
            matched_reference,
        ) = self._best_bug_match(maps)
        if best_logo_score < 0.50:
            relocated = self._relocated_bug_match(maps)
            if relocated[0] > best_logo_score:
                (
                    best_logo_score,
                    visual_logo_score,
                    matched_region,
                    expected_region,
                    matched_key,
                    matched_reference,
                ) = relocated
        else:
            self._clear_relocation_tracking(samples=True)
        self.logo_match_confidence = visual_logo_score
        self.bug_identity_confidence = best_logo_score
        raw_logo_missing = best_logo_score < 0.50
        if not raw_logo_missing:
            self.logo_last_seen_at = _timestamp()
        if self.last_commercial is None and not raw_logo_missing:
            self.last_commercial = False
            self.state = "program"
            self.last_decision_at = _timestamp()
        self._missing_count = self._missing_count + 1 if raw_logo_missing else 0
        duration_score = min(1.0, self._missing_count / MISSING_CONFIRMATIONS)
        logo_presence_delta = 1.0 - best_logo_score
        if raw_logo_missing:
            logo_presence_delta = min(1.0, logo_presence_delta * LOGO_ABSENCE_BOOST)
        secondary_score = 0.0
        if self._scoreboard_reference is not None and self.scoreboard_region:
            secondary_score = self._match_score(
                self._scoreboard_reference,
                scoreboard_maps[self.scoreboard_region],
            )
        secondary_absence = 1.0 - secondary_score if self._scoreboard_reference is not None else 1.0
        self.scoreboard_confidence = secondary_score
        secondary_missing = (
            self.sports_generated
            and self._scoreboard_reference is not None
            and secondary_score < 0.45
        )
        self._secondary_missing_count = (
            self._secondary_missing_count + 1 if secondary_missing else 0
        )
        current_color_change: float | None = None
        if self._previous_color_histogram is not None:
            current_color_change = _distribution_distance(
                self._previous_color_histogram,
                color_histogram,
            )
            self._color_changes.append(current_color_change)
            del self._color_changes[:-COLOR_WINDOW_FRAMES]
        self._previous_color_histogram = color_histogram
        cut_threshold = COLOR_CUT_FLOOR
        if len(self._program_color_changes) >= LEARNING_FRAMES:
            ordered = sorted(self._program_color_changes)
            baseline_p90 = ordered[int((len(ordered) - 1) * 0.90)]
            cut_threshold = max(COLOR_CUT_FLOOR, baseline_p90 * 1.25)
        if len(self._color_changes) >= COLOR_MIN_SAMPLES:
            self.cut_density = sum(
                change >= cut_threshold for change in self._color_changes
            ) / len(self._color_changes)
            self.mean_color_change = sum(self._color_changes) / len(self._color_changes)
            mean_excess = sum(
                max(0.0, change - cut_threshold) for change in self._color_changes
            ) / len(self._color_changes)
            self.color_volatility = min(
                1.0,
                0.85 * (self.cut_density / 0.40) + 0.15 * (mean_excess / 0.20),
            )
        else:
            self.cut_density = 0.0
            self.mean_color_change = 0.0
            self.color_volatility = 0.0

        self.primary_confidence = (
            0.80 * logo_presence_delta
            + 0.15 * duration_score
            + 0.05 * secondary_absence
        )
        local_duration = min(1.0, self._secondary_missing_count / LOCAL_LAYOUT_CONFIRMATIONS)
        has_color_baseline = len(self._program_color_changes) >= LEARNING_FRAMES
        if self.sports_generated and self._scoreboard_reference is not None and not raw_logo_missing:
            self.local_break_confidence = (
                0.25 * secondary_absence
                + 0.20 * local_duration
                + 0.55 * self.color_volatility
            )
        elif (
            not self.sports_generated
            and not raw_logo_missing
            and has_color_baseline
            and not self._boundary_suppressed()
        ):
            # Normal shows have no dependable scoreboard. Convert only unusually
            # high color/cut volatility into confidence; ordinary scene changes
            # and the credits sequence observed during testing remain below the
            # commercial threshold.
            self.local_break_confidence = max(
                0.0,
                min(1.0, (self.color_volatility - 0.45) / 0.55),
            )
        else:
            self.local_break_confidence = 0.0
        self.commercial_confidence = max(
            0.0,
            min(1.0, max(self.primary_confidence, self.local_break_confidence)),
        )
        if self._channel_profile:
            scored = commercial_profiles.score_features(
                self._channel_profile,
                self._profile_features(),
            )
            self.channel_model_ready = bool(scored.get("ready"))
            self.channel_model_score = float(scored.get("score") or 0)
        model_program_veto = bool(
            self.channel_model_ready
            and self.channel_model_score < MODEL_STRONG_PROGRAM_SCORE
        )
        model_supports_local = bool(
            not self.channel_model_ready
            or self.channel_model_score >= MODEL_LOCAL_COMMERCIAL_SCORE
        )
        primary_candidate = self.primary_confidence > COMMERCIAL_THRESHOLD
        local_candidate = self.local_break_confidence > COMMERCIAL_THRESHOLD and (
            (
                self.sports_generated
                and secondary_missing
                and len(self._color_changes) >= COLOR_MIN_SAMPLES
            )
            or (
                not self.sports_generated
                and has_color_baseline
                and len(self._color_changes) >= COLOR_WINDOW_FRAMES
            )
        )
        self._local_candidate_count = (
            self._local_candidate_count + 1 if local_candidate else 0
        )
        if not raw_logo_missing:
            self.region = matched_region
        missing_confirmations = MISSING_CONFIRMATIONS
        if model_program_veto:
            # A channel-specific pattern already corrected as program must
            # persist for the full short-window boundary before it can hide
            # playback again.
            missing_confirmations = max(
                missing_confirmations,
                int(SHORT_FALSE_POSITIVE_SECONDS / POLL_SECONDS),
            )
        bug_override = (
            raw_logo_missing
            and self._missing_count >= missing_confirmations
            and bool(self._reference_entries())
            and not self._bug_transition_grace()
            and not self._manual_program_hold()
        )
        commercial = self.last_commercial
        if commercial is not True:
            self._present_count = 0
        if (
            bug_override
        ):
            commercial = True
            self._commercial_reason = "logo-missing"
        elif (
            commercial is not True
            and local_candidate
            and not self._manual_program_hold()
            and model_supports_local
            and self._local_candidate_count >= (
                LOCAL_BREAK_CONFIRMATIONS
                if self.sports_generated else NORMAL_LOCAL_BREAK_CONFIRMATIONS
            )
        ):
            commercial = True
            self._commercial_reason = "local-layout" if self.sports_generated else "local-color"
        elif commercial is True:
            if bug_override and self._commercial_reason != "logo-missing":
                self._commercial_reason = "logo-missing"
                returned = False
            elif self._commercial_reason == "logo-missing":
                returned = bool(
                    matched_key
                    and best_logo_score >= BUG_RETURN_CONFIDENCE
                )
            elif self._commercial_reason == "local-layout":
                returned = not secondary_missing
            else:
                returned = self.color_volatility < 0.35
            required_return_confirmations = RETURN_CONFIRMATIONS
            if self._commercial_reason == "logo-missing":
                strong_learned_program_return = bool(
                    not self.sports_generated
                    and self.channel_model_ready
                    and self.channel_model_score <= STRONG_PROGRAM_MODEL_MAX
                    and best_logo_score >= BUG_RETURN_CONFIDENCE
                    and visual_logo_score >= STRONG_PROGRAM_GRAPHIC_CONFIDENCE
                )
                required_return_confirmations = (
                    STRONG_PROGRAM_RETURN_CONFIRMATIONS
                    if strong_learned_program_return
                    else BUG_RETURN_CONFIRMATIONS
                )
                candidate_key = (
                    f"{matched_key}:{matched_region}" if returned else ""
                )
                if candidate_key and candidate_key == self._return_candidate_key:
                    self._present_count += 1
                elif candidate_key:
                    self._return_candidate_key = candidate_key
                    self._present_count = 1
                else:
                    self._return_candidate_key = ""
                    self._present_count = 0
            else:
                self._return_candidate_key = ""
                self._present_count = self._present_count + 1 if returned else 0
            if self._present_count >= required_return_confirmations:
                commercial = False
                self._commercial_reason = ""
                self._return_candidate_key = ""

        # This diagnostic value represents the final state after every signal,
        # threshold, dwell timer, and channel-model veto has been applied. Keep
        # it separate from the raw trigger confidence used by the classifier.
        target_confidence = 1.0 if commercial is True else 0.0
        confidence_alpha = (
            OVERALL_CONFIDENCE_RISE_ALPHA
            if target_confidence > self.overall_commercial_confidence
            else OVERALL_CONFIDENCE_FALL_ALPHA
        )
        self.overall_commercial_confidence += confidence_alpha * (
            target_confidence - self.overall_commercial_confidence
        )

        stable_program_frame = (
            current_color_change is not None
            and self.last_commercial is False
            and not raw_logo_missing
            and not secondary_missing
            and self.commercial_confidence < 0.30
        )
        if stable_program_frame:
            self._program_color_changes.append(current_color_change)
            del self._program_color_changes[:-PROGRAM_COLOR_BASELINE_FRAMES]
        self._track_bug_promotion(
            key=matched_key,
            current_region=matched_region,
            expected_region=expected_region,
            reference=matched_reference,
            stable_program=bool(
                self.last_commercial is False
                and not raw_logo_missing
                and self.commercial_confidence < 0.30
            ),
        )
        if commercial is True and self.last_commercial is True:
            self._commercial_episode_frame_count += 1
            episode_seconds = self._commercial_episode_frame_count * POLL_SECONDS
            if episode_seconds >= FALSE_POSITIVE_BUFFER_SECONDS:
                self._commercial_episode_features.clear()
                self._commercial_episode_feedback_expired = True
            elif self._commercial_episode_frame_count % FALSE_POSITIVE_SAMPLE_FRAMES == 0:
                self._commercial_episode_features.append(dict(self._profile_features()))
        if commercial != self.last_commercial:
            self._commercial_frame_count = 0
            self._clear_recovery()
            self.recovery_state = "idle"
            next_state = "commercial" if commercial else "program"
            short_false_positive = False
            false_positive_duration = 0.0
            if commercial:
                self._return_candidate_key = ""
                self._commercial_event_id = f"logo-{uuid.uuid4().hex[:20]}"
                self._commercial_started_monotonic = time.monotonic()
                self._commercial_episode_frame_count = 0
                self._commercial_episode_features = [dict(self._profile_features())]
                self._commercial_episode_feedback_expired = False
            elif self._commercial_started_monotonic is not None:
                false_positive_duration = max(
                    0.0,
                    time.monotonic() - self._commercial_started_monotonic,
                )
                short_false_positive = (
                    bool(self._commercial_event_id)
                    and not self._commercial_episode_feedback_expired
                    and false_positive_duration < SHORT_FALSE_POSITIVE_SECONDS
                )
            self._record_state_transition(commercial, next_state)
            if short_false_positive:
                self._record_short_false_positive(false_positive_duration)
            self.last_commercial = commercial
            self.state = next_state
            self.last_decision_at = _timestamp()
            self.callback(bool(commercial))
            if not commercial:
                self._return_candidate_key = ""
                self._commercial_event_id = ""
                self._commercial_started_monotonic = None
                self._commercial_episode_frame_count = 0
                self._commercial_episode_features.clear()
                self._commercial_episode_feedback_expired = False

    def _learn_scoreboard(self, maps: dict[str, tuple[int, ...]]) -> None:
        self._scoreboard_pair.append(maps)
        if len(self._scoreboard_pair) < 2:
            return
        first, second = self._scoreboard_pair[-2:]
        candidates = []
        for name in SCOREBOARD_NAMES:
            if name == self.region:
                continue
            left, right = first[name], second[name]
            left_count, right_count = sum(left), sum(right)
            intersection = sum(1 for a, b in zip(left, right) if a and b)
            union = sum(1 for a, b in zip(left, right) if a or b)
            similarity = intersection / union if union else 0.0
            score = min(left_count, right_count) * similarity
            candidates.append((score, similarity, name, right))
        score, similarity, name, reference = max(candidates)
        if score >= 45 and similarity >= 0.42:
            self.scoreboard_region = name
            self._scoreboard_reference = reference
            self.scoreboard_detected_at = _timestamp()
        else:
            # Retain the newest frame so each subsequent sample gets another
            # half-second confirmation opportunity.
            self._scoreboard_pair = self._scoreboard_pair[-1:]

    def _clear_recovery(self) -> None:
        for samples in self._recovery_samples.values():
            samples.clear()

    def _recover_replacement_bug(self, maps: dict[str, tuple[int, ...]]) -> bool:
        """Cautiously accept a stable post-break bug instead of holding forever."""
        self._commercial_frame_count += 1
        if self._commercial_frame_count < RECOVERY_DELAY_FRAMES:
            self.recovery_state = "waiting"
            return False
        if self.color_volatility > RECOVERY_COLOR_LIMIT:
            self._clear_recovery()
            self.recovery_state = "waiting-for-stability"
            return False
        self.recovery_state = "learning-replacement"
        for name, edge_map in maps.items():
            self._recovery_samples[name].append(edge_map)
            del self._recovery_samples[name][:-RECOVERY_STABLE_FRAMES]
        if len(self._recovery_samples[REGION_NAMES[0]]) < RECOVERY_STABLE_FRAMES:
            return False

        candidates: list[tuple[int, str, tuple[int, ...]]] = []
        for name, samples in self._recovery_samples.items():
            required = len(samples) * 0.75
            persistent = tuple(
                1 if sum(sample[index] for sample in samples) >= required else 0
                for index in range(len(samples[0]))
            )
            candidates.append((sum(persistent), name, persistent))
        score, name, persistent = max(candidates)
        if score < MIN_PERSISTENT_EDGES:
            return False

        self._remember_trusted_bug(
            name,
            persistent,
            observed_ticks=RECOVERY_STABLE_FRAMES,
        )
        self._references = {name: persistent}
        self._samples = {region: [] for region in REGION_NAMES}
        self._samples[name] = list(self._recovery_samples[name])
        self.region = name
        self.logo_detected_at = _timestamp()
        self.logo_last_seen_at = self.logo_detected_at
        self.last_commercial = False
        self.state = "program"
        self.last_decision_at = _timestamp()
        self.commercial_confidence = 0.0
        self.primary_confidence = 0.0
        self.logo_match_confidence = 0.0
        self.bug_identity_confidence = 0.0
        self._missing_count = 0
        self._present_count = 0
        self._commercial_reason = ""
        self._commercial_frame_count = 0
        self.recovery_state = "replacement-accepted"
        self._clear_recovery()
        self.callback(False)
        return True

    def _release_uncertain_normal_stream(self) -> None:
        """Fail open after four minutes rather than hiding normal TV forever."""
        self.last_commercial = False
        self.state = "learning"
        self.last_decision_at = _timestamp()
        self.commercial_confidence = 0.0
        self.primary_confidence = 0.0
        self.local_break_confidence = 0.0
        self.logo_match_confidence = 0.0
        self.bug_identity_confidence = 0.0
        self._references.clear()
        self._samples = {region: [] for region in REGION_NAMES}
        self._missing_count = 0
        self._present_count = 0
        self._commercial_reason = ""
        self._commercial_frame_count = 0
        self.recovery_state = "safety-release"
        self._clear_recovery()
        self.callback(False)

    def _profile_features(self) -> dict[str, float]:
        return {
            "cut_density": self.cut_density,
            "mean_color_change": self.mean_color_change,
            "color_volatility": self.color_volatility,
            "edge_density": self.edge_density,
            "mean_brightness": self.mean_brightness,
            "mean_saturation": self.mean_saturation,
            "program_graphics_confidence": self.logo_match_confidence,
            "bug_identity_confidence": self.bug_identity_confidence,
            "commercial_confidence": self.overall_commercial_confidence,
        }

    def _boundary_suppressed(self) -> bool:
        return bool(
            self._boundary_suppressed_until
            and datetime.now(timezone.utc) < self._boundary_suppressed_until
        )

    def _refresh_programme_boundary(self) -> None:
        if self.epg_path is None or not self.channel_identity.startswith("tvg:"):
            return
        try:
            from guide_epg import programme_window

            window = programme_window(
                self.epg_path,
                self.channel_identity.split(":", 1)[1],
                timezone_name=self.timezone_name,
            )
        except (OSError, ValueError):
            return
        current = dict(window.get("current") or {})
        key = "|".join(
            str(current.get(name) or "") for name in ("start", "stop", "title")
        )
        if not key.strip("|"):
            return
        if not self._current_program_key:
            self._current_program_key = key
            return
        if key == self._current_program_key:
            return
        self._current_program_key = key
        self._boundary_suppressed_until = datetime.now(timezone.utc) + timedelta(
            seconds=PROGRAM_BOUNDARY_SUPPRESSION_SECONDS
        )
        self._bug_transition_grace_until = datetime.now(timezone.utc) + timedelta(
            seconds=BUG_TRANSITION_GRACE_SECONDS
        )
        self._program_color_changes.clear()
        self._color_changes.clear()
        self._local_candidate_count = 0
        self.local_break_confidence = 0.0
        if self.last_commercial is True and self._commercial_reason in {
            "local-color",
            "logo-missing",
        }:
            self.last_commercial = False
            self.state = "program"
            self._commercial_reason = ""
            self._missing_count = 0
            self._present_count = 0
            self.recovery_state = "program-transition-grace"
            self.last_decision_at = _timestamp()
            self.callback(False)

    def _sample_channel_profile(self) -> None:
        if self.sports_generated or not self.channel_identity or self.profile_db_path is None:
            return
        self._profile_sample_frames += 1
        if self._profile_sample_frames < PROFILE_SAMPLE_FRAMES:
            return
        self._profile_sample_frames = 0
        self._refresh_programme_boundary()
        if self.last_commercial is True and self._commercial_reason in {
            "logo-missing",
            "countdown-clock",
        }:
            label = "commercial"
        elif (
            self.last_commercial is False
            and self.bug_identity_confidence >= 0.50
            and self.primary_confidence < 0.45
        ):
            label = "program"
        else:
            label = "uncertain"
        try:
            commercial_profiles.record(
                self.profile_db_path,
                self.channel_identity,
                label=label,
                features=self._profile_features(),
                event_id=self._commercial_event_id if self.last_commercial is True else "",
                detector_state=self.state,
                commercial_reason=self._commercial_reason,
            )
            self._profile_samples_since_refresh += 1
            if not self._channel_profile or self._profile_samples_since_refresh >= PROFILE_REFRESH_SAMPLES:
                self._channel_profile = commercial_profiles.profile(
                    self.profile_db_path,
                    self.channel_identity,
                )
                self._profile_samples_since_refresh = 0
            scored = commercial_profiles.score_features(
                self._channel_profile,
                self._profile_features(),
            )
        except (OSError, sqlite3.Error):
            return
        self.channel_model_ready = bool(scored.get("ready"))
        self.channel_model_score = float(scored.get("score") or 0)

    def _record_state_transition(self, commercial: bool, state: str) -> None:
        if (
            self.sports_generated
            or self.last_commercial is None
            or not self.channel_identity
            or self.profile_db_path is None
        ):
            return
        try:
            label = "commercial" if commercial else "program"
            commercial_profiles.record(
                self.profile_db_path,
                self.channel_identity,
                label=label,
                source="state-transition",
                event_id=self._commercial_event_id,
                features=self._profile_features(),
                detector_state=state,
                commercial_reason=self._commercial_reason,
            )
        except (OSError, sqlite3.Error):
            return

    def _record_short_false_positive(self, duration_seconds: float) -> None:
        if (
            self.sports_generated
            or not self.channel_identity
            or self.profile_db_path is None
            or not self._commercial_event_id
        ):
            return
        try:
            commercial_profiles.relabel_event_as_false_positive(
                self.profile_db_path,
                self.channel_identity,
                self._commercial_event_id,
            )
            commercial_profiles.record_many(
                self.profile_db_path,
                (
                    {
                        "channel_identity": self.channel_identity,
                        "label": "program",
                        "source": "auto-false-positive",
                        "event_id": self._commercial_event_id,
                        "features": features,
                        "detector_state": "program",
                        "commercial_reason": "short-false-positive",
                    }
                    for features in self._commercial_episode_features
                ),
            )
            self._channel_profile = commercial_profiles.profile(
                self.profile_db_path,
                self.channel_identity,
            )
            self._profile_samples_since_refresh = 0
        except (OSError, sqlite3.Error):
            return

    def _update_countdown_detector(
        self,
        maps: dict[str, tuple[int, ...]],
        *,
        fallback_allowed: bool,
    ) -> bool:
        """Observe all four corners and return whether bugless mode owns state."""
        scored: list[tuple[float, str, tuple[int, ...] | None]] = []
        for name in COUNTDOWN_REGION_NAMES:
            samples = self._countdown_samples[name]
            samples.append(maps[name])
            del samples[:-COUNTDOWN_WINDOW_FRAMES]
            confidence, reference = _countdown_signature(samples)
            scored.append((confidence, name, reference))

        confidence, region, reference = max(scored)
        self.countdown_confidence = confidence
        if self._manual_program_hold():
            self._countdown_candidate_count = 0
            self._countdown_missing_count = 0
            if self.bugless_countdown_mode:
                self._set_countdown_commercial(False)
            return self.bugless_countdown_mode
        if not self.bugless_countdown_mode:
            self._countdown_candidate_count = (
                self._countdown_candidate_count + 1
                if fallback_allowed and confidence >= COUNTDOWN_THRESHOLD
                else 0
            )
            if self._countdown_candidate_count >= COUNTDOWN_CONFIRMATIONS and reference:
                self.bugless_countdown_mode = True
                self.countdown_region = region
                self.countdown_detected_at = _timestamp()
                self._countdown_reference = reference
                self._countdown_missing_count = 0
                self._set_countdown_commercial(True)
            return self.bugless_countdown_mode

        # The learned clock disappearing is a faster return signal than
        # waiting for the full rolling periodicity window to drain.
        presence = self._match_score(
            self._countdown_reference,
            maps.get(self.countdown_region, ()),
        )
        self.countdown_confidence = max(self.countdown_confidence, presence)
        if self.last_commercial is not True:
            self._countdown_candidate_count = (
                self._countdown_candidate_count + 1 if presence >= 0.42 else 0
            )
            if self._countdown_candidate_count >= COUNTDOWN_CONFIRMATIONS:
                self._countdown_missing_count = 0
                self._set_countdown_commercial(True)
        elif presence >= 0.42:
            self._countdown_candidate_count = 0
            self._countdown_missing_count = 0
            self._set_countdown_commercial(True)
        else:
            self._countdown_missing_count += 1
            if self._countdown_missing_count >= COUNTDOWN_RELEASE_CONFIRMATIONS:
                self._countdown_candidate_count = 0
                self._set_countdown_commercial(False)
        return True

    def _set_countdown_commercial(self, commercial: bool) -> None:
        self.commercial_confidence = self.countdown_confidence if commercial else 0.0
        target = 1.0 if commercial else 0.0
        alpha = (
            OVERALL_CONFIDENCE_RISE_ALPHA
            if target > self.overall_commercial_confidence
            else OVERALL_CONFIDENCE_FALL_ALPHA
        )
        self.overall_commercial_confidence += alpha * (
            target - self.overall_commercial_confidence
        )
        self._commercial_reason = "countdown-clock" if commercial else ""
        if self.last_commercial is commercial:
            if commercial:
                self._commercial_frame_count += 1
            return

        self._commercial_frame_count = 0
        next_state = "commercial" if commercial else "program"
        if commercial:
            self._commercial_event_id = f"clock-{uuid.uuid4().hex[:20]}"
            self._commercial_started_monotonic = time.monotonic()
        self._record_state_transition(commercial, next_state)
        self.last_commercial = commercial
        self.state = next_state
        self.last_decision_at = _timestamp()
        self.callback(commercial)
        if not commercial:
            self._commercial_event_id = ""
            self._commercial_started_monotonic = None

    def _process(self, path: Path) -> None:
        with Image.open(path) as image:
            maps = {name: _edge_map(region) for name, region in _regions(image).items()}
            scoreboard_maps = (
                {
                    name: _gaussian_structure(region)
                    for name, region in _scoreboard_regions(image).items()
                }
                if self.sports_generated else {}
            )
            color_histogram, self.mean_saturation, self.mean_brightness = _color_features(image)
        self.edge_density = sum(sum(edge_map) for edge_map in maps.values()) / float(
            len(maps) * len(next(iter(maps.values())))
        )
        self._frames_observed += 1
        self._load_trusted_bugs()
        if not self.sports_generated and self._update_countdown_detector(
            maps,
            fallback_allowed=bool(
                self._frames_observed >= COUNTDOWN_FALLBACK_PROBATION_FRAMES
                and not self._reference_entries()
            ),
        ):
            self._sample_channel_profile()
            return
        # Bootstrap from the first rolling window. Once a credible broadcast
        # graphic exists, classify the frame against that trusted evidence
        # before allowing it into the learning bank. This prevents persistent
        # commercial graphics (for example, a prescription-drug name) from
        # being learned as a replacement network bug.
        if not self._reference_entries():
            if not self._refresh_references(maps):
                return
            self._observe(maps, scoreboard_maps, color_histogram)
        else:
            self._observe(maps, scoreboard_maps, color_histogram)
            if self.last_commercial is True and self._commercial_reason == "logo-missing":
                if self._recover_replacement_bug(maps):
                    self._sample_channel_profile()
                    return
                if (
                    not self.sports_generated
                    and self._commercial_frame_count >= NORMAL_MAXIMUM_HOLD_FRAMES
                ):
                    self._release_uncertain_normal_stream()
                    self._sample_channel_profile()
                    return
            if (
                self.last_commercial is not True
                and (
                    self.commercial_confidence < 0.30
                    or self._bug_transition_grace()
                )
                and self._secondary_missing_count == 0
            ):
                self._refresh_references(maps)

        if not self._reference_entries():
            self._sample_channel_profile()
            return
        if self.sports_generated and self._references and self._scoreboard_reference is None:
            self._learn_scoreboard(scoreboard_maps)
        self._sample_channel_profile()

    def _run(self) -> None:
        try:
            while not self._stop.wait(POLL_SECONDS):
                frames = sorted(self.directory.glob("frame-*.jpg"))
                for path in frames:
                    try:
                        self._process(path)
                    except (OSError, ValueError):
                        continue
                    finally:
                        path.unlink(missing_ok=True)
        except Exception as exc:
            if not self._stop.is_set():
                self.state = "error"
                self.error = str(exc)

    def status(self) -> dict:
        trusted_regions = {
            str(region or "")
            for bug in self._trusted_bugs
            for region in (bug.get("regions") or [bug.get("region")])
            if str(region or "")
        }
        logo_candidates = sorted(set(self._references) | trusted_regions)
        return {
            "state": self.state,
            "region": self.region,
            "logo_detected": bool(self._reference_entries()),
            "logo_candidates": logo_candidates,
            "trusted_bug_count": len(self._trusted_bugs),
            "logo_detected_at": self.logo_detected_at,
            "logo_last_seen_at": self.logo_last_seen_at,
            "scoreboard_detected": self._scoreboard_reference is not None,
            "scoreboard_applicable": self.sports_generated,
            "scoreboard_region": self.scoreboard_region,
            "scoreboard_detected_at": self.scoreboard_detected_at,
            "countdown_applicable": not self.sports_generated,
            "countdown_detected": self.bugless_countdown_mode,
            "countdown_region": self.countdown_region,
            "countdown_detected_at": self.countdown_detected_at,
            "countdown_confidence": round(self.countdown_confidence * 100, 1),
            "countdown_fallback_available": bool(
                self.bugless_countdown_mode
                or (
                    self._frames_observed >= COUNTDOWN_FALLBACK_PROBATION_FRAMES
                    and not self._reference_entries()
                )
            ),
            "countdown_probation_seconds_remaining": round(
                max(
                    0.0,
                    (
                        COUNTDOWN_FALLBACK_PROBATION_FRAMES
                        - self._frames_observed
                    ) * POLL_SECONDS,
                ),
                1,
            ),
            "commercial": self.last_commercial,
            "commercial_confidence": round(self.overall_commercial_confidence * 100, 1),
            "trigger_confidence": round(self.commercial_confidence * 100, 1),
            "primary_confidence": round(self.primary_confidence * 100, 1),
            "bug_identity_confidence": round(self.bug_identity_confidence * 100, 1),
            "bug_mobility_confidence": round(self._bug_mobility_confidence() * 100, 1),
            "bug_relocation_active": bool(self._relocation_key),
            "bug_relocation_region": self._relocation_region,
            "bug_relocation_detected_at": self._relocation_detected_at,
            "local_break_confidence": round(self.local_break_confidence * 100, 1),
            "color_volatility": round(self.color_volatility * 100, 1),
            "scoreboard_confidence": round(self.scoreboard_confidence * 100, 1),
            "commercial_reason": self._commercial_reason,
            "channel_identity": self.channel_identity,
            "channel_model_ready": self.channel_model_ready,
            "channel_model_score": round(self.channel_model_score * 100, 1),
            "program_boundary_suppressed": self._boundary_suppressed(),
            "bug_transition_grace": self._bug_transition_grace(),
            "channel_features": {
                name: round(value * 100, 1)
                for name, value in self._profile_features().items()
            },
            "recovery_state": self.recovery_state,
            "commercial_observed_seconds": round(
                self._commercial_frame_count * POLL_SECONDS, 1
            ),
            "last_decision_at": self.last_decision_at,
            "error": self.error,
            "sample_interval_seconds": POLL_SECONDS,
            "evidence_half_life_seconds": EVIDENCE_HALF_LIFE_SECONDS,
        }

    def profile_snapshot(self) -> dict:
        return {
            "channel_identity": self.channel_identity,
            "sports_generated": self.sports_generated,
            "features": self._profile_features(),
            "detector_state": self.state,
            "commercial_reason": self._commercial_reason,
        }

    def stop(self) -> None:
        self._stop.set()
        shutil.rmtree(self.directory, ignore_errors=True)
