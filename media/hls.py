from __future__ import annotations

import secrets
import shutil
import subprocess
import threading
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from settings import SETTINGS
from .ffmpeg import normalized_live_input_args, terminate
import media_pipeline


LOGGER = logging.getLogger(__name__)
HLS_ROOT = SETTINGS.cast_hls_dir
HLS_ROOT.mkdir(parents=True, exist_ok=True)
CAST_ROOT = HLS_ROOT
SEGMENT_SECONDS = 2
# Enough history for a 30-second entertainment cushion without affecting a
# sports receiver that explicitly joins four seconds behind the live edge.
PLAYLIST_SEGMENTS = 40

_LOCK = threading.RLock()
_SESSIONS: dict[str, "HlsSession"] = {}
_TARGETS: dict[str, str] = {}
_REFERENCES: dict[str, int] = {}


@dataclass
class HlsSession:
    token: str
    target: str
    directory: Path
    process: subprocess.Popen
    created_monotonic: float
    last_access_monotonic: float
    pipeline_token: str
    targets: tuple[str, ...] = ()
    target_index: int = 0
    recovery_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    stderr_handle: object | None = field(default=None, repr=False)
    stderr_path: Path | None = None
    recovery_count: int = 0
    last_error: str = ""
    on_target: Callable[[str | None], None] | None = field(default=None, repr=False)


# Backward-compatible name for callers from the original Chromecast-only experiment.
CastHlsSession = HlsSession


def _normalize_targets(targets: str | Iterable[str]) -> tuple[str, ...]:
    values = [targets] if isinstance(targets, str) else list(targets or [])
    return tuple(dict.fromkeys(str(value or "").strip() for value in values if str(value or "").strip()))


def _remove_session_files(directory: Path) -> None:
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception:
        pass


def _close_stderr(session: HlsSession) -> None:
    handle = session.stderr_handle
    session.stderr_handle = None
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass


def _stderr_tail(path: Path | None, *, limit: int = 12000) -> str:
    if path is None:
        return ""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _record_failure(session: HlsSession, prefix: str) -> None:
    _close_stderr(session)
    details = _stderr_tail(session.stderr_path)
    message = prefix if not details else f"{prefix}\n{details}"
    session.last_error = prefix
    LOGGER.warning("%s", prefix)
    media_pipeline.record_output_error("hls", message)


def _notify_target(session: HlsSession, target: str | None) -> None:
    callback = session.on_target
    if callback is None:
        return
    try:
        callback(target)
    except Exception:
        pass


def _mapped_targets(session: HlsSession) -> tuple[str, ...]:
    return session.targets or ((session.target,) if session.target else ())


def _force_stop_session(token: str) -> bool:
    with _LOCK:
        key = str(token or "")
        session = _SESSIONS.pop(key, None)
        _REFERENCES.pop(key, None)
        if session is not None:
            for target in _mapped_targets(session):
                if _TARGETS.get(target) == key:
                    _TARGETS.pop(target, None)
    if session is None:
        return False
    terminate(session.process)
    _close_stderr(session)
    media_pipeline.release_session(session.pipeline_token)
    _remove_session_files(session.directory)
    return True


def stop_session(token: str) -> bool:
    with _LOCK:
        key = str(token or "")
        session = _SESSIONS.get(key)
        if session is not None and _REFERENCES.get(key, 1) > 1:
            _REFERENCES[key] -= 1
            return True
    return _force_stop_session(str(token or ""))


def stop_all_sessions() -> int:
    with _LOCK:
        tokens = list(_SESSIONS)
        for token in tokens:
            _REFERENCES[token] = 1
    for token in tokens:
        _force_stop_session(token)
    return len(tokens)


