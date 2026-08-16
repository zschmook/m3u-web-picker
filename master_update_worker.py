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


def _redacted_exception_text(exc: BaseException) -> str:
    try:
        return core.redact_url_credentials(str(exc))
    except Exception:
        return str(exc)


def _failure_detail(exc: BaseException) -> str:
    """Return the browser-safe failure plus the deepest chained root cause."""
    message = _redacted_exception_text(exc)
    cause = exc.__cause__ or exc.__context__
    seen: set[int] = {id(exc)}
    deepest = cause
    while deepest is not None and id(deepest) not in seen:
        seen.add(id(deepest))
        next_cause = deepest.__cause__ or deepest.__context__
        if next_cause is None or id(next_cause) in seen:
            break
        deepest = next_cause

    if deepest is None:
        return message
    detail = _redacted_exception_text(deepest)
    return f"{message} Root cause: {type(deepest).__name__}: {detail}"


def _run(trigger: str) -> None:
    global _thread, _trigger, _started_at, _started_monotonic
    try:
        # Resolve the function through the core module at execution time.  The
        # dev runtime wraps core.run_master_update with Jellyfin cleanup and
        # update-reporting after modules are imported; this keeps those wrappers
        # in the background execution path.
        core.run_master_update(trigger=trigger)
    except Exception as exc:
        print(f"Background Master Update failed: {_failure_detail(exc)}")
    finally:
        with _lock:
            if _thread is threading.current_thread():
                _thread = None
                _trigger = None
                _started_at = None
                _started_monotonic = None


def start(*, trigger: str = "manual") -> tuple[bool, dict]:
    """Start exactly one background Master Update and return immediately."""
    global _thread, _trigger, _started_at, _started_monotonic

    clean_trigger = str(trigger or "manual").strip() or "manual"
    with _lock:
        if _thread_alive_unlocked() or core.master_update_payload().get("running"):
            return False, payload()

        _trigger = clean_trigger
        _started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        _started_monotonic = time.monotonic()
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
