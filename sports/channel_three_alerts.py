from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from media.ffmpeg import terminate

from . import game_alert_demo as demo
from . import live_stats
from .alert_stream import render_alert
from .channel_one_alerts import MlbScoreTracker


CHANNEL_NUMBER = 3
STREAM_PATH = "/sports/mlb-score-alerts/3/stream.m3u8"

_LOCK = threading.RLock()
_SESSION: "ChannelThreeAlertSession | None" = None


@dataclass
class ChannelThreeAlertSession:
    directory: Path
    parent_url: str
    tracker: MlbScoreTracker = field(default_factory=MlbScoreTracker)
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    poll_thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""


def _parent_target() -> str:
    import core
    import sports

    for item in core.curated_channels_for_guide():
        if str(item.get("number", "") or "").strip() != str(CHANNEL_NUMBER):
            continue
        play_url = str(item.get("play_url", "") or "").split("?", 1)[0].strip()
        manual = re.fullmatch(r"/guide/play/manual/([^/]+)", play_url)
        if manual:
            return core.manual_stream_target(manual.group(1))
        generated = re.fullmatch(r"/guide/play/sports/(\d+)", play_url)
        if generated:
            return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "mlb-score-alerts-channel-3"


def _poll_loop(session: ChannelThreeAlertSession, db_path: Path | str) -> None:
    while not session.stop_event.is_set():
        process = session.process
        if process is None or process.poll() is not None:
            return
        if time.monotonic() - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
            return
        try:
            session.tracker.poll(db_path)
        except Exception as exc:
            session.last_error = str(exc)
            with session.tracker.state_lock:
                session.tracker.last_error = str(exc)
        session.stop_event.wait(3.0)


def _run(session: ChannelThreeAlertSession, db_path: Path | str) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return
    frame_period = 1.0 / demo.FRAME_RATE
    try:
        while not session.stop_event.is_set():
            if process.poll() is not None:
                break
            if time.monotonic() - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
                break
            alert = session.tracker.current(db_path)
            try:
                process.stdin.write(render_alert(alert))
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                session.last_error = str(exc)
                break
            session.stop_event.wait(frame_period)
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        terminate(process)
        with _LOCK:
            global _SESSION
            if _SESSION is session:
                _SESSION = None
        shutil.rmtree(session.directory, ignore_errors=True)


def start_session(db_path: Path | str) -> ChannelThreeAlertSession:
    global _SESSION
    target = _parent_target()
    if not target:
        raise RuntimeError("Channel 3 is not available for MLB scoring alerts.")

    with _LOCK:
        current = _SESSION
        if (
            current is not None
            and current.process is not None
            and current.process.poll() is None
            and current.parent_url == target
        ):
            current.last_access_monotonic = time.monotonic()
            return current
    if current is not None:
        stop_session()

    directory = _directory()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            demo._ffmpeg_command(target, directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start channel 3 alert wrapper: {exc}") from exc

    session = ChannelThreeAlertSession(directory=directory, parent_url=target, process=process)
    session.thread = threading.Thread(
        target=_run,
        args=(session, db_path),
        name="mlb-score-alerts-channel-3-render",
        daemon=True,
    )
    session.poll_thread = threading.Thread(
        target=_poll_loop,
        args=(session, db_path),
        name="mlb-score-alerts-channel-3-poll",
        daemon=True,
    )
    with _LOCK:
        _SESSION = session
    session.thread.start()
    session.poll_thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + demo.STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error = session.last_error
            stop_session()
            raise RuntimeError(
                error or "Channel 3 alert wrapper stopped before the stream became ready."
            )
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session()
    raise RuntimeError("Channel 3 alert wrapper did not become ready in time.")


def stop_session() -> bool:
    global _SESSION
    with _LOCK:
        session = _SESSION
        _SESSION = None
        if session is not None:
            session.stop_event.set()
    if session is None:
        return False
    if session.process is not None:
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def get_session() -> ChannelThreeAlertSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(db_path: Path | str, filename: str) -> Path | None:
    if not re.fullmatch(r"(?:stream\.m3u8|segment_\d{6}\.ts)", str(filename or "")):
        return None
    session = get_session()
    if session is None and filename == "stream.m3u8":
        session = start_session(db_path)
    if session is None:
        return None
    path = session.directory / filename
    return path if path.exists() else None


def state_payload(db_path: Path | str) -> dict:
    session = get_session()
    if session is None:
        return {
            "channel_number": CHANNEL_NUMBER,
            "mode": "all-mlb-scores",
            "active": False,
            "active_alert": None,
            "queued_alerts": 0,
            "tracked_games": 0,
            "last_error": "",
        }
    payload = session.tracker.state_payload(db_path)
    payload["channel_number"] = CHANNEL_NUMBER
    payload["active"] = True
    if session.last_error:
        payload["last_error"] = session.last_error
    return payload


def route_channel_three(text: str, base_url: str) -> str:
    """Point served channel 3 at the temporary MLB-scoring alert wrapper."""
    lines = str(text or "").splitlines()
    target = f"{base_url.rstrip('/')}{STREAM_PATH}"
    for index, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        if not re.search(r'\btvg-chno="3"', line):
            continue
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("#"):
            cursor += 1
        if cursor < len(lines):
            lines[cursor] = target
        break
    return "\n".join(lines) + ("\n" if lines else "")
