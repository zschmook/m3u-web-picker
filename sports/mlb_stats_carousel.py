from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import sports as _s

from . import live_stats
from . import mlb_stats_companions


CHANNEL_NUMBER = "0.1"
DISPLAY_NAME = "MLB Live Scores"
TVG_ID = "m3u-picker-sports-stats-carousel-mlb"
GROUP_TITLE = "Sports Stats"
PLAY_URL = "/guide/play/stats-carousel/mlb"
STREAM_PATH = "/sports/stats-carousel/mlb/stream.m3u8"
ROTATE_SECONDS = 10.0
ROTATION_OPTIONS = (10, 15, 30, 45, 60)
ROTATION_SETTING_KEY = "__mlb_stats_carousel_rotation_seconds"
CANDIDATE_REFRESH_SECONDS = 15.0
POSTGAME_WINDOW = timedelta(minutes=90)

_LOCK = threading.RLock()
_SESSION: "CarouselSession | None" = None
_RESOLVED_GAME_IDS: dict[str, str] = {}
_FINAL_EVENT_KEYS: set[str] = set()


@dataclass
class CarouselSession:
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


def rotation_seconds(db_path: Path | str) -> float:
    _s.init_db(db_path)
    with closing(_s.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (ROTATION_SETTING_KEY,),
        ).fetchone()
    if not row:
        return ROTATE_SECONDS
    raw = row["value"]
    try:
        value = int(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return ROTATE_SECONDS
    return float(value) if value in ROTATION_OPTIONS else ROTATE_SECONDS


def set_rotation_seconds(db_path: Path | str, value: object) -> float:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Live-score rotation must be 10, 15, 30, 45, or 60 seconds.") from exc
    if seconds not in ROTATION_OPTIONS:
        raise ValueError("Live-score rotation must be 10, 15, 30, 45, or 60 seconds.")
    _s.init_db(db_path)
    with closing(_s.connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
            (ROTATION_SETTING_KEY, json.dumps(seconds)),
        )
        conn.commit()
    return float(seconds)


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _event_window(row: dict) -> tuple[datetime | None, datetime | None]:
    start = _datetime(row.get("event_start"))
    stop = _datetime(row.get("event_end"))
    if start is None:
        programme = row.get("epg_programme")
        if isinstance(programme, dict):
            start = _datetime(programme.get("start"))
            stop = stop or _datetime(programme.get("stop"))
    if start is not None and (stop is None or stop <= start):
        stop = start + timedelta(hours=5)
    return start, stop


def _row_thinks_live(row: dict, now: datetime) -> bool:
    if bool(row.get("is_replay")):
        return False
    key = mlb_stats_companions.logical_event_key(row)
    if key in _FINAL_EVENT_KEYS:
        return False
    start, stop = _event_window(row)
    if start is None or stop is None:
        return False
    anchor = now.astimezone(start.tzinfo)
    return start <= anchor <= stop + POSTGAME_WINDOW


def enabled_rows(db_path: Path | str) -> list[dict]:
    """Return one generated MLB parent per logical event admitted by Sports Automation."""
    return mlb_stats_companions.primary_mlb_rows(_s.generated_rows(db_path))


def _has_enabled_mlb_rule(db_path: Path | str) -> bool:
    rules = [rule for rule in _s.get_rules(db_path) if bool(rule.get("enabled"))]
    if not rules:
        return False

    mlb_sport_id = str(_s.LEAGUE_SPORTS.get("mlb", "") or "")
    for rule in rules:
        scope_type = str(rule.get("scope_type") or "").strip().lower()
        scope_id = str(rule.get("scope_id") or "").strip().lower()
        if scope_type == "league" and scope_id == "mlb":
            return True
        if scope_type == "sport" and mlb_sport_id and scope_id == mlb_sport_id:
            return True
        # Team ids discovered by the sports catalog are namespaced by league.
        if scope_type == "team" and scope_id.startswith("mlb:"):
            return True

    # Static/API catalog rows carry league_id, which covers team rules even if a
    # future catalog changes its scope-id naming convention.
    catalog = {
        (str(item.get("scope_type") or ""), str(item.get("id") or "")): item
        for item in _s.catalog_payload(db_path)
    }
    for rule in rules:
        key = (str(rule.get("scope_type") or ""), str(rule.get("scope_id") or ""))
        item = catalog.get(key)
        if item and str(item.get("league_id") or "").strip().lower() == "mlb":
            return True
    return False


def candidate_rows(db_path: Path | str, *, now: datetime | None = None) -> list[dict]:
    anchor = now if isinstance(now, datetime) else datetime.now().astimezone()
    if anchor.tzinfo is None:
        anchor = anchor.astimezone()
    return [row for row in enabled_rows(db_path) if _row_thinks_live(row, anchor)]


def is_enabled(db_path: Path | str) -> bool:
    return _has_enabled_mlb_rule(db_path)


def guide_item() -> dict:
    return {
        "number": CHANNEL_NUMBER,
        "name": DISPLAY_NAME,
        "group": GROUP_TITLE,
        "logo": "",
        "tvg_id": TVG_ID,
        "subtitle": "Rotating live MLB scores",
        "generated": True,
        "play_url": PLAY_URL,
        "stats_carousel": True,
        "stats_sport": "mlb",
    }


def m3u_lines(base_url: str) -> list[str]:
    attrs = [
        f'tvg-id="{TVG_ID}"',
        f'tvg-chno="{CHANNEL_NUMBER}"',
        f'tvg-name="{DISPLAY_NAME}"',
        f'group-title="{GROUP_TITLE}"',
        'x-sports-stats-carousel="mlb"',
    ]
    return [
        f"#EXTINF:-1 {' '.join(attrs)},{DISPLAY_NAME}",
        f"{base_url.rstrip('/')}{STREAM_PATH}",
    ]


def _idle_state() -> dict:
    return {
        "away": {"abbr": "MLB", "score": "-", "record": "", "stats": {}, "innings": []},
        "home": {"abbr": "LIVE", "score": "-", "record": "", "stats": {}, "innings": []},
        "status": "Waiting for live games",
        "state": "idle",
        "period": 0,
        "balls": 0,
        "strikes": 0,
        "outs": 0,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "",
        "pitcher": "",
        "last_play": "No enabled MLB game is currently inside its expected live window.",
        "source_event_id": "",
        "mlb_game_pk": "",
        "data_source": "mlb-carousel-idle",
        "data_source_label": DISPLAY_NAME,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _is_final_state(state: dict) -> bool:
    abstract = str(state.get("state") or "").strip().lower()
    if abstract in {"final", "completed", "post"}:
        return True
    status = str(state.get("status") or "").strip().lower()
    return status.startswith("final") or status.startswith("completed")


def _resolve_game_id(row: dict) -> str:
    key = mlb_stats_companions.logical_event_key(row)
    cached = _RESOLVED_GAME_IDS.get(key, "")
    if cached:
        return cached
    game_id, _game = live_stats.resolve_espn_event(row)
    game_id = str(game_id or "").strip()
    if not game_id:
        raise RuntimeError("MLB carousel could not resolve a game id.")
    _RESOLVED_GAME_IDS[key] = game_id
    return game_id


def _next_row(rows: list[dict], current_key: str) -> dict | None:
    if not rows:
        return None
    keys = [mlb_stats_companions.logical_event_key(row) for row in rows]
    if current_key in keys:
        return rows[(keys.index(current_key) + 1) % len(rows)]
    return rows[0]


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "carousel-mlb"


def _stop_session_locked() -> CarouselSession | None:
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


def get_session() -> CarouselSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def _worker(session: CarouselSession, db_path: Path | str) -> None:
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
    frame = live_stats.render_mlb_frame(_idle_state())

    try:
        while not session.stop_event.is_set() and process.poll() is None:
            now_mono = time.monotonic()
            if now_mono - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
                break

            if now_mono >= next_candidate_refresh:
                candidates = candidate_rows(db_path)
                candidate_keys = {
                    mlb_stats_companions.logical_event_key(row)
                    for row in candidates
                }
                if current_row is not None and current_key not in candidate_keys:
                    current_row = None
                    current_game_id = ""
                    switch_at = 0.0
                next_candidate_refresh = now_mono + CANDIDATE_REFRESH_SECONDS

            if current_row is None or now_mono >= switch_at:
                current_row = _next_row(candidates, current_key)
                current_rotation_seconds = rotation_seconds(db_path)
                if current_row is None:
                    current_key = ""
                    current_game_id = ""
                    session.current_event_key = ""
                    session.current_parent_number = 0
                    session.current_source_event_id = ""
                    session.last_state = _idle_state()
                    frame = live_stats.render_mlb_frame(session.last_state)
                    switch_at = now_mono + current_rotation_seconds
                    next_poll = now_mono + float(live_stats.POLL_SECONDS)
                else:
                    current_key = mlb_stats_companions.logical_event_key(current_row)
                    current_game_id = ""
                    session.current_event_key = current_key
                    session.current_parent_number = int(current_row.get("assigned_number") or 0)
                    session.current_source_event_id = ""
                    session.last_state = {}
                    switch_at = now_mono + current_rotation_seconds
                    next_poll = 0.0

            if current_row is not None and now_mono >= next_poll:
                try:
                    if not current_game_id:
                        current_game_id = _resolve_game_id(current_row)
                        session.current_source_event_id = current_game_id
                    state = live_stats.fetch_mlb_state(current_game_id)
                    if _is_final_state(state):
                        _FINAL_EVENT_KEYS.add(current_key)
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
                    frame = live_stats.render_mlb_frame(state)
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


def start_session(db_path: Path | str) -> CarouselSession:
    global _SESSION
    if not is_enabled(db_path):
        raise RuntimeError("MLB live-score carousel is not enabled by Sports Automation.")

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
        raise RuntimeError(f"Could not start MLB carousel ffmpeg: {exc}") from exc

    session = CarouselSession(directory=directory, process=process)
    thread = threading.Thread(
        target=_worker,
        args=(session, db_path),
        name="mlb-stats-carousel",
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
            raise RuntimeError("MLB carousel ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session()
    raise RuntimeError("MLB carousel stream did not become ready in time.")


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


def state_payload(db_path: Path | str) -> dict:
    generated = enabled_rows(db_path)
    candidates = candidate_rows(db_path)
    session = get_session()
    return {
        "channel_number": CHANNEL_NUMBER,
        "name": DISPLAY_NAME,
        "enabled": is_enabled(db_path),
        "generated_event_count": len(generated),
        "live_candidate_count": len(candidates),
        "rotation_seconds": rotation_seconds(db_path),
        "rotation_options": list(ROTATION_OPTIONS),
        "active": session is not None,
        "current_event_key": session.current_event_key if session else "",
        "current_parent_number": session.current_parent_number if session else 0,
        "current_source_event_id": session.current_source_event_id if session else "",
        "error": session.last_error if session else "",
        "state": dict(session.last_state) if session and session.last_state else {},
    }
