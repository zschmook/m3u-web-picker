from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Callable

MAX_MALFORMED_SAMPLES = 10
XMLTV_GENERATOR_NAME = "M3U Web Picker Sports Automation"
GUIDE_PREGAME_HOURS = 24
GUIDE_POSTGAME_HOURS = 2
SPORTS_DISABLED_CACHE_HOURS = 24
SPORTS_DISABLED_AT_KEY = "__sports_disabled_at"
SPORTS_INTERVAL_ANCHOR_KEY = "__sports_interval_anchor_at"
SCHEDULE_MODES = {"daily", "interval"}
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24
ESTIMATED_EVENT_HOURS = {
    "mlb": 4, "milb": 4, "ncaa-baseball": 4, "international-baseball": 4,
    "nfl": 4, "ncaaf-fbs": 4, "ncaaf-fcs": 4, "ncaaf-d2": 4,
    "ncaaf-d3": 4, "naia-football": 4, "njcaa-football": 4,
    "high-school-football": 4, "nba": 3, "wnba": 3, "nba-g-league": 3,
    "ncaab-men": 3, "ncaab-women": 3, "international-basketball": 3,
    "nhl": 3, "ahl": 3, "ncaa-hockey": 3, "international-hockey": 3,
    "mls": 3, "nwsl": 3, "premier-league": 3, "la-liga": 3,
    "uefa-champions-league": 3, "international-soccer": 3,
    "cricket-test": 8, "cricket-odi": 8, "cricket-t20": 5,
    "cricket-ipl": 5, "cricket-domestic": 8,
    "rugby-union-international": 3, "rugby-union-club": 3,
    "rugby-league-nrl": 3, "rugby-league-super": 3, "rugby-league-origin": 3,
    "poker": 8, "wsop": 8, "wpt": 8, "ept": 8,
    "golf": 8, "pga-tour": 8, "lpga-tour": 8, "liv-golf": 8,
    "dp-world-tour": 8, "golf-majors": 8,
    "cycling": 6, "tour-de-france": 6, "giro-ditalia": 6,
    "vuelta-espana": 6, "tour-california": 6,
}


class MalformedSportsEntry(ValueError):
    """A provider entry contains bad event data and may be skipped safely."""


class ScanCancelled(RuntimeError):
    """A manual sports scan was cancelled at a safe checkpoint."""


CancelCheck = Callable[[], bool] | None
EVENT_END_GRACE = timedelta(minutes=90)
EVENT_MERGE_TOLERANCE = timedelta(minutes=90)
REPLAY_ATTACH_WINDOW = timedelta(hours=24)
LOGICAL_EVENT_DAY_ROLLOVER_HOUR = 12
MAX_ESTIMATED_EVENT_DURATION = timedelta(
    hours=max(ESTIMATED_EVENT_HOURS.values(), default=8)
)


def _raise_if_cancelled(cancel_check: CancelCheck) -> None:
    if cancel_check and cancel_check():
        raise ScanCancelled(
            "Sports update cancelled. Existing sports channels were kept."
        )


def _new_scan_diagnostics() -> dict:
    return {"malformed_m3u": 0, "malformed_epg": 0, "samples": []}


def _record_malformed_entry(
    diagnostics: dict,
    *,
    source: str,
    label: str,
    exc: Exception,
) -> None:
    key = f"malformed_{source}"
    diagnostics[key] = int(diagnostics.get(key, 0)) + 1
    samples = diagnostics.setdefault("samples", [])
    if len(samples) < MAX_MALFORMED_SAMPLES:
        clean_label = re.sub(r"\s+", " ", str(label or "unnamed entry")).strip()
        samples.append(
            {
                "source": source.upper(),
                "label": clean_label[:180],
                "error": f"{type(exc).__name__}: {exc}"[:240],
            }
        )


def _malformed_count(diagnostics: dict) -> int:
    return int(diagnostics.get("malformed_m3u", 0)) + int(
        diagnostics.get("malformed_epg", 0)
    )


def _log_malformed_summary(diagnostics: dict) -> None:
    count = _malformed_count(diagnostics)
    if not count:
        return
    print(
        f"Sports scan skipped {count} malformed provider "
        f"entr{'y' if count == 1 else 'ies'}."
    )
    for sample in diagnostics.get("samples", []):
        print(
            "  - "
            f"{sample.get('source', 'SOURCE')} entry "
            f"{sample.get('label', 'unnamed entry')!r}: "
            f"{sample.get('error', 'invalid data')}"
        )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clock_text(value: datetime) -> str:
    """Portable 12-hour time without a leading zero."""
    return value.strftime("%I:%M %p").lstrip("0")


def _schedule_text(value: datetime) -> str:
    """Portable long local date/time used in generated guide descriptions."""
    return f"{value.strftime('%A, %B')} {value.day} at {_clock_text(value)} {value.strftime('%Z')}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _normalize(value: str) -> str:
    value = value.replace("&", " and ").replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _smart_team_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip(" |:-"))
    if not value:
        return value
    if value.isupper():
        value = value.title()
    replacements = {
        "76Ers": "76ers", "Fc": "FC", "Sc": "SC", "Ucla": "UCLA",
        "Usc": "USC", "Lsu": "LSU", "Smu": "SMU",
    }
    for before, after in replacements.items():
        value = re.sub(rf"\b{re.escape(before)}\b", after, value)
    return value


def _json_load(value: str, fallback):
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _is_sd_channel(channel: dict) -> bool:
    group = str(channel.get("group", "") or "").strip().upper()
    name = str(channel.get("name", "") or "").strip()
    return group == "LOW BANDWIDTH" or bool(
        re.search(r"(?:^|[ |_-])SD(?:$|[ |_-])", name, re.I)
    )
