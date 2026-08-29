from __future__ import annotations

import threading
from datetime import datetime


_LOCK = threading.RLock()
_STATE = {
    "active": False,
    "source": "idle",
    "started_at": None,
    "event_id": "",
    "break_duration_seconds": None,
    "logo_state": "idle",
    "last_logo_at": None,
}


def _now() -> datetime:
    return datetime.now().astimezone()


def payload() -> dict:
    with _LOCK:
        result = dict(_STATE)
    started_at = result.get("started_at")
    if result.get("active") and isinstance(started_at, datetime):
        result["elapsed_seconds"] = max(0, int((_now() - started_at).total_seconds()))
        result["started_at"] = started_at.isoformat(timespec="seconds")
    else:
        result["elapsed_seconds"] = 0
        result["started_at"] = None
    last_logo_at = result.get("last_logo_at")
    result["last_logo_at"] = (
        last_logo_at.isoformat(timespec="seconds")
        if isinstance(last_logo_at, datetime)
        else None
    )
    return result


def set_manual(active: bool) -> dict:
    with _LOCK:
        _STATE.update(
            active=bool(active),
            source="manual" if active else "idle",
            started_at=_now() if active else None,
            event_id="manual-test" if active else "",
            break_duration_seconds=None,
        )
    return payload()


def apply_logo_state(active: bool) -> dict:
    """Apply a broadcast-logo decision while preserving an active manual test."""
    observed_at = _now()
    with _LOCK:
        _STATE.update(
            logo_state="commercial" if active else "program",
            last_logo_at=observed_at,
        )
        if _STATE["source"] != "manual":
            already_active = bool(_STATE["active"]) and _STATE["source"] == "logo"
            _STATE.update(
                active=bool(active),
                source="logo" if active else "idle",
                started_at=(_STATE["started_at"] if already_active else observed_at) if active else None,
                event_id="logo-live" if active else "",
                break_duration_seconds=None,
            )
    return payload()


def clear_logo_state() -> dict:
    """Discard detector and manual state after the last analyzed stream closes."""
    with _LOCK:
        _STATE.update(
            active=False,
            source="idle",
            started_at=None,
            event_id="",
            break_duration_seconds=None,
            logo_state="idle",
            last_logo_at=None,
        )
    return payload()
