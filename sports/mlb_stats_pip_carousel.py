from __future__ import annotations

import io
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from . import live_stats
from . import mlb_stats_carousel
from . import mlb_stats_companions


STREAM_PATH = "/sports/stats-carousel/mlb-pip/stream.m3u8"
FRAME_WIDTH = 1280
FRAME_HEIGHT = 360
TOP_CROP_BOTTOM = 174
LAST_PLAY_CROP_TOP = 494
LAST_PLAY_CROP_BOTTOM = 668

_LOCK = threading.RLock()
_SESSION: "CompactCarouselSession | None" = None


@dataclass
class CompactCarouselSession:
    directory: Path
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_state: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    current_event_key: str = ""
    current_parent_number: int = 0
    current_source_event_id: str = ""


def render_compact_frame(state: dict) -> bytes:
    """Remove the full scorecard middle while preserving the finished UI."""
    payload = live_stats.render_mlb_frame(state, width=FRAME_WIDTH, height=720)
    full = Image.open(io.BytesIO(payload)).convert("RGB")
    image = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT), (8, 14, 24))

    # Keep the complete top scoreboard exactly as rendered, including the final
    # enrichment/scorebug layers (logos, team gradients, inning marker, outs).
    top = full.crop((0, 0, FRAME_WIDTH, TOP_CROP_BOTTOM))
    image.paste(top, (0, 0))

    # Pull LAST PLAY directly beneath it. This intentionally removes line score,
    # team stats, bases/count, and batter/pitcher without redesigning either of
    # the two sections that remain.
    last_play = full.crop(
        (0, LAST_PLAY_CROP_TOP, FRAME_WIDTH, LAST_PLAY_CROP_BOTTOM)
    )
    image.paste(last_play, (0, TOP_CROP_BOTTOM))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "carousel-mlb-pip"


def _stop_session_locked() -> CompactCarouselSession | None:
    global _SESSION
    session = _SESSION
    _SESSION = None
    if session is not None:
        session.stop_event.set()
    return session


def stop_session() -> bool:
    with _LOCK:
        session = _stop_session_locked()
    if session is None:
        return False
    process = session.process
    if process is not None:
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        live_stats.terminate(process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def get_session() -> CompactCarouselSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def _worker(session: CompactCarouselSession, db_path: Path | str) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return

    candidates: list[dict] = []
    current_row: dict | None = None
    current_key = ""
    current_game_id = ""
    next_candidate_refresh = 0.0
    next_poll = 0.0
    switch_at = 0.0
    frame = render_compact_frame(mlb_stats_carousel._idle_state())

    try:
        while not session.stop_event.is_set() and process.poll() is None:
            now_mono = time.monotonic()
            if now_mono - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
                break

            if now_mono >= next_candidate_refresh:
                candidates = mlb_stats_carousel.candidate_rows(db_path)
                candidate_keys = {
                    mlb_stats_companions.logical_event_key(row)
                    for row in candidates
                }
                if current_row is not None and current_key not in candidate_keys:
                    current_row = None
                    current_game_id = ""
                    switch_at = 0.0
                next_candidate_refresh = (
                    now_mono + mlb_stats_carousel.CANDIDATE_REFRESH_SECONDS
                )

            if current_row is None or now_mono >= switch_at:
                current_row = mlb_stats_carousel._next_row(candidates, current_key)
                rotation_seconds = mlb_stats_carousel.rotation_seconds(db_path)
                if current_row is None:
                    current_key = ""
                    current_game_id = ""
                    session.current_event_key = ""
                    session.current_parent_number = 0
                    session.current_source_event_id = ""
                    session.last_state = mlb_stats_carousel._idle_state()
                    frame = render_compact_frame(session.last_state)
                    switch_at = now_mono + rotation_seconds
                    next_poll = now_mono + float(live_stats.POLL_SECONDS)
                else:
                    current_key = mlb_stats_companions.logical_event_key(current_row)
                    current_game_id = ""
                    session.current_event_key = current_key
                    session.current_parent_number = int(
                        current_row.get("assigned_number") or 0
                    )
                    session.current_source_event_id = ""
                    session.last_state = {}
                    switch_at = now_mono + rotation_seconds
                    next_poll = 0.0

            if current_row is not None and now_mono >= next_poll:
                try:
                    if not current_game_id:
                        current_game_id = mlb_stats_carousel._resolve_game_id(current_row)
                        session.current_source_event_id = current_game_id
                    state = live_stats.fetch_mlb_state(current_game_id)
                    if mlb_stats_carousel._is_final_state(state):
                        mlb_stats_carousel._FINAL_EVENT_KEYS.add(current_key)
                        candidates = [
                            row
                            for row in candidates
                            if mlb_stats_companions.logical_event_key(row) != current_key
                        ]
                        current_row = None
                        current_game_id = ""
                        switch_at = 0.0
                        next_poll = 0.0
                        continue
                    session.last_state = state
                    session.last_error = ""
                    frame = render_compact_frame(state)
                except Exception as exc:
                    session.last_error = str(exc)
                next_poll = now_mono + float(live_stats.POLL_SECONDS)

            try:
                process.stdin.write(frame)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            time.sleep(1.0 / float(live_stats.FRAME_RATE))
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        live_stats.terminate(process)
        with _LOCK:
            global _SESSION
            if _SESSION is session:
                _SESSION = None
        shutil.rmtree(session.directory, ignore_errors=True)


def start_session(db_path: Path | str) -> CompactCarouselSession:
    global _SESSION
    if not mlb_stats_carousel.is_enabled(db_path):
        raise RuntimeError("MLB PiP carousel is not enabled by Sports Automation.")

    with _LOCK:
        current = _SESSION
        if current is not None and current.process is not None and current.process.poll() is None:
            current.last_access_monotonic = time.monotonic()
            return current
    if current is not None:
        stop_session()

    directory = _directory()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            live_stats._ffmpeg_command(directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start compact MLB PiP carousel: {exc}") from exc

    session = CompactCarouselSession(directory=directory, process=process)
    thread = threading.Thread(
        target=_worker,
        args=(session, db_path),
        name="mlb-stats-pip-carousel",
        daemon=True,
    )
    session.thread = thread
    with _LOCK:
        _SESSION = session
    thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + float(live_stats.STARTUP_TIMEOUT)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session()
            raise RuntimeError(
                "Compact MLB PiP carousel stopped before the stream became ready."
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
    raise RuntimeError("Compact MLB PiP carousel did not become ready in time.")


def safe_media_file(db_path: Path | str, filename: str) -> Path | None:
    session = get_session()
    if session is None and filename == "stream.m3u8":
        session = start_session(db_path)
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
    session.last_access_monotonic = time.monotonic()
    return path if path.exists() and path.is_file() else None


def state_payload(db_path: Path | str) -> dict[str, Any]:
    session = get_session()
    return {
        "enabled": mlb_stats_carousel.is_enabled(db_path),
        "active": session is not None,
        "rotation_seconds": mlb_stats_carousel.rotation_seconds(db_path),
        "current_event_key": session.current_event_key if session else "",
        "current_parent_number": session.current_parent_number if session else 0,
        "current_source_event_id": session.current_source_event_id if session else "",
        "error": session.last_error if session else "",
        "state": dict(session.last_state) if session and session.last_state else {},
        "frame_width": FRAME_WIDTH,
        "frame_height": FRAME_HEIGHT,
    }
