from __future__ import annotations

import json
import math
import os
import re
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

from PIL import Image, ImageChops, ImageFilter, ImageOps

import commercial_profiles
import commercial_signatures


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
ADAPTIVE_MISSING_MIN_EPISODES = 5
ADAPTIVE_MISSING_MAX_SECONDS = 10.0
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
BUG_PROMOTION_TICKS = 180
BUG_PROMOTION_MIN_SCENE_CHANGES = 3
BUG_PROMOTION_SCENE_GAP_TICKS = 8
BUG_PROMOTION_COOLDOWN_SECONDS = 60.0
MAX_SESSION_TRUSTED_BUGS = 3
BUG_RELOCATION_WINDOW_TICKS = 4
BUG_RELOCATION_SAVE_TICKS = 12
BUG_RELOCATION_VISUAL_THRESHOLD = 0.70
BUG_RELOCATION_SHIFT_X = 4
BUG_RELOCATION_SHIFT_Y = 3
BUG_TRANSITION_GRACE_SECONDS = 20
EDGE_THRESHOLD = 35
ADAPTIVE_EDGE_FLOOR = 10
ADAPTIVE_EDGE_CEILING = 28
FUZZY_EDGE_RADIUS = 1
COMMERCIAL_THRESHOLD = 0.65
BUG_RETURN_CONFIDENCE = 0.78
BUG_RETURN_CONFIRMATIONS = 8
STRONG_PROGRAM_GRAPHIC_CONFIDENCE = 0.78
PROGRAM_RETURN_MIN_CONFIDENCE = 0.65
PROGRAM_RETURN_ADAPTIVE_FLOOR = 0.42
PROGRAM_RETURN_FULL_TRUST_TICKS = 40
PROGRAM_RETURN_ADAPTIVE_STEP = 0.05
PROGRAM_RETURN_EVIDENCE_TARGET = 3
PROGRAM_RETURN_STRONG_EVIDENCE = 2
PROGRAM_RETURN_WEAK_EVIDENCE = 1
PROGRAM_RETURN_MISMATCH_PENALTY = 2
MANUAL_PROGRAM_HOLD_SECONDS = 30
MIN_PERSISTENT_EDGES = 16
COUNTDOWN_WINDOW_FRAMES = 24
COUNTDOWN_FALLBACK_PROBATION_SECONDS = 120.0
COUNTDOWN_FALLBACK_PROBATION_FRAMES = int(
    COUNTDOWN_FALLBACK_PROBATION_SECONDS / POLL_SECONDS
)
BUGLESS_CLASSIFICATION_SECONDS = 120.0
BUGLESS_CLASSIFICATION_FRAMES = int(BUGLESS_CLASSIFICATION_SECONDS / POLL_SECONDS)
BUGLESS_RESCAN_DELAY_SECONDS = 10.0
BUGLESS_RESCAN_DELAY_FRAMES = int(BUGLESS_RESCAN_DELAY_SECONDS / POLL_SECONDS)
BUGLESS_FALSE_LOGO_TRIGGER_LIMIT = 2
COUNTDOWN_CONFIRMATIONS = 8
COUNTDOWN_REENTRY_CONFIRMATIONS = 8
COUNTDOWN_RELEASE_CONFIRMATIONS = 8
COUNTDOWN_IDLE_RELEASE_SECONDS = 20.0
COUNTDOWN_IDLE_RELEASE_FRAMES = int(COUNTDOWN_IDLE_RELEASE_SECONDS / POLL_SECONDS)
COUNTDOWN_THRESHOLD = 0.68
COUNTDOWN_PRESENCE_THRESHOLD = 0.58
COUNTDOWN_MIN_PERSISTENT_EDGES = 6
COUNTDOWN_MAX_PERSISTENT_EDGES = 320
REGION_NAMES = ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")
COUNTDOWN_REGION_NAMES = ("top-left", "top-right", "bottom-left", "bottom-right")
SCOREBOARD_NAMES = (
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _adaptive_missing_confirmations(profile: dict | None) -> int:
    """Delay unreliable logo-loss triggers while keeping proven channels fast."""
    values = profile or {}
    episodes = max(0, int(values.get("logo_missing_episodes") or 0))
    if episodes < ADAPTIVE_MISSING_MIN_EPISODES:
        return MISSING_CONFIRMATIONS
    false_positives = max(
        0,
        min(episodes, int(values.get("logo_missing_short_false_positives") or 0)),
    )
    # A small Beta(1, 1) prior prevents five early samples from immediately
    # pinning a channel at either the fastest or slowest possible response.
    false_positive_rate = (false_positives + 1.0) / (episodes + 2.0)
    base_seconds = MISSING_CONFIRMATIONS * POLL_SECONDS
    hold_seconds = base_seconds + (
        (ADAPTIVE_MISSING_MAX_SECONDS - base_seconds)
        * (false_positive_rate ** 2)
    )
    return max(MISSING_CONFIRMATIONS, math.ceil(hold_seconds / POLL_SECONDS))


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
    gray = image.convert("L").resize((48, 24))
    # Broadcast bugs are frequently monochrome and partially transparent. A
    # fixed absolute threshold loses their silhouette as the picture beneath
    # them changes. Preserve the original strong edges, but supplement them
    # with locally normalized structure and an adaptive weak-edge threshold.
    strong_values = list(gray.filter(ImageFilter.FIND_EDGES).getdata())
    normalized = ImageOps.autocontrast(gray, cutoff=1).filter(
        ImageFilter.UnsharpMask(radius=1.0, percent=140, threshold=2)
    )
    normalized_edges = normalized.filter(ImageFilter.FIND_EDGES)
    local_structure = ImageChops.difference(
        normalized.filter(ImageFilter.GaussianBlur(0.6)),
        normalized.filter(ImageFilter.GaussianBlur(2.2)),
    ).point(lambda value: min(255, value * 2))
    weak_values = list(ImageChops.lighter(normalized_edges, local_structure).getdata())
    interior_values = [
        value
        for index, value in enumerate(weak_values)
        if 2 <= (index % 48) < 46 and 2 <= (index // 48) < 22 and value > 0
    ]
    if interior_values:
        ordered = sorted(interior_values)
        percentile = ordered[round((len(ordered) - 1) * 0.65)]
        adaptive_threshold = max(
            ADAPTIVE_EDGE_FLOOR,
            min(ADAPTIVE_EDGE_CEILING, round(percentile * 0.75)),
        )
    else:
        adaptive_threshold = ADAPTIVE_EDGE_CEILING
    # FIND_EDGES treats the crop boundary as an edge. Exclude that artificial
    # frame so only graphics inside the sampled region can become the logo.
    return tuple(
        1
        if (
            2 <= (index % 48) < 46
            and 2 <= (index // 48) < 22
            and (
                strong_values[index] >= EDGE_THRESHOLD
                or weak_values[index] >= adaptive_threshold
            )
        )
        else 0
        for index in range(len(strong_values))
    )


def _fuzzy_edge_match_count(
    reference: tuple[int, ...],
    edge_map: tuple[int, ...],
    *,
    width: int = 48,
    radius: int = FUZZY_EDGE_RADIUS,
) -> int:
    """Count reference edges present directly or one cell away."""
    height = len(reference) // width
    matched = 0
    for index, expected in enumerate(reference):
        if not expected:
            continue
        x, y = index % width, index // width
        found = False
        for yy in range(max(0, y - radius), min(height, y + radius + 1)):
            for xx in range(max(0, x - radius), min(width, x + radius + 1)):
                if edge_map[(yy * width) + xx]:
                    found = True
                    break
            if found:
                break
        matched += int(found)
    return matched


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
    if not (
        COUNTDOWN_MIN_PERSISTENT_EDGES
        <= persistent_count
        <= COUNTDOWN_MAX_PERSISTENT_EDGES
    ):
        return 0.0, None
    # Countdown digits can sit several cells away from the static label or
    # frame that anchors the overlay. Keep the neighborhood local to that
    # overlay, but wide enough to include adjacent changing digits.
    neighborhood = _expand_mask(persistent, radius=7)
    total_pulses = [
        sum(1 for left, right in zip(previous, current) if left != right)
        for previous, current in zip(frames, frames[1:])
    ]
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
    active_concentrations = [
        nearby / total
        for nearby, total in zip(pulses, total_pulses)
        if total > 0
    ]
    concentration = (
        sum(active_concentrations) / len(active_concentrations)
        if active_concentrations else 0.0
    )
    # Periodicity is deliberately dominant: a static logo may have excellent
    # structure, but it must never look like a counting clock. Motion must also
    # be concentrated around that structure; ordinary scene cuts change the
    # entire corner and caused the old even/odd pulse test to false-trigger.
    confidence = (
        0.15 * structure
        + 0.45 * periodicity
        + 0.20 * pulse_strength
        + 0.20 * concentration
    )
    if pulse_strength < 0.25 or periodicity < 0.40 or concentration < 0.55:
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
    channel_bug_mode: str = "unknown"
    cut_density: float = 0.0
    mean_color_change: float = 0.0
    edge_density: float = 0.0
    mean_brightness: float = 0.0
    mean_saturation: float = 0.0
    logo_match_confidence: float = 0.0
    bug_identity_confidence: float = 0.0
    channel_model_score: float = 0.0
    channel_model_ready: bool = False
    signature_match_confidence: float = 0.0
    signature_authority: float = 0.0
    signature_id: int = 0
    signature_ids: tuple[int, ...] = ()
    signature_occurrences: int = 0
    classified_commercial_count: int = 0
    probable_commercial_candidate_count: int = 0
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
    _session_promotion_candidates: dict[str, dict] = field(default_factory=dict)
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
    _countdown_idle_count: int = 0
    _frames_observed: int = 0
    _bugless_rescan_after_frame: int | None = None
    _false_logo_trigger_corrections: int = 0
    _bug_promotion_resume_monotonic: float = 0.0
    _bug_scan_previous_histogram: tuple[float, ...] | None = None
    _missing_count: int = 0
    _present_count: int = 0
    _return_candidate_key: str = ""
    _secondary_missing_count: int = 0
    _local_candidate_count: int = 0
    _previous_color_histogram: tuple[float, ...] | None = None
    _color_changes: list[float] = field(default_factory=list)
    _program_color_changes: list[float] = field(default_factory=list)
    _commercial_reason: str = ""
    _commercial_entry_reason: str = ""
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
    _fingerprint_frame_count: int = 0
    _fingerprint_history: list[commercial_signatures.Fingerprint] = field(default_factory=list)
    _signature_episode_points: list[commercial_signatures.Fingerprint] = field(default_factory=list)
    _signature_event_user_confirmed: bool = False
    _signature_match_until_monotonic: float = 0.0
    _signature_stats_frame_count: int = 0
    _inspection_archive_directory: Path | None = None
    _current_analysis_frame: Path | None = None
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

    def set_inspection_archive(
        self,
        directory: Path | None,
    ) -> None:
        target = Path(directory) if directory else None
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
        self._inspection_archive_directory = target

    def _archive_decision_frame(
        self,
        observation: dict,
        *,
        label: str,
        source: str,
        features: dict[str, float],
        detector_state: str,
        commercial_reason: str,
    ) -> None:
        target = self._inspection_archive_directory
        path = self._current_analysis_frame
        observation_id = int(observation.get("id") or 0)
        if target is None or path is None or not path.is_file() or observation_id <= 0:
            return
        timestamp = re.sub(r"[^0-9A-Za-z]+", "", str(observation.get("observed_at") or ""))
        stem = f"observation-{observation_id:09d}-{timestamp or 'unknown'}"
        shutil.copyfile(path, target / f"{stem}.jpg")
        (target / f"{stem}.json").write_text(
            json.dumps({
                **observation,
                "label": label,
                "source": source,
                "detector_state": detector_state,
                "commercial_reason": commercial_reason,
                "features": features,
            }, indent=2),
            encoding="utf-8",
        )

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
            if self._trusted_bugs:
                self.channel_bug_mode = "bugged"
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

    def _trusted_return_gate(self, matched_key: str) -> tuple[float, int]:
        """Relax the return score only after a bug has substantial channel history.

        Faint, translucent, monochrome, or compressed bugs can have a lower
        absolute edge-map score even when their identity is stable. More
        persistence lowers the required score, while proportionally increasing
        the consecutive evidence needed so a briefly similar advertisement
        graphic cannot end the break.
        """
        if not matched_key.startswith("trusted:"):
            return PROGRAM_RETURN_MIN_CONFIDENCE, PROGRAM_RETURN_EVIDENCE_TARGET
        try:
            index = int(matched_key.rsplit(":", 1)[1])
            observed_ticks = int(self._trusted_bugs[index].get("observed_ticks") or 0)
        except (IndexError, TypeError, ValueError):
            observed_ticks = 0
        trust = min(1.0, observed_ticks / PROGRAM_RETURN_FULL_TRUST_TICKS)
        threshold = PROGRAM_RETURN_MIN_CONFIDENCE - trust * (
            PROGRAM_RETURN_MIN_CONFIDENCE - PROGRAM_RETURN_ADAPTIVE_FLOOR
        )
        extra_evidence = round(
            (PROGRAM_RETURN_MIN_CONFIDENCE - threshold) / PROGRAM_RETURN_ADAPTIVE_STEP
        )
        return threshold, PROGRAM_RETURN_EVIDENCE_TARGET + extra_evidence

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
        reference_coordinates = [
            (index % 48, index // 48)
            for index, expected in enumerate(reference)
            if expected
        ]
        reference_count = len(reference_coordinates)
        if not reference_count:
            return 0.0
        best_direct = 0.0
        best_delta_x = 0
        best_delta_y = 0
        for delta_y in range(-BUG_RELOCATION_SHIFT_Y, BUG_RELOCATION_SHIFT_Y + 1):
            for delta_x in range(-BUG_RELOCATION_SHIFT_X, BUG_RELOCATION_SHIFT_X + 1):
                direct_matched = 0
                for reference_x, reference_y in reference_coordinates:
                    x = reference_x + delta_x
                    y = reference_y + delta_y
                    if 0 <= x < 48 and 0 <= y < 24 and edge_map[(y * 48) + x]:
                        direct_matched += 1
                direct_score = direct_matched / reference_count
                if direct_score > best_direct:
                    best_direct = direct_score
                    best_delta_x = delta_x
                    best_delta_y = delta_y
        fuzzy_matched = 0
        for reference_x, reference_y in reference_coordinates:
            x = reference_x + best_delta_x
            y = reference_y + best_delta_y
            fuzzy_matched += int(any(
                edge_map[(yy * 48) + xx]
                for yy in range(max(0, y - FUZZY_EDGE_RADIUS), min(24, y + FUZZY_EDGE_RADIUS + 1))
                for xx in range(max(0, x - FUZZY_EDGE_RADIUS), min(48, x + FUZZY_EDGE_RADIUS + 1))
            ))
        fuzzy_score = fuzzy_matched / reference_count
        recall_score = (0.70 * best_direct) + (0.30 * fuzzy_score)

        shifted_reference = {
            (reference_x + best_delta_x, reference_y + best_delta_y)
            for reference_x, reference_y in reference_coordinates
            if 0 <= reference_x + best_delta_x < 48
            and 0 <= reference_y + best_delta_y < 24
        }
        if not shifted_reference:
            return 0.0
        min_x = max(0, min(x for x, _y in shifted_reference) - 2)
        max_x = min(47, max(x for x, _y in shifted_reference) + 2)
        min_y = max(0, min(y for _x, y in shifted_reference) - 2)
        max_y = min(23, max(y for _x, y in shifted_reference) + 2)
        actual_in_box = {
            (index % 48, index // 48)
            for index, value in enumerate(edge_map)
            if value
            and min_x <= (index % 48) <= max_x
            and min_y <= (index // 48) <= max_y
        }
        precision_score = (
            len(actual_in_box & shifted_reference) / len(actual_in_box)
            if actual_in_box else 0.0
        )
        # Recall keeps translucent edges tolerant of the changing picture;
        # precision prevents a dense ad frame from satisfying every sparse
        # reference edge merely by chance.
        return recall_score * precision_score

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

    def _track_session_bug_promotions(
        self,
        maps: dict[str, tuple[int, ...]],
        *,
        stable_program: bool,
        scene_change: bool = False,
    ) -> None:
        """Promote conservative cold-start bug candidates.

        A candidate must keep the same visual identity for ninety seconds and
        survive several independent scene changes. Only one candidate is
        promoted at a time; the remaining regions must earn their own evidence
        window instead of all self-confirming from the same rolling composite.
        """
        if (
            not stable_program
            or time.monotonic() < self._bug_promotion_resume_monotonic
            or len(self._trusted_bugs) >= MAX_SESSION_TRUSTED_BUGS
        ):
            self._session_promotion_candidates.clear()
            return
        active_regions = set(self._references)
        for region in tuple(self._session_promotion_candidates):
            if region not in active_regions:
                self._session_promotion_candidates.pop(region, None)
        eligible: list[tuple[float, int, str, tuple[int, ...], int]] = []
        for region, reference in self._references.items():
            if sum(reference) < MIN_PERSISTENT_EDGES:
                self._session_promotion_candidates.pop(region, None)
                continue
            already_trusted = any(
                min(
                    self._translated_match_score(tuple(bug.get("fingerprint") or ()), reference),
                    self._translated_match_score(reference, tuple(bug.get("fingerprint") or ())),
                ) >= commercial_profiles.TRUSTED_BUG_MATCH_THRESHOLD
                for bug in self._trusted_bugs
            )
            if already_trusted:
                self._session_promotion_candidates.pop(region, None)
                continue
            current = tuple(maps.get(region) or ())
            visual_match = self._translated_match_score(reference, current)
            if visual_match < 0.45:
                self._session_promotion_candidates.pop(region, None)
                continue
            candidate = self._session_promotion_candidates.get(region)
            previous_reference = tuple((candidate or {}).get("reference") or ())
            identity_match = (
                min(
                    self._translated_match_score(previous_reference, reference),
                    self._translated_match_score(reference, previous_reference),
                )
                if previous_reference else 1.0
            )
            if candidate and identity_match < 0.60:
                candidate = None
            ticks = int((candidate or {}).get("ticks") or 0) + 1
            scene_changes = int((candidate or {}).get("scene_changes") or 0)
            last_scene_frame = int((candidate or {}).get("last_scene_frame") or -10_000)
            if (
                scene_change
                and self._frames_observed - last_scene_frame
                >= BUG_PROMOTION_SCENE_GAP_TICKS
            ):
                scene_changes += 1
                last_scene_frame = self._frames_observed
            visual_total = float((candidate or {}).get("visual_total") or 0.0) + visual_match
            self._session_promotion_candidates[region] = {
                "reference": reference,
                "ticks": ticks,
                "visual_match": visual_match,
                "visual_total": visual_total,
                "scene_changes": scene_changes,
                "last_scene_frame": last_scene_frame,
            }
            if (
                ticks >= BUG_PROMOTION_TICKS
                and scene_changes >= BUG_PROMOTION_MIN_SCENE_CHANGES
            ):
                eligible.append(
                    (
                        visual_total / max(1, ticks),
                        scene_changes,
                        region,
                        reference,
                        ticks,
                    )
                )
        if eligible:
            _visual, _scenes, region, reference, ticks = max(
                eligible,
                key=lambda item: (item[1], item[0], -sum(item[3])),
            )
            self._remember_trusted_bug(region, reference, observed_ticks=ticks)
            self.channel_bug_mode = "bugged"
            self._session_promotion_candidates.clear()

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
        corrected_false_logo = bool(
            was_commercial and self._commercial_reason == "logo-missing"
        )
        commercial_seconds = (
            max(0.0, time.monotonic() - self._commercial_started_monotonic)
            if was_commercial and self._commercial_started_monotonic is not None
            else 0.0
        )
        signature_result: dict = {}
        if was_commercial and commercial_seconds >= FALSE_POSITIVE_BUFFER_SECONDS:
            signature_result = self._finish_signature_episode()
        if (
            self.profile_db_path is not None
            and (self.signature_ids or self.signature_id)
            and self._commercial_reason == "known-ad"
            and commercial_seconds < FALSE_POSITIVE_BUFFER_SECONDS
        ):
            try:
                commercial_signatures.mark_false_positives(
                    self.profile_db_path,
                    self.signature_ids or (self.signature_id,),
                )
            except (OSError, sqlite3.Error):
                pass
        self._manual_program_until = datetime.now(timezone.utc) + timedelta(
            seconds=MANUAL_PROGRAM_HOLD_SECONDS
        )
        self._bug_promotion_resume_monotonic = (
            time.monotonic() + BUG_PROMOTION_COOLDOWN_SECONDS
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
        self.signature_match_confidence = 0.0
        self.signature_authority = 0.0
        self.signature_id = 0
        self.signature_ids = ()
        self._signature_match_until_monotonic = 0.0
        self._signature_episode_points.clear()
        self._signature_event_user_confirmed = False
        self.last_decision_at = _timestamp()
        if corrected_false_logo and not self._trusted_bugs:
            self._false_logo_trigger_corrections += 1
            self._update_channel_bug_mode()
        if was_commercial:
            if self.profile_db_path is not None and self._commercial_event_id:
                try:
                    commercial_profiles.finish_commercial_episode(
                        self.profile_db_path,
                        self.channel_identity,
                        self._commercial_event_id,
                        exit_reason="manual-program",
                        features=self._profile_features(),
                        signature_ids=signature_result.get("signature_ids", ()),
                        signature_windows=int(signature_result.get("windows") or 0),
                        user_confirmed=bool(signature_result.get("user_confirmed")),
                        short_false_positive=(
                            commercial_seconds < FALSE_POSITIVE_BUFFER_SECONDS
                        ),
                    )
                except (OSError, sqlite3.Error):
                    pass
            self._commercial_event_id = ""
            self._commercial_started_monotonic = None
            self._commercial_entry_reason = ""
            self.callback(False)

    def _update_channel_bug_mode(self) -> None:
        """Classify whether this stream has a dependable broadcast bug."""
        if self.sports_generated or self._trusted_bugs:
            self.channel_bug_mode = "bugged"
            self._bugless_rescan_after_frame = None
            return
        if self.channel_bug_mode == "bugless":
            return
        if (
            self._frames_observed < BUGLESS_CLASSIFICATION_FRAMES
            and self._false_logo_trigger_corrections < BUGLESS_FALSE_LOGO_TRIGGER_LIMIT
        ):
            return
        self.channel_bug_mode = "bugless"
        self._bugless_rescan_after_frame = (
            self._frames_observed + BUGLESS_RESCAN_DELAY_FRAMES
        )
        self._references.clear()
        self._samples = {name: [] for name in REGION_NAMES}
        self._active_bug_key = ""
        self._active_bug_ticks = 0
        self._promotion_bug_key = ""
        self._promotion_bug_ticks = 0
        self._session_promotion_candidates.clear()
        self._missing_count = 0
        if self.last_commercial is True and self._commercial_reason == "logo-missing":
            # A break already in progress on the bug-confidence gate would
            # otherwise wait forever on a threshold this channel has just been
            # declared unable to reliably clear. Drop the reason so the next
            # tick re-derives it from a signal bugless mode actually trusts.
            self._commercial_reason = ""
            self._present_count = 0
            self._return_candidate_key = ""

    def _bugless_bug_scan_ready(self) -> bool:
        """Keep bugless mode reversible without blocking countdown analysis."""
        if self.channel_bug_mode != "bugless":
            return True
        if self._bugless_rescan_after_frame is None:
            self._bugless_rescan_after_frame = (
                self._frames_observed + BUGLESS_RESCAN_DELAY_FRAMES
            )
        return self._frames_observed >= self._bugless_rescan_after_frame

    def apply_commercial_feedback(self) -> bool:
        """Give the current fingerprint episode an immediate promotion weight."""
        if self.last_commercial is not True:
            return False
        self._signature_event_user_confirmed = True
        return True

    def _refresh_signature_stats(self) -> None:
        if self.profile_db_path is None:
            return
        try:
            stats = commercial_signatures.library_stats(
                self.profile_db_path,
                self.channel_identity,
            )
        except (OSError, sqlite3.Error):
            return
        self.classified_commercial_count = int(stats.get("classified") or 0)
        self.probable_commercial_candidate_count = int(stats.get("candidates") or 0)

    def _sample_ad_fingerprint(self, fingerprint: commercial_signatures.Fingerprint) -> None:
        if self.profile_db_path is None:
            return
        self._fingerprint_frame_count += 1
        if self._fingerprint_frame_count % max(
            1,
            int(commercial_signatures.SAMPLE_INTERVAL_SECONDS / POLL_SECONDS),
        ):
            return
        self._fingerprint_history.append(fingerprint)
        del self._fingerprint_history[:-40]
        if self.last_commercial is True:
            self._signature_episode_points.append(fingerprint)
            del self._signature_episode_points[:-600]

        try:
            matched = commercial_signatures.match_live(
                self.profile_db_path,
                self.channel_identity,
                self._fingerprint_history,
            )
        except (OSError, sqlite3.Error):
            matched = {"matched": False, "score": 0.0}
        now = time.monotonic()
        if matched.get("matched"):
            self.signature_match_confidence = float(matched.get("score") or 0)
            self.signature_authority = float(matched.get("authority") or 0)
            self.signature_id = int(matched.get("signature_id") or 0)
            self.signature_ids = tuple(
                int(value)
                for value in matched.get("signature_ids", ())
                if int(value or 0) > 0
            ) or ((self.signature_id,) if self.signature_id else ())
            self.signature_occurrences = int(matched.get("occurrence_count") or 0)
            self._signature_match_until_monotonic = now + max(
                0.75,
                float(matched.get("seconds_remaining") or 0) + 0.75,
            )
        elif now >= self._signature_match_until_monotonic:
            self.signature_match_confidence = 0.0
            self.signature_authority = 0.0
            self.signature_id = 0
            self.signature_ids = ()
            self.signature_occurrences = 0

        self._signature_stats_frame_count += 1
        if self._signature_stats_frame_count == 1 or self._signature_stats_frame_count >= 60:
            self._signature_stats_frame_count = 0
            self._refresh_signature_stats()

    def _begin_signature_episode(self) -> None:
        self._signature_episode_points = list(self._fingerprint_history[-2:])
        self._signature_event_user_confirmed = False

    def _finish_signature_episode(self) -> dict:
        user_confirmed = bool(self._signature_event_user_confirmed)
        if self.profile_db_path is None or not self._commercial_event_id:
            self._signature_episode_points.clear()
            self._signature_event_user_confirmed = False
            return {"windows": 0, "signature_ids": [], "user_confirmed": user_confirmed}
        result: dict = {"windows": 0, "signature_ids": []}
        try:
            result = commercial_signatures.record_episode(
                self.profile_db_path,
                self.channel_identity,
                self._commercial_event_id,
                self._signature_episode_points,
                user_confirmed=user_confirmed,
                trigger_reason=self._commercial_entry_reason or self._commercial_reason,
            )
        except (OSError, sqlite3.Error):
            pass
        self._signature_episode_points.clear()
        self._signature_event_user_confirmed = False
        self._refresh_signature_stats()
        return {**result, "user_confirmed": user_confirmed}

    def _close_episode_ledger(
        self,
        exit_reason: str,
        *,
        keep_signatures: bool = True,
    ) -> None:
        """Close non-standard exits that bypass the normal transition path."""
        if (
            self.last_commercial is not True
            or self.profile_db_path is None
            or not self._commercial_event_id
        ):
            return
        duration = (
            max(0.0, time.monotonic() - self._commercial_started_monotonic)
            if self._commercial_started_monotonic is not None
            else 0.0
        )
        signature_result: dict = {}
        if keep_signatures and duration >= FALSE_POSITIVE_BUFFER_SECONDS:
            signature_result = self._finish_signature_episode()
        else:
            self._signature_episode_points.clear()
            self._signature_event_user_confirmed = False
        try:
            commercial_profiles.finish_commercial_episode(
                self.profile_db_path,
                self.channel_identity,
                self._commercial_event_id,
                exit_reason=exit_reason,
                features=self._profile_features(),
                signature_ids=signature_result.get("signature_ids", ()),
                signature_windows=int(signature_result.get("windows") or 0),
                user_confirmed=bool(signature_result.get("user_confirmed")),
                short_false_positive=duration < FALSE_POSITIVE_BUFFER_SECONDS,
            )
        except (OSError, sqlite3.Error):
            pass
        self._commercial_event_id = ""
        self._commercial_started_monotonic = None
        self._commercial_entry_reason = ""

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
        if self.last_commercial is None and (
            not raw_logo_missing or self.channel_bug_mode == "bugless"
        ):
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

        # A bugless channel has no logo/bug that can go missing, so
        # best_logo_score is pinned at 0 and this signal carries no
        # information for it. Let local_break_confidence carry detection
        # instead rather than reporting a permanently elevated primary score.
        self.primary_confidence = (
            0.0
            if self.channel_bug_mode == "bugless"
            else (
                0.80 * logo_presence_delta
                + 0.15 * duration_score
                + 0.05 * secondary_absence
            )
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
            and (not raw_logo_missing or self.channel_bug_mode == "bugless")
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
        recognition_support = max(
            0.0,
            min(1.0, self.signature_match_confidence * self.signature_authority),
        )
        self.commercial_confidence = 1.0 - (
            (1.0 - self.commercial_confidence) * (1.0 - recognition_support)
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
        missing_confirmations = _adaptive_missing_confirmations(
            self._channel_profile
        )
        if model_program_veto:
            # A channel-specific pattern already corrected as program must
            # persist for the full short-window boundary before it can hide
            # playback again.
            missing_confirmations = max(
                missing_confirmations,
                int(SHORT_FALSE_POSITIVE_SECONDS / POLL_SECONDS),
            )
        bug_override = (
            self.channel_bug_mode != "bugless"
            and raw_logo_missing
            and self._missing_count >= missing_confirmations
            and bool(self._reference_entries())
            and not self._bug_transition_grace()
            and not self._manual_program_hold()
        )
        commercial = self.last_commercial
        recognized_candidate = bool(
            recognition_support >= COMMERCIAL_THRESHOLD
            and not self._manual_program_hold()
        )
        if commercial is not True:
            self._present_count = 0
        if recognized_candidate:
            commercial = True
            self._commercial_reason = "known-ad"
        elif (
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
                returned = False
            elif self._commercial_reason == "local-layout":
                returned = not secondary_missing
            elif self._commercial_reason == "known-ad":
                returned = recognition_support < 0.30
            else:
                returned = self.color_volatility < 0.35
            required_return_confirmations = RETURN_CONFIRMATIONS
            if self._commercial_reason == "known-ad":
                required_return_confirmations = (
                    1 if best_logo_score >= BUG_RETURN_CONFIDENCE else 2
                )
            if self._commercial_reason == "logo-missing":
                return_threshold, adaptive_return_target = self._trusted_return_gate(
                    matched_key
                )
                trusted_program_return = bool(
                    not self.sports_generated
                    and matched_key.startswith("trusted:")
                    and best_logo_score >= return_threshold
                    and visual_logo_score >= return_threshold
                )
                # A broadcast may move the same bug between known layouts for
                # desk, weather, traffic, or field segments. Identity remains
                # authoritative; relocation detection has already confirmed a
                # new position before it can produce this trusted match.
                candidate_key = matched_key if trusted_program_return else ""
                if candidate_key:
                    evidence = (
                        PROGRAM_RETURN_STRONG_EVIDENCE
                        if best_logo_score >= BUG_RETURN_CONFIDENCE
                        and visual_logo_score >= STRONG_PROGRAM_GRAPHIC_CONFIDENCE
                        else PROGRAM_RETURN_WEAK_EVIDENCE
                    )
                    if candidate_key == self._return_candidate_key:
                        self._present_count += evidence
                    else:
                        # A different identity cannot inherit return confidence
                        # from the previous candidate. Position changes for the
                        # same trusted bug intentionally retain evidence.
                        self._return_candidate_key = candidate_key
                        self._present_count = evidence
                    required_return_confirmations = adaptive_return_target
                    returned = self._present_count >= required_return_confirmations
                elif (
                    matched_key
                    and not matched_key.startswith("trusted:")
                    and best_logo_score >= BUG_RETURN_CONFIDENCE
                ):
                    # A channel without a persisted trusted identity still gets
                    # the conservative legacy return path until its session bug
                    # has accumulated enough observations to be trusted.
                    session_key = f"{matched_key}:{matched_region}"
                    if session_key == self._return_candidate_key:
                        self._present_count += 1
                    else:
                        self._return_candidate_key = session_key
                        self._present_count = 1
                    required_return_confirmations = BUG_RETURN_CONFIRMATIONS
                    returned = self._present_count >= required_return_confirmations
                else:
                    # Commercial frames are visually chaotic. Let weak or
                    # mismatched frames drain accumulated return evidence, but
                    # do not let color/cut volatility veto a stable, exact bug
                    # identity once it has been reacquired.
                    self._present_count = max(
                        0,
                        self._present_count - PROGRAM_RETURN_MISMATCH_PENALTY,
                    )
                    if not self._present_count:
                        self._return_candidate_key = ""
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
            and (not raw_logo_missing or self.channel_bug_mode == "bugless")
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
        self._track_session_bug_promotions(
            maps,
            stable_program=bool(
                self.last_commercial is False
                and not raw_logo_missing
                and self.commercial_confidence < 0.30
            ),
            scene_change=bool(
                current_color_change is not None
                and current_color_change >= 0.18
            ),
        )
        if commercial is True and self.last_commercial is True:
            if self._commercial_reason in {"known-ad", "logo-missing"}:
                # A local-color episode becomes eligible for fingerprinting
                # only after an independent signal corroborates it.
                self._commercial_entry_reason = self._commercial_reason
            self._commercial_episode_frame_count += 1
            episode_seconds = self._commercial_episode_frame_count * POLL_SECONDS
            if episode_seconds >= FALSE_POSITIVE_BUFFER_SECONDS:
                self._commercial_episode_features.clear()
                self._commercial_episode_feedback_expired = True
            elif self._commercial_episode_frame_count % FALSE_POSITIVE_SAMPLE_FRAMES == 0:
                self._commercial_episode_features.append(dict(self._profile_features()))
        if commercial != self.last_commercial:
            self._bug_promotion_resume_monotonic = (
                time.monotonic() + BUG_PROMOTION_COOLDOWN_SECONDS
            )
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
                self._commercial_entry_reason = self._commercial_reason
                self._commercial_episode_frame_count = 0
                self._commercial_episode_features = [dict(self._profile_features())]
                self._commercial_episode_feedback_expired = False
                self._begin_signature_episode()
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
            signature_result: dict = {}
            if not commercial and not short_false_positive:
                signature_result = self._finish_signature_episode()
            elif not commercial:
                self._signature_episode_points.clear()
                self._signature_event_user_confirmed = False
            self._record_state_transition(
                commercial,
                next_state,
                exit_reason=(
                    "short-false-positive"
                    if short_false_positive
                    else "program-return"
                ),
                short_false_positive=short_false_positive,
                signature_result=signature_result,
            )
            if short_false_positive:
                self._record_short_false_positive(false_positive_duration)
            self.last_commercial = commercial
            self.state = next_state
            self.last_decision_at = _timestamp()
            self.callback(bool(commercial))
            if not commercial:
                self._return_candidate_key = ""
                self._commercial_event_id = ""
                self._commercial_entry_reason = ""
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
        self._close_episode_ledger("replacement-accepted")
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
        self._close_episode_ledger("safety-release", keep_signatures=False)
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
            self._close_episode_ledger(
                "program-boundary",
                keep_signatures=False,
            )
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
            features = self._profile_features()
            observation = commercial_profiles.record_with_metadata(
                self.profile_db_path,
                self.channel_identity,
                label=label,
                features=features,
                event_id=self._commercial_event_id if self.last_commercial is True else "",
                detector_state=self.state,
                commercial_reason=self._commercial_reason,
            )
            self._archive_decision_frame(
                observation,
                label=label,
                source="inferred",
                features=features,
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

    def _record_state_transition(
        self,
        commercial: bool,
        state: str,
        *,
        exit_reason: str = "program-return",
        short_false_positive: bool = False,
        signature_result: dict | None = None,
    ) -> None:
        if (
            self.sports_generated
            or self.last_commercial is None
            or not self.channel_identity
            or self.profile_db_path is None
        ):
            return
        try:
            label = "commercial" if commercial else "program"
            features = self._profile_features()
            observation = commercial_profiles.record_with_metadata(
                self.profile_db_path,
                self.channel_identity,
                label=label,
                source="state-transition",
                event_id=self._commercial_event_id,
                features=features,
                detector_state=state,
                commercial_reason=self._commercial_reason,
            )
            self._archive_decision_frame(
                observation,
                label=label,
                source="state-transition",
                features=features,
                detector_state=state,
                commercial_reason=self._commercial_reason,
            )
            if commercial:
                commercial_profiles.begin_commercial_episode(
                    self.profile_db_path,
                    self.channel_identity,
                    self._commercial_event_id,
                    entry_reason=self._commercial_entry_reason or self._commercial_reason,
                    features=features,
                )
            else:
                result = dict(signature_result or {})
                commercial_profiles.finish_commercial_episode(
                    self.profile_db_path,
                    self.channel_identity,
                    self._commercial_event_id,
                    exit_reason=exit_reason,
                    features=features,
                    signature_ids=result.get("signature_ids", ()),
                    signature_windows=int(result.get("windows") or 0),
                    user_confirmed=bool(result.get("user_confirmed")),
                    short_false_positive=short_false_positive,
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
        """Observe all four corners and return whether countdown owns state.

        Countdown detection is a temporary fallback for a channel already
        proven bugless. It must relinquish control when a trusted bug appears,
        when the fallback is no longer eligible, or after the learned overlay
        has remained absent. This keeps normal bug acquisition alive.
        """
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
            self._relinquish_countdown_mode()
            return False
        if self.bugless_countdown_mode and (
            not fallback_allowed
            or self.channel_bug_mode != "bugless"
            or bool(self._trusted_bugs)
        ):
            self._relinquish_countdown_mode()
            return False
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
                self._countdown_idle_count = 0
                self._set_countdown_commercial(True)
            return self.bugless_countdown_mode

        # The learned clock disappearing is a faster return signal than
        # waiting for the full rolling periodicity window to drain.
        current = tuple(maps.get(self.countdown_region, ()))
        presence = self._translated_match_score(
            self._countdown_reference or (),
            current,
        )
        self.countdown_confidence = max(self.countdown_confidence, presence)
        if self.last_commercial is not True:
            self._countdown_candidate_count = (
                self._countdown_candidate_count + 1
                if presence >= COUNTDOWN_PRESENCE_THRESHOLD else 0
            )
            if presence >= COUNTDOWN_PRESENCE_THRESHOLD:
                self._countdown_idle_count = 0
            else:
                self._countdown_idle_count += 1
            if self._countdown_candidate_count >= COUNTDOWN_REENTRY_CONFIRMATIONS:
                self._countdown_missing_count = 0
                self._countdown_idle_count = 0
                self._set_countdown_commercial(True)
            elif self._countdown_idle_count >= COUNTDOWN_IDLE_RELEASE_FRAMES:
                self._relinquish_countdown_mode()
                return False
        elif presence >= COUNTDOWN_PRESENCE_THRESHOLD:
            self._countdown_candidate_count = 0
            self._countdown_missing_count = 0
            self._countdown_idle_count = 0
            self._set_countdown_commercial(True)
        else:
            self._countdown_missing_count += 1
            if self._countdown_missing_count >= COUNTDOWN_RELEASE_CONFIRMATIONS:
                self._countdown_candidate_count = 0
                self._countdown_idle_count = 0
                self._set_countdown_commercial(False)
        return True

    def _relinquish_countdown_mode(self) -> None:
        if self.last_commercial is True and self._commercial_reason == "countdown-clock":
            self._set_countdown_commercial(False)
        self.bugless_countdown_mode = False
        self.countdown_region = ""
        self.countdown_confidence = 0.0
        self._countdown_reference = None
        self._countdown_candidate_count = 0
        self._countdown_missing_count = 0
        self._countdown_idle_count = 0
        self._countdown_samples = {name: [] for name in COUNTDOWN_REGION_NAMES}

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
            self._commercial_entry_reason = "countdown-clock"
            self._signature_event_user_confirmed = False
            self._signature_episode_points.clear()
            self._bug_promotion_resume_monotonic = (
                time.monotonic() + BUG_PROMOTION_COOLDOWN_SECONDS
            )
        self._record_state_transition(
            commercial,
            next_state,
            exit_reason="countdown-ended",
        )
        if not commercial:
            # Countdown/filler slates are break markers, not reusable ads.
            self._signature_episode_points.clear()
            self._signature_event_user_confirmed = False
            self._bug_promotion_resume_monotonic = (
                time.monotonic() + BUG_PROMOTION_COOLDOWN_SECONDS
            )
        self.last_commercial = commercial
        self.state = next_state
        self.last_decision_at = _timestamp()
        self.callback(commercial)
        if not commercial:
            self._commercial_event_id = ""
            self._commercial_entry_reason = ""
            self._commercial_started_monotonic = None

    def _process(self, path: Path) -> None:
        self._current_analysis_frame = path
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
            ad_fingerprint = commercial_signatures.fingerprint_image(
                image,
                color_histogram,
            )
        self._sample_ad_fingerprint(ad_fingerprint)
        self.edge_density = sum(sum(edge_map) for edge_map in maps.values()) / float(
            len(maps) * len(next(iter(maps.values())))
        )
        self._frames_observed += 1
        self._load_trusted_bugs()
        self._update_channel_bug_mode()
        countdown_owns_state = bool(
            not self.sports_generated and self._update_countdown_detector(
            maps,
            fallback_allowed=bool(
                self._frames_observed >= COUNTDOWN_FALLBACK_PROBATION_FRAMES
                and self.channel_bug_mode == "bugless"
                and not self._trusted_bugs
            ),
            )
        )
        if countdown_owns_state:
            # Once the countdown overlay disappears, continue learning the
            # ordinary five bug regions. A real bug that survives multiple
            # scene changes immediately restores the normal classifier.
            if self.last_commercial is not True and self._bugless_bug_scan_ready():
                scene_change = bool(
                    self._bug_scan_previous_histogram is not None
                    and _distribution_distance(
                        self._bug_scan_previous_histogram,
                        color_histogram,
                    ) >= 0.18
                )
                self._bug_scan_previous_histogram = color_histogram
                self._refresh_references(maps)
                self._track_session_bug_promotions(
                    maps,
                    stable_program=True,
                    scene_change=scene_change,
                )
                if self._trusted_bugs:
                    self.channel_bug_mode = "bugged"
                    self._relinquish_countdown_mode()
                    countdown_owns_state = False
            if countdown_owns_state:
                self._sample_channel_profile()
                return
        # Bootstrap from the first rolling window. Once a credible broadcast
        # graphic exists, classify the frame against that trusted evidence
        # before allowing it into the learning bank. This prevents persistent
        # commercial graphics (for example, a prescription-drug name) from
        # being learned as a replacement network bug.
        if self.channel_bug_mode == "bugless":
            # "Bugless" is a fallback, not a terminal classification. Give the
            # suspected false graphic ten seconds to disappear, then keep the
            # normal five-region learner awake so a returning station bug can
            # promote itself and restore bug-based decisions.
            if self._bugless_bug_scan_ready():
                self._refresh_references(maps)
            self._observe(maps, scoreboard_maps, color_histogram)
        elif not self._reference_entries():
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
            "channel_bug_mode": self.channel_bug_mode,
            "bug_rescan_seconds_remaining": round(
                max(
                    0,
                    (
                        (self._bugless_rescan_after_frame or self._frames_observed)
                        - self._frames_observed
                    ) * POLL_SECONDS,
                ),
                1,
            ),
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
                or self.channel_bug_mode == "bugless"
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
            "signature_match_confidence": round(self.signature_match_confidence * 100, 1),
            "signature_authority": round(self.signature_authority * 100, 1),
            "signature_id": self.signature_id,
            "signature_occurrences": self.signature_occurrences,
            "classified_commercial_count": self.classified_commercial_count,
            "probable_commercial_candidate_count": self.probable_commercial_candidate_count,
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
        self._close_episode_ledger("stream-ended")
        self._stop.set()
        shutil.rmtree(self.directory, ignore_errors=True)