def _next_segment_number(directory: Path) -> int:
    numbers = []
    for path in directory.glob("segment_*.ts"):
        try:
            numbers.append(int(path.stem.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return max(numbers, default=-1) + 1


def _playlist_snapshot(directory: Path) -> tuple[int, frozenset[str]]:
    playlist = directory / "stream.m3u8"
    try:
        modified = playlist.stat().st_mtime_ns
    except OSError:
        modified = 0
    return modified, frozenset(path.name for path in directory.glob("segment_*.ts"))


def buffered_seconds(directory: Path) -> float:
    """Count only completed, locally available media referenced by a playlist."""
    try:
        lines = (directory / "stream.m3u8").read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    total, duration = 0.0, 0.0
    for line in lines:
        if line.startswith("#EXTINF:"):
            try:
                duration = max(0.0, float(line.partition(":")[2].split(",", 1)[0]))
            except ValueError:
                duration = 0.0
        elif line and not line.startswith("#"):
            if line.startswith("segment_") and Path(line).name == line and (directory / line).is_file():
                total += duration
            duration = 0.0
    return total


def wait_for_buffer(directory: Path, process: subprocess.Popen, seconds: float, *, timeout: float = 40.0) -> bool:
    """Warm one existing encoder; never open a second source to fill a buffer."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if buffered_seconds(directory) >= seconds:
            return True
        if process.poll() is not None or time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _hls_command(target: str, directory: Path) -> list[str]:
    base_command = normalized_live_input_args(
        target,
        video_extra=("-force_key_frames", "expr:gte(t,n_forced*2)"),
        preserve_av_timing=True,
    )
    # Providers can send a buffered window immediately after reconnecting.
    # Consume it at playback speed so the rolling playlist does not run ahead
    # of the receiver and delete segments it has not downloaded yet.
    input_index = base_command.index("-i")
    base_command[input_index:input_index] = ["-re"]
    return base_command + [
        # Reconnects can drop AAC packets while the video clock keeps moving.
        # Fill missing samples and trim reconnect overlap. Hard compensation
        # keeps sample timestamps monotonic when the provider clock restarts;
        # soft stretching can replay padding and send AAC timestamps backward.
        "-af",
        "aresample=async=1:first_pts=0",
        "-f",
        "hls",
        "-hls_time",
        str(SEGMENT_SECONDS),
        "-hls_list_size",
        str(PLAYLIST_SEGMENTS),
        "-hls_delete_threshold",
        "4",
        "-hls_allow_cache",
        "0",
        "-hls_segment_type",
        "mpegts",
        "-start_number",
        str(_next_segment_number(directory)),
        "-hls_flags",
        "delete_segments+append_list+omit_endlist+independent_segments+temp_file+discont_start",
        "-hls_segment_filename",
        str(directory / "segment_%06d.ts"),
        str(directory / "stream.m3u8"),
    ]


def _spawn(session: HlsSession, target: str) -> subprocess.Popen:
    command = _hls_command(target, session.directory)
    stderr_path = session.directory / "ffmpeg.stderr.log"
    stderr_handle = stderr_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        stderr_handle.close()
        raise
    session.stderr_path = stderr_path
    session.stderr_handle = stderr_handle
    session.process = process
    return process


def _wait_ready(
    session: HlsSession,
    process: subprocess.Popen,
    previous: tuple[int, frozenset[str]],
    startup_timeout: float,
) -> bool:
    previous_mtime, previous_segments = previous
    playlist_path = session.directory / "stream.m3u8"
    deadline = time.monotonic() + max(1.0, startup_timeout)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            playlist = playlist_path.read_text(encoding="utf-8", errors="replace")
            modified = playlist_path.stat().st_mtime_ns
        except OSError:
            playlist = ""
            modified = 0
        segments = {path.name for path in session.directory.glob("segment_*.ts")}
        new_segments = segments.difference(previous_segments)
        if (
            modified > previous_mtime
            and "#EXTM3U" in playlist
            and playlist.count("#EXTINF") >= 2
            and len(new_segments) >= 2
        ):
            return True
        time.sleep(0.10)
    return False


def _try_target(session: HlsSession, index: int, startup_timeout: float) -> bool:
    target = session.targets[index]
    previous = _playlist_snapshot(session.directory)
    try:
        process = _spawn(session, target)
    except RuntimeError as exc:
        session.last_error = str(exc)
        return False
    except OSError as exc:
        session.last_error = f"Could not start Cast ffmpeg: {exc}"
        return False
    if _wait_ready(session, process, previous, startup_timeout):
        session.target = target
        session.target_index = index
        session.last_error = ""
        media_pipeline.clear_output_error("hls")
        return True
    terminate(process)
    _record_failure(session, f"HLS candidate {index + 1}/{len(session.targets)} stopped before becoming ready.")
    return False


def _recover_session(session: HlsSession, *, startup_timeout: float = 12.0) -> bool:
    with session.recovery_lock:
        with _LOCK:
            if _SESSIONS.get(session.token) is not session:
                return False
        if session.process.poll() is None:
            return True

        _record_failure(
            session,
            f"HLS source {session.target_index + 1}/{len(session.targets)} disconnected; attempting recovery.",
        )
        count = len(session.targets)
        order = [((session.target_index + offset) % count) for offset in range(1, count + 1)]
        for index in order:
            if _try_target(session, index, startup_timeout):
                session.recovery_count += 1
                session.last_access_monotonic = time.monotonic()
                LOGGER.info(
                    "HLS relay %s recovered with candidate %d/%d (recovery %d).",
                    session.token,
                    session.target_index + 1,
                    len(session.targets),
                    session.recovery_count,
                )
                _notify_target(session, session.target)
                return True

        _notify_target(session, None)
        _force_stop_session(session.token)
        return False


def start_session(
    targets: str | Iterable[str],
    *,
    startup_timeout: float = 12.0,
    on_target: Callable[[str | None], None] | None = None,
) -> HlsSession:
    """Start or share one resilient remote-playback HLS relay.

    The relay keeps one public HLS token while retrying a disconnected source
    and moving through ordered provider candidates. Browser playback remains on
    the separate fragmented-MP4 path.
    """
    candidates = _normalize_targets(targets)
    if not candidates:
        raise RuntimeError("Curated stream was not found.")

    shared_tokens: list[str] = []
    with _LOCK:
        for target in candidates:
            token = _TARGETS.get(target)
            if token and token not in shared_tokens:
                shared_tokens.append(token)
    for shared_token in shared_tokens:
        shared = get_session(shared_token)
        if shared is not None:
            with _LOCK:
                if _SESSIONS.get(shared.token) is shared:
                    _REFERENCES[shared.token] = _REFERENCES.get(shared.token, 1) + 1
                    shared.last_access_monotonic = time.monotonic()
                    if on_target is not None:
                        shared.on_target = on_target
                    return shared

    pipeline_token = media_pipeline.acquire_session("hls")
    token = secrets.token_urlsafe(18)
    directory = HLS_ROOT / token
    directory.mkdir(parents=True, exist_ok=False)
    now = time.monotonic()
    session = HlsSession(
        token=token,
        target=candidates[0],
        directory=directory,
        process=None,  # type: ignore[arg-type]
        created_monotonic=now,
        last_access_monotonic=now,
        pipeline_token=pipeline_token,
        targets=candidates,
        on_target=on_target,
    )

    # Keep the currently playing relay alive while a replacement warms up.
    with _LOCK:
        oldest = sorted(_SESSIONS.values(), key=lambda item: item.created_monotonic)[:-3]
    for old in oldest:
        stop_session(old.token)

    for index in range(len(candidates)):
        if _try_target(session, index, startup_timeout):
            with _LOCK:
                _SESSIONS[token] = session
                for target in candidates:
                    _TARGETS[target] = token
                _REFERENCES[token] = 1
            LOGGER.info(
                "HLS relay %s started with candidate %d/%d.",
                session.token,
                session.target_index + 1,
                len(session.targets),
            )
            _notify_target(session, session.target)
            return session

    media_pipeline.release_session(pipeline_token)
    _remove_session_files(directory)
    _notify_target(session, None)
    detail = session.last_error or "No provider candidate became ready."
    raise RuntimeError(f"Cast ffmpeg stopped before the HLS stream became ready. {detail}")


def get_session(token: str) -> HlsSession | None:
    with _LOCK:
        session = _SESSIONS.get(str(token or ""))
    if session is None:
        return None
    if session.process.poll() is not None and not _recover_session(session):
        return None
    return session


def touch_session(token: str) -> HlsSession | None:
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
