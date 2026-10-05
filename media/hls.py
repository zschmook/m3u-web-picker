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
from .hls_manifest import HlsManifestNormalizer
import media_pipeline


LOGGER = logging.getLogger(__name__)
HLS_ROOT = SETTINGS.cast_hls_dir
HLS_ROOT.mkdir(parents=True, exist_ok=True)
CAST_ROOT = HLS_ROOT
SEGMENT_SECONDS = 2
# Enough history for a 30-second entertainment cushion without affecting a
# sports receiver that explicitly joins four seconds behind the live edge.
PLAYLIST_SEGMENTS = 40
OUTPUT_STALL_SECONDS = 15.0

_LOCK = threading.RLock()
_SESSIONS: dict[str, "HlsSession"] = {}
_TARGETS: dict[object, str] = {}
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
    recovery_cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    recovery_worker: threading.Thread | None = field(default=None, repr=False)
    manifest_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    manifest_normalizer: HlsManifestNormalizer = field(default_factory=HlsManifestNormalizer, repr=False)
    stderr_handle: object | None = field(default=None, repr=False)
    stderr_path: Path | None = None
    recovery_count: int = 0
    last_error: str = ""
    playlist_version: int = 0
    last_playlist_progress_monotonic: float = 0.0
    on_target: Callable[[str | None], None] | None = field(default=None, repr=False)
    read_ahead: bool = False
    retain_history: bool = False


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


def _target_key(target: str, read_ahead: bool, retain_history: bool = False):
    if retain_history:
        return ("retained", read_ahead, target)
    return ("read-ahead", target) if read_ahead else target


def _finish_process(process: subprocess.Popen) -> None:
    terminate(process)
    # An exited buffered muxer still owns a producer and feeder. Its owner
    # must finish cleanup before another source uses the released slot.
    process.wait(timeout=2)


def _force_stop_session(token: str) -> bool:
    with _LOCK:
        key = str(token or "")
        session = _SESSIONS.pop(key, None)
        _REFERENCES.pop(key, None)
        if session is not None:
            for target in _mapped_targets(session):
                target_key = _target_key(target, session.read_ahead, session.retain_history)
                if _TARGETS.get(target_key) == key:
                    _TARGETS.pop(target_key, None)
    if session is None:
        return False
    session.recovery_cancelled.set()
    # Cancellation makes a warming replacement exit promptly. Wait for its
    # owner before removing files so it cannot publish into a stopped session.
    with session.recovery_lock:
        _finish_process(session.process)
        _close_stderr(session)
    media_pipeline.release_session(session.pipeline_token)
    with session.manifest_lock:
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


def _append_start_number(directory: Path) -> int:
    # FFmpeg imports each retained entry with append_list and advances this
    # number itself. Starting at the next filename renumbers the old footage.
    try:
        for line in (directory / "stream.m3u8").read_text(encoding="utf-8").splitlines():
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                return max(0, int(line.partition(":")[2]))
    except (OSError, ValueError):
        pass
    return _next_segment_number(directory)


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


def _hls_command(target: str, directory: Path, *, retain_history: bool = False) -> list[str]:
    base_command = normalized_live_input_args(
        target,
        video_extra=("-force_key_frames", "expr:gte(t,n_forced*2)"),
        preserve_av_timing=True,
    )
    # Providers can send a buffered window immediately after reconnecting.
    # Consume it at playback speed so the rolling playlist does not run ahead
    # of the receiver and delete segments it has not downloaded yet.
    input_index = base_command.index("-i")
    # Retain the source clock across an HTTP reconnect. FFmpeg's default
    # discontinuity correction can retime an overlapping provider window into
    # new footage: output PTS stays monotonic while viewers see a backward replay.
    # CFR video and the audio resampler can instead discard already-played input.
    base_command[input_index:input_index] = ["-copyts", "-start_at_zero", "-re"]
    playlist_args = ["-hls_list_size", str(0 if retain_history else PLAYLIST_SEGMENTS)]
    flags = "append_list+omit_endlist+independent_segments+temp_file+discont_start"
    if retain_history:
        # A paused viewer still owns its earlier footage. Keep all completed
        # segments until its last playback lease releases this private mode.
        playlist_args += ["-hls_playlist_type", "event"]
    else:
        playlist_args += ["-hls_delete_threshold", "4"]
        flags = "delete_segments+" + flags
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
        *playlist_args,
        "-hls_allow_cache",
        "0",
        "-hls_segment_type",
        "mpegts",
        "-start_number",
        str(_append_start_number(directory)),
        "-hls_flags",
        flags,
        "-hls_segment_filename",
        str(directory / "segment_%06d.ts"),
        str(directory / "stream.m3u8"),
    ]


def _spawn(session: HlsSession, target: str) -> subprocess.Popen:
    command = _hls_command(target, session.directory, retain_history=session.retain_history)
    stderr_path = session.directory / "ffmpeg.stderr.log"
    stderr_handle = stderr_path.open("ab", buffering=0)
    try:
        if session.read_ahead:
            from .live_read_ahead import BufferedHlsProcess
            output_index = command.index("-f")
            encoder = command[:output_index]
            encoder.remove("-re")
            encoder += ["-sn", "-dn", "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", "pipe:1"]
            muxer = [command[0], "-nostdin", "-hide_banner", "-loglevel", "error",
                "-probesize", "65536", "-analyzeduration", "200000", "-f", "mpegts",
                "-copyts", "-start_at_zero", "-i", "pipe:0", "-map", "0:v:0?",
                "-map", "0:a:0?", "-c", "copy", *command[output_index:]]
            process = BufferedHlsProcess(encoder, muxer, stderr_handle)
        else:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
                bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception:
        stderr_handle.close()
        raise
    session.stderr_path = stderr_path
    session.stderr_handle = stderr_handle
    session.process = process
    session.playlist_version = 0
    session.last_playlist_progress_monotonic = time.monotonic()
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
        if session.recovery_cancelled.is_set():
            return False
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
    if session.recovery_cancelled.is_set():
        return False
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
    _finish_process(process)
    _record_failure(session, f"HLS candidate {index + 1}/{len(session.targets)} stopped before becoming ready.")
    return False


