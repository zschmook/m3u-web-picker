from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


CAST_ROOT = Path(os.environ.get("M3U_CAST_HLS_DIR", "/tmp/m3u-web-picker-cast-hls"))
CAST_ROOT.mkdir(parents=True, exist_ok=True)

_LOCK = threading.RLock()
_SESSIONS: dict[str, "CastHlsSession"] = {}


@dataclass
class CastHlsSession:
    token: str
    directory: Path
    process: subprocess.Popen
    created_monotonic: float
    last_access_monotonic: float


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _remove_session_files(directory: Path) -> None:
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception:
        pass


def stop_session(token: str) -> bool:
    with _LOCK:
        session = _SESSIONS.pop(str(token or ""), None)
    if session is None:
        return False
    _terminate_process(session.process)
    _remove_session_files(session.directory)
    return True


def stop_all_sessions() -> int:
    with _LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in sessions:
        _terminate_process(session.process)
        _remove_session_files(session.directory)
    return len(sessions)


def _cleanup_dead_sessions() -> None:
    with _LOCK:
        dead = [token for token, session in _SESSIONS.items() if session.process.poll() is not None]
    for token in dead:
        stop_session(token)


def start_session(target: str, *, startup_timeout: float = 12.0) -> CastHlsSession:
    """Start one Cast-only HLS transcoder for a curated provider target.

    Browser playback deliberately remains on the separate fragmented-MP4 path.
    This path writes a short rolling HLS playlist with MPEG-TS segments because
    Cast receivers are much happier pulling discrete live segments than reading
    an endless fragmented-MP4 HTTP response.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Cast playback is unavailable because ffmpeg is not installed.")
    if not str(target or "").strip():
        raise RuntimeError("Curated stream was not found.")

    # Keep the currently playing Cast relay alive while the replacement HLS
    # stream warms up. The sender tears the old relay down only after the
    # receiver successfully loads the new playlist, avoiding a dead-air gap on
    # channel switches. Clean dead/old experimental sessions opportunistically.
    _cleanup_dead_sessions()
    with _LOCK:
        oldest = sorted(_SESSIONS.values(), key=lambda item: item.created_monotonic)[:-3]
    for old in oldest:
        stop_session(old.token)

    token = secrets.token_urlsafe(18)
    directory = CAST_ROOT / token
    directory.mkdir(parents=True, exist_ok=False)
    playlist_path = directory / "stream.m3u8"
    segment_pattern = directory / "segment_%06d.ts"

    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-i",
        target,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-force_key_frames",
        "expr:gte(t,n_forced*2)",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "8",
        "-hls_delete_threshold",
        "4",
        "-hls_allow_cache",
        "0",
        "-hls_segment_type",
        "mpegts",
        "-hls_flags",
        "delete_segments+append_list+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename",
        str(segment_pattern),
        str(playlist_path),
    ]

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        _remove_session_files(directory)
        raise RuntimeError(f"Could not start Cast ffmpeg: {exc}") from exc

    now = time.monotonic()
    session = CastHlsSession(
        token=token,
        directory=directory,
        process=process,
        created_monotonic=now,
        last_access_monotonic=now,
    )
    with _LOCK:
        _SESSIONS[token] = session

    deadline = time.monotonic() + max(1.0, startup_timeout)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session(token)
            raise RuntimeError("Cast ffmpeg stopped before the HLS stream became ready.")
        try:
            playlist = playlist_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            playlist = ""
        segments = list(directory.glob("segment_*.ts"))
        if "#EXTM3U" in playlist and playlist.count("#EXTINF") >= 2 and len(segments) >= 2:
            touch_session(token)
            return session
        time.sleep(0.10)

    stop_session(token)
    raise RuntimeError("Timed out waiting for the Cast HLS stream to become ready.")


def get_session(token: str) -> CastHlsSession | None:
    with _LOCK:
        session = _SESSIONS.get(str(token or ""))
    if session is None:
        return None
    if session.process.poll() is not None:
        stop_session(session.token)
        return None
    return session


def touch_session(token: str) -> CastHlsSession | None:
    session = get_session(token)
    if session is None:
        return None
    with _LOCK:
        current = _SESSIONS.get(session.token)
        if current is not None:
            current.last_access_monotonic = time.monotonic()
            return current
    return None


def safe_media_file(token: str, filename: str) -> Path | None:
    session = touch_session(token)
    if session is None:
        return None
    name = str(filename or "")
    if name == "stream.m3u8":
        path = session.directory / name
    elif name.startswith("segment_") and name.endswith(".ts") and name[8:-3].isdigit():
        path = session.directory / name
    else:
        return None
    try:
        path.resolve().relative_to(session.directory.resolve())
    except (OSError, ValueError):
        return None
    return path if path.exists() and path.is_file() else None
