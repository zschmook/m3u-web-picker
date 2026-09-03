from __future__ import annotations

import threading
import time
from datetime import datetime

import core


_lock = threading.RLock()
_thread: threading.Thread | None = None
_trigger: str | None = None
_started_at: str | None = None
_started_monotonic: float | None = None
_last_completed_trigger: str | None = None
_last_completed_at: str | None = None
_last_error: str = ""


def _thread_alive_unlocked() -> bool:
    return bool(_thread and _thread.is_alive())


def payload() -> dict:
    """Return the authoritative live Master Update state.

    ``core.run_master_update`` owns the detailed update lifecycle.  There is a
    very small window between accepting a background job and the core function
    setting its own ``running`` flag, though.  Treat the worker thread itself as
    running during that window so a reload/navigation can never briefly report
    an idle update after the server already accepted the work.
    """
    result = dict(core.master_update_payload())
    with _lock:
        worker_alive = _thread_alive_unlocked()
        trigger = _trigger
        started_at = _started_at
        started_monotonic = _started_monotonic
        last_completed_trigger = _last_completed_trigger
        last_completed_at = _last_completed_at
        last_error = _last_error

    result["last_completed_trigger"] = last_completed_trigger
    result["last_completed_at"] = last_completed_at
    result["last_error"] = last_error

    if worker_alive and not result.get("running"):
        result["running"] = True
        result["started_at"] = result.get("started_at") or started_at
        result["trigger"] = result.get("trigger") or trigger
        if started_monotonic is not None:
            result["elapsed_seconds"] = max(
                0,
                int(time.monotonic() - float(started_monotonic)),
            )
        result["phase"] = "starting"
    else:
        result["phase"] = "running" if result.get("running") else "idle"
    return result


def _finish_onboarding_refresh(*, success: bool, error: str = "") -> None:
    """Persist whether the first-run guide is safe to expose to the user."""
    try:
        import onboarding

        onboarding.finish_initial_refresh(
            core.DB_PATH,
            provider_configured=True,
            success=success,
            error=error,
        )
    except Exception as exc:
        print(f"Could not finalize onboarding guide gate: {exc}")


def _demo_provider_requires_guide_matches() -> bool:
    """Demo onboarding promises a working guide, not merely a downloaded file."""
    try:
        primary = core.primary_provider_source() or {}
        name = str(primary.get("name", "") or "").strip()
        return bool(core.selected_ids) and name.endswith(" Demo")
    except Exception:
        return False


def _onboarding_guide_ready() -> tuple[bool, str]:
    """Require cached/filtered public EPG plus a published Combined XMLTV."""
    try:
        public_epg = core.public_epg_payload()
    except Exception as exc:
        return False, f"Could not inspect public EPG state: {exc}"

    enabled = [
        item
        for item in (public_epg.get("countries") or [])
        if item.get("enabled")
    ]
    missing = [
        str(item.get("code") or "public EPG")
        for item in enabled
        if not item.get("cached") or int(item.get("filtered_bytes") or 0) <= 0
    ]
    if missing:
        return False, f"Public EPG data was not ready for: {', '.join(missing)}."

    if _demo_provider_requires_guide_matches():
        empty = [
            str(item.get("code") or "public EPG")
            for item in enabled
            if int(item.get("filtered_channels") or 0) <= 0
            or int(item.get("filtered_programmes") or 0) <= 0
        ]
        if empty:
            return False, (
                "Public EPG downloaded, but no usable demo-channel guide data "
                f"matched for: {', '.join(empty)}."
            )

    try:
        combined_ready = core.COMBINED_EPG_PATH.exists() and core.COMBINED_EPG_PATH.stat().st_size > 0
    except Exception:
        combined_ready = False
    if not combined_ready:
        return False, "Combined XMLTV was not published by the first update."

    return True, ""


def _run(trigger: str) -> None:
    global _thread, _trigger, _started_at, _started_monotonic
    global _last_completed_trigger, _last_completed_at, _last_error
    onboarding_trigger = trigger == "onboarding"
    error_message = ""
    try:
        # Resolve the function through the core module at execution time.  The
        # dev runtime wraps core.run_master_update with Jellyfin cleanup and
        # update-reporting after modules are imported; this keeps those wrappers
        # in the background execution path.
        core.run_master_update(trigger=trigger)
        if onboarding_trigger:
            ready, error = _onboarding_guide_ready()
            _finish_onboarding_refresh(success=ready, error=error)
    except Exception as exc:
        try:
            message = core.redact_url_credentials(str(exc))
        except Exception:
            message = str(exc)
        error_message = message
        print(f"Background Master Update failed: {message}")
        if onboarding_trigger:
            _finish_onboarding_refresh(success=False, error=message)
    finally:
        with _lock:
            _last_completed_trigger = trigger
            _last_completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
            _last_error = error_message
            if _thread is threading.current_thread():
                _thread = None
                _trigger = None
                _started_at = None
                _started_monotonic = None


def start(*, trigger: str = "manual") -> tuple[bool, dict]:
    """Start exactly one background Master Update and return immediately."""
    global _thread, _trigger, _started_at, _started_monotonic, _last_error

    clean_trigger = str(trigger or "manual").strip() or "manual"
    with _lock:
        if _thread_alive_unlocked() or core.master_update_payload().get("running"):
            return False, payload()

        _trigger = clean_trigger
        _started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        _started_monotonic = time.monotonic()
        _last_error = ""
        worker = threading.Thread(
            target=_run,
            args=(clean_trigger,),
            daemon=True,
            name=f"m3u-master-update-{clean_trigger}",
        )
        _thread = worker
        try:
            worker.start()
        except Exception:
            _thread = None
            _trigger = None
            _started_at = None
            _started_monotonic = None
            raise

    return True, payload()


def wait_for_idle(timeout: float = 5.0) -> bool:
    """Test/support helper: wait for the current worker without starting work."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        with _lock:
            worker = _thread
        if worker is None or not worker.is_alive():
            return True
        worker.join(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
    return not payload().get("running")
