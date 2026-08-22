from __future__ import annotations

import subprocess
import threading
import time
import uuid
from typing import Any

import app_config


SECTION = "media_pipeline"
DEFAULTS = {
    "enabled": False,
    "warning_acknowledged": False,
    "encoder": "auto",
    "max_sessions": 2,
}
HARDWARE_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_vaapi")
CPU_ENCODER = "libx264"
_last_test: dict[str, Any] = {}
_session_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}


def settings() -> dict[str, Any]:
    saved = app_config.section(SECTION)
    result = {**DEFAULTS, **saved}
    result["enabled"] = bool(result["enabled"])
    result["warning_acknowledged"] = bool(result["warning_acknowledged"])
    result["encoder"] = str(result["encoder"] or "auto")
    result["max_sessions"] = max(1, min(16, int(result["max_sessions"] or 2)))
    return result


def _version(executable: str) -> str:
    result = subprocess.run(
        [executable, "-version"], capture_output=True, text=True, timeout=5, check=False
    )
    return (result.stdout or result.stderr or "").splitlines()[0].strip()


def _listed_encoders(executable: str) -> set[str]:
    result = subprocess.run(
        [executable, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    text = f"{result.stdout}\n{result.stderr}"
    return {name for name in (*HARDWARE_ENCODERS, CPU_ENCODER) if name in text}


def _test_encoder(executable: str, encoder: str) -> tuple[bool, str, float]:
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=854x480:r=30:d=0.2",
        "-frames:v", "3", "-an", "-c:v", encoder, "-f", "null", "-",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc), round(time.monotonic() - started, 3)
    detail = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, detail[-1000:], round(time.monotonic() - started, 3)


def capability_test() -> dict[str, Any]:
    global _last_test
    tested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        from media.ffmpeg import executable as ffmpeg_executable

        executable = ffmpeg_executable()
        version = _version(executable)
        listed = _listed_encoders(executable)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        _last_test = {
            "ok": False, "ffmpeg_available": False, "hardware_available": False,
            "active_encoder": "", "mode": "unavailable", "error": str(exc),
            "tested_at": tested_at,
        }
        return dict(_last_test)

    attempts = []
    working_hardware = ""
    for encoder in HARDWARE_ENCODERS:
        if encoder not in listed:
            continue
        ok, detail, duration = _test_encoder(executable, encoder)
        attempts.append({"encoder": encoder, "ok": ok, "duration_seconds": duration, "error": "" if ok else detail})
        if ok:
            working_hardware = encoder
            break

    cpu_ok, cpu_detail, cpu_duration = _test_encoder(executable, CPU_ENCODER)
    attempts.append({"encoder": CPU_ENCODER, "ok": cpu_ok, "duration_seconds": cpu_duration, "error": "" if cpu_ok else cpu_detail})
    active = working_hardware or (CPU_ENCODER if cpu_ok else "")
    _last_test = {
        "ok": bool(active), "ffmpeg_available": True,
        "hardware_available": bool(working_hardware),
        "active_encoder": active,
        "mode": "hardware" if working_hardware else ("cpu" if cpu_ok else "unavailable"),
        "ffmpeg_version": version, "listed_hardware_encoders": sorted(listed.intersection(HARDWARE_ENCODERS)),
        "attempts": attempts, "error": "" if active else "No usable H.264 encoder was found.",
        "tested_at": tested_at,
    }
    return dict(_last_test)


def status(*, run_test: bool = False) -> dict[str, Any]:
    current = settings()
    test = capability_test() if run_test else dict(_last_test)
    with _session_lock:
        sessions = [dict(item) for item in _sessions.values()]
    return {"settings": current, "capability": test, "runtime": {"active_sessions": len(sessions), "sessions": sessions}, "direct_playlist": "/playlist/channels.direct.m3u"}


def save(values: dict[str, Any]) -> dict[str, Any]:
    current = settings()
    for key in DEFAULTS:
        if key in values:
            current[key] = values[key]
    current["enabled"] = bool(current["enabled"])
    current["warning_acknowledged"] = bool(current["warning_acknowledged"])
    current["max_sessions"] = max(1, min(16, int(current["max_sessions"] or 2)))
    requested = str(current["encoder"] or "auto")
    if requested not in {"auto", CPU_ENCODER, *HARDWARE_ENCODERS}:
        raise ValueError("Choose a supported FFmpeg encoder.")
    current["encoder"] = requested
    if current["enabled"] and not current["warning_acknowledged"]:
        raise ValueError("Acknowledge the encoding performance warning before enabling FFmpeg.")
    if current["enabled"]:
        test = capability_test()
        if not test.get("ok"):
            raise ValueError(str(test.get("error") or "FFmpeg has no usable encoder."))
        if requested not in {"auto", test.get("active_encoder")}:
            matching = next((item for item in test.get("attempts", []) if item.get("encoder") == requested), None)
            if not matching or not matching.get("ok"):
                raise ValueError(f"The selected encoder {requested} failed its functional test.")
    return app_config.update_section(SECTION, current)


def active_encoder() -> str:
    current = settings()
    if not current["enabled"]:
        return CPU_ENCODER
    requested = current["encoder"]
    test = dict(_last_test) or capability_test()
    if requested == "auto":
        return str(test.get("active_encoder") or CPU_ENCODER)
    if requested == CPU_ENCODER:
        return CPU_ENCODER
    # A specific hardware encoder was requested, but that setting can outlive
    # the environment it was validated in (e.g. the container moves to a host
    # or compose file without GPU passthrough). Only honor it if the most
    # recent functional test actually confirmed it still works here; otherwise
    # ffmpeg would be launched with an encoder that fails to even initialize.
    attempt = next(
        (item for item in test.get("attempts", []) if item.get("encoder") == requested),
        None,
    )
    if attempt and attempt.get("ok"):
        return requested
    return CPU_ENCODER


def acquire_session(output: str) -> str:
    token = uuid.uuid4().hex
    with _session_lock:
        limit = settings()["max_sessions"]
        if len(_sessions) >= limit:
            raise RuntimeError(f"FFmpeg stream limit reached ({limit} active). Stop another stream or raise the limit in Settings → Encoding.")
        _sessions[token] = {"id": token, "output": str(output), "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return token


def release_session(token: str) -> None:
    with _session_lock:
        _sessions.pop(str(token or ""), None)