def _playlist_stalled(session: HlsSession) -> bool:
    """Detect an alive encoder that cannot publish after a true source reset."""
    try:
        version = (session.directory / "stream.m3u8").stat().st_mtime_ns
    except OSError:
        return False
    now = time.monotonic()
    with _LOCK:
        if version != session.playlist_version or not session.last_playlist_progress_monotonic:
            session.playlist_version = version
            session.last_playlist_progress_monotonic = now
            return False
        return now - session.last_playlist_progress_monotonic >= OUTPUT_STALL_SECONDS


def _recover_session(session: HlsSession, *, startup_timeout: float = 12.0) -> bool:
    with session.recovery_lock:
        with _LOCK:
            if _SESSIONS.get(session.token) is not session:
                return False
        if session.process.poll() is None:
            if not _playlist_stalled(session):
                return True
            # A genuinely new encoder clock can be far behind the old one.
            # Reopen that source with a new HLS discontinuity rather than wait
            # indefinitely for its timestamp to catch up with the prior epoch.
            _finish_process(session.process)
        else:
            # A buffered worker can have a dead muxer with producer cleanup
            # still in flight. Finish it here before opening another source.
            _finish_process(session.process)

        _record_failure(
            session,
            f"HLS source {session.target_index + 1}/{len(session.targets)} disconnected; attempting recovery.",
        )
        count = len(session.targets)
        order = [((session.target_index + offset) % count) for offset in range(1, count + 1)]
        for index in order:
            if session.recovery_cancelled.is_set():
                return False
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
        # Completed media belongs to the viewer, even while all sources are
        # temporarily unavailable. Keep it readable while the worker retries.
        return False


def _recover_in_background(session: HlsSession) -> None:
    delay = 1.0
    while not session.recovery_cancelled.is_set():
        with _LOCK:
            if _SESSIONS.get(session.token) is not session:
                return
        try:
            if _recover_session(session):
                return
        except (OSError, RuntimeError, subprocess.SubprocessError):
            LOGGER.warning("HLS recovery attempt failed; retained media remains available.")
        if session.recovery_cancelled.wait(delay):
            return
        delay = min(8.0, delay * 2)


def _schedule_recovery(session: HlsSession) -> None:
    with _LOCK:
        if _SESSIONS.get(session.token) is not session or session.recovery_cancelled.is_set():
            return
        if session.recovery_worker is not None and session.recovery_worker.is_alive():
            return
        worker = threading.Thread(target=_recover_in_background, args=(session,),
            name="hls-recovery", daemon=True)
        session.recovery_worker = worker
        worker.start()


def start_session(
    targets: str | Iterable[str],
    *,
    startup_timeout: float = 12.0,
    on_target: Callable[[str | None], None] | None = None,
    read_ahead: bool = False,
    retain_history: bool = False,
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
        # Each movie viewer joins at its own tune time and owns its paused
        # timeline. Sharing would start a new viewer at another owner's past.
        if not retain_history:
            for target in candidates:
                token = _TARGETS.get(_target_key(target, read_ahead, retain_history))
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
        read_ahead=read_ahead,
        retain_history=retain_history,
        manifest_normalizer=HlsManifestNormalizer(retain_window=not retain_history),
    )

    # Keep the currently playing relay alive while a replacement warms up.
    with _LOCK:
        oldest = [item for item in sorted(_SESSIONS.values(), key=lambda item: item.created_monotonic)[:-3]
                  if not item.retain_history]
    for old in oldest:
        stop_session(old.token)

    for index in range(len(candidates)):
        if _try_target(session, index, startup_timeout):
            with _LOCK:
                _SESSIONS[token] = session
                if not retain_history:
                    for target in candidates:
                        _TARGETS[_target_key(target, read_ahead, retain_history)] = token
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
    if session.process.poll() is not None or _playlist_stalled(session):
        # Existing playlist/segment requests must never wait for a replacement
        # encoder, or one reconnect can consume the receiver's entire buffer.
        _schedule_recovery(session)
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
        # FFmpeg must keep its original manifest for append_list. Publish a
        # separate receiver view whose segment epochs survive sliding/recovery.
        with session.manifest_lock:
            path = session.directory / "receiver.m3u8"
            try:
                raw = (session.directory / name).read_text(encoding="utf-8")
                if "#EXTINF:" not in raw:
                    return path if path.is_file() else None
                normalized = session.manifest_normalizer.normalize(raw, generation=session.recovery_count)
                temporary = path.with_suffix(".m3u8.tmp")
                temporary.write_text(normalized, encoding="utf-8")
                temporary.replace(path)
                return path
            except (OSError, ValueError):
                return path if path.is_file() else None
    elif name.startswith("segment_") and name.endswith(".ts") and name[8:-3].isdigit():
        path = session.directory / name
    else:
        return None
    try:
        path.resolve().relative_to(session.directory.resolve())
    except (OSError, ValueError):
        return None
    return path if path.exists() and path.is_file() else None
