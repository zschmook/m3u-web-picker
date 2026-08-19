from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from media.ffmpeg import executable as ffmpeg_executable
from media.ffmpeg import terminate
from . import live_stats


STREAM_PATH = "/sports/multiview/ncaa/stream.m3u8"
DISPLAY_NAME = "NCAA Multiview"
GROUP_TITLE = "Sports Multiview"
IDLE_SECONDS = 180.0
STARTUP_TIMEOUT = 25.0


# A deliberately static Week 5 test slate. The weights are the prototype
# values agreed on for the director: subscribed team wins, then AP strength,
# conference strength, opponent strength, and finally live-game modifiers.
WEEK_5_GAMES = (
    {"id": "ore-psu", "away": "Oregon", "away_abbr": "ORE", "away_score": 21, "home": "Penn State", "home_abbr": "PSU", "home_score": 24, "clock": "8:14", "period": "3rd", "weight": 1000, "subscribed": True, "color": "0x1b365d"},
    {"id": "lsu-miss", "away": "LSU", "away_abbr": "LSU", "away_score": 17, "home": "Ole Miss", "home_abbr": "MISS", "home_score": 20, "clock": "4:51", "period": "3rd", "weight": 760, "color": "0x4b116f"},
    {"id": "ala-uga", "away": "Alabama", "away_abbr": "ALA", "away_score": 24, "home": "Georgia", "home_abbr": "UGA", "home_score": 21, "clock": "10:02", "period": "4th", "weight": 680, "color": "0x9e1b32"},
    {"id": "usc-ill", "away": "USC", "away_abbr": "USC", "away_score": 14, "home": "Illinois", "home_abbr": "ILL", "home_score": 17, "clock": "2:10", "period": "2nd", "weight": 536, "color": "0x13294b"},
    {"id": "wis-nd", "away": "Wisconsin", "away_abbr": "WIS", "away_score": 14, "home": "Notre Dame", "home_abbr": "ND", "home_score": 0, "clock": "2:10", "period": "2nd", "weight": 438, "color": "0x153e7e"},
    {"id": "lou-miss", "away": "Louisville", "away_abbr": "LOU", "away_score": 10, "home": "Ole Miss", "home_abbr": "MISS", "home_score": 17, "clock": "8:31", "period": "3rd", "weight": 426, "color": "0x8c1d40"},
    {"id": "clem-lsu", "away": "Clemson", "away_abbr": "CLEM", "away_score": 24, "home": "LSU", "home_abbr": "LSU", "home_score": 17, "clock": "6:42", "period": "4th", "weight": 362, "color": "0xf56600"},
    {"id": "bay-aub", "away": "Baylor", "away_abbr": "BAY", "away_score": 7, "home": "Auburn", "home_abbr": "AUB", "home_score": 10, "clock": "12:20", "period": "2nd", "weight": 126, "color": "0x0b6e4f"},
)

GAME_BY_ID = {game["id"]: dict(game) for game in WEEK_5_GAMES}
DEFAULT_SLOTS = ["ore-psu", "lsu-miss", "ala-uga", "usc-ill"]


@dataclass
class DirectorState:
    slots: list[str] = field(default_factory=lambda: list(DEFAULT_SLOTS))
    locked: list[bool] = field(default_factory=lambda: [True, False, False, False])
    audio_slot: int = 0
    upset_ids: list[str] = field(default_factory=list)
    revision: int = 1


@dataclass
class MultiviewSession:
    directory: Path
    process: subprocess.Popen
    revision: int
    started_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""
    source_ids: tuple[str, ...] = ()


_LOCK = threading.RLock()
_STATE = DirectorState()
_SESSION: MultiviewSession | None = None
_STUB_SESSIONS: dict[str, MultiviewSession] = {}
_REAPER_STARTED = False


def _ensure_reaper() -> None:
    global _REAPER_STARTED
    with _LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True

    def reap() -> None:
        global _SESSION
        while True:
            time.sleep(15.0)
            now = time.monotonic()
            expired: list[MultiviewSession] = []
            with _LOCK:
                if _SESSION is not None and now - _SESSION.last_access_monotonic > IDLE_SECONDS:
                    expired.append(_SESSION)
                    _SESSION = None
                active_sources = set(_SESSION.source_ids if _SESSION is not None else ())
                for game_id, session in tuple(_STUB_SESSIONS.items()):
                    if game_id in active_sources:
                        continue
                    if now - session.last_access_monotonic > IDLE_SECONDS:
                        expired.append(session)
                        _STUB_SESSIONS.pop(game_id, None)
            for session in expired:
                terminate(session.process)

    threading.Thread(target=reap, name="sports-multiview-reaper", daemon=True).start()


def _game_payload(game_id: str) -> dict:
    game = dict(GAME_BY_ID[game_id])
    game["score_text"] = f'{game["away_abbr"]} {game["away_score"]} @ {game["home_score"]} {game["home_abbr"]}'
    game["status_text"] = f'{game["clock"]} - {game["period"]}'
    game["upset_alert"] = game_id in _STATE.upset_ids
    return game


def state_payload() -> dict:
    with _LOCK:
        state = DirectorState(list(_STATE.slots), list(_STATE.locked), _STATE.audio_slot, list(_STATE.upset_ids), _STATE.revision)
        session = _SESSION
    selected = set(state.slots)
    ticker = sorted(
        (_game_payload(game_id) for game_id in GAME_BY_ID if game_id not in selected),
        key=lambda game: (not game["upset_alert"], -int(game["weight"])),
    )
    return {
        "sport": "ncaa",
        "display_name": DISPLAY_NAME,
        "stream_path": STREAM_PATH,
        "slots": [_game_payload(game_id) for game_id in state.slots],
        "locked": state.locked,
        "audio_slot": state.audio_slot,
        "ticker": ticker,
        "revision": state.revision,
        "active": bool(session and session.process.poll() is None),
        "last_error": session.last_error if session else "",
        "connections": {"visible": 4, "spare": 1},
    }


def update_state(payload: dict) -> dict:
    global _SESSION
    with _LOCK:
        next_slots = list(_STATE.slots)
        next_locked = list(_STATE.locked)
        next_audio = _STATE.audio_slot
        next_upsets = list(_STATE.upset_ids)

        if "locked" in payload:
            next_locked = [bool(value) for value in payload.get("locked") or []]
            if len(next_locked) != 4:
                raise ValueError("locked must contain four values")
            next_locked[0] = True
        if "slots" in payload:
            slots = [str(value) for value in payload.get("slots") or []]
            if len(slots) != 4 or len(set(slots)) != 4 or any(value not in GAME_BY_ID for value in slots):
                raise ValueError("slots must contain four different known game ids")
            for index, locked in enumerate(next_locked):
                if locked and slots[index] != _STATE.slots[index]:
                    raise ValueError(f"slot {index} is locked")
            next_slots = slots
        if "audio_slot" in payload:
            next_audio = int(payload["audio_slot"])
            if next_audio not in range(4):
                raise ValueError("audio_slot must be between 0 and 3")
        if "upset_ids" in payload:
            next_upsets = list(dict.fromkeys(str(value) for value in payload.get("upset_ids") or []))
            if any(value not in GAME_BY_ID for value in next_upsets):
                raise ValueError("upset_ids contains an unknown game")

        stream_changed = next_slots != _STATE.slots or next_audio != _STATE.audio_slot
        changed = stream_changed or next_locked != _STATE.locked or next_upsets != _STATE.upset_ids
        if changed:
            _STATE.slots = next_slots
            _STATE.locked = next_locked
            _STATE.audio_slot = next_audio
            _STATE.upset_ids = next_upsets
            _STATE.revision += 1
            old_session = _SESSION if stream_changed else None
            if stream_changed:
                _SESSION = None
            elif _SESSION is not None:
                _SESSION.revision = _STATE.revision
        else:
            old_session = None
    if old_session is not None:
        terminate(old_session.process)
    return state_payload()


def reset_state() -> dict:
    global _SESSION
    with _LOCK:
        stream_changed = _STATE.slots != DEFAULT_SLOTS or _STATE.audio_slot != 0
        _STATE.slots = list(DEFAULT_SLOTS)
        _STATE.locked = [True, False, False, False]
        _STATE.audio_slot = 0
        _STATE.upset_ids = []
        _STATE.revision += 1
        old_session = _SESSION if stream_changed else None
        if stream_changed:
            _SESSION = None
        elif _SESSION is not None:
            _SESSION.revision = _STATE.revision
    if old_session is not None:
        terminate(old_session.process)
    return state_payload()


def _escape_drawtext(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def ffmpeg_command(directory: Path, input_targets: list[str], audio_slot: int) -> list[str]:
    if len(input_targets) != 4:
        raise ValueError("multiview requires exactly four input targets")
    playlist = directory / "stream.m3u8"
    segments = directory / "segment_%06d.ts"
    command = [ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error"]
    for target in input_targets:
        command.extend(["-thread_queue_size", "64", "-threads", "1", "-i", str(target)])
    filters = (
        "[0:v]scale=1280:1080,setsar=1[main];"
        "[1:v]scale=640:360,setsar=1[one];"
        "[2:v]scale=640:360,setsar=1[two];"
        "[3:v]scale=640:360,setsar=1[three];"
        "[main]pad=1920:1080:0:0:black[canvas];"
        "[canvas][one]overlay=x=1280:y=0[first];"
        "[first][two]overlay=x=1280:y=360[second];"
        "[second][three]overlay=x=1280:y=720[v]"
    )
    audio_input = max(0, min(3, int(audio_slot)))
    command.extend([
        "-filter_complex_threads", "2", "-filter_complex", filters,
        "-map", "[v]", "-map", f"{audio_input}:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-threads", "4", "-r", "10", "-g", "20", "-keyint_min", "20", "-sc_threshold", "0",
        "-force_key_frames", "expr:gte(t,n_forced*2)", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
        "-hls_delete_threshold", "4", "-hls_allow_cache", "0",
        "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", str(segments), str(playlist),
    ])
    return command


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "multiview-ncaa"


def start_session() -> MultiviewSession:
    global _SESSION
    _ensure_reaper()
    with _LOCK:
        if _SESSION is not None and _SESSION.process.poll() is None and _SESSION.revision == _STATE.revision:
            _SESSION.last_access_monotonic = time.monotonic()
            return _SESSION
        state = DirectorState(list(_STATE.slots), list(_STATE.locked), _STATE.audio_slot, list(_STATE.upset_ids), _STATE.revision)
        old = _SESSION
        _SESSION = None
    if old is not None:
        terminate(old.process)
    directory = _directory()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    input_targets: list[str] = []
    for game_id in state.slots:
        playlist = safe_stub_media_file(game_id, "stream.m3u8")
        if playlist is None:
            raise RuntimeError(f"Could not start generated test channel {game_id}")
        input_targets.append(str(playlist))
    try:
        process = subprocess.Popen(ffmpeg_command(directory, input_targets, state.audio_slot), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise RuntimeError(f"Could not start multiview ffmpeg: {exc}") from exc
    session = MultiviewSession(directory, process, state.revision, source_ids=tuple(state.slots))
    with _LOCK:
        _SESSION = session
    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            with _LOCK:
                if _SESSION is session:
                    _SESSION = None
            raise RuntimeError("Multiview ffmpeg stopped before the stream became ready")
        try:
            if "#EXTINF:" in playlist.read_text(encoding="utf-8", errors="replace"):
                return session
        except OSError:
            pass
        time.sleep(0.1)
    terminate(process)
    raise RuntimeError("Multiview stream did not become ready in time")


def safe_media_file(filename: str) -> Path | None:
    if not re.fullmatch(r"(?:stream\.m3u8|segment_\d{6}\.ts)", str(filename or "")):
        return None
    with _LOCK:
        session = _SESSION
    if (session is None or session.process.poll() is not None or session.revision != _STATE.revision) and filename == "stream.m3u8":
        session = start_session()
    if session is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    with _LOCK:
        for game_id in session.source_ids:
            stub = _STUB_SESSIONS.get(game_id)
            if stub is not None:
                stub.last_access_monotonic = session.last_access_monotonic
    path = session.directory / filename
    return path if path.exists() else None


def stub_ffmpeg_command(directory: Path, game: dict) -> list[str]:
    playlist = directory / "stream.m3u8"
    segments = directory / "segment_%06d.ts"
    label = _escape_drawtext(f'{game["away_abbr"]} {game["away_score"]}  @  {game["home_score"]} {game["home_abbr"]}')
    status = _escape_drawtext(f'{game["clock"]} - {game["period"]}   WEEK 5 WEIGHT {game["weight"]}')
    video = (
        f'color=c={game["color"]}:s=640x360:r=10,'
        f'drawbox=x=0:y=0:w=iw:h=92:color=black@0.28:t=fill,'
        f'drawtext=text=\'{label}\':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=18,'
        f'drawtext=text=\'{status}\':fontcolor=white:fontsize=18:x=(w-text_w)/2:y=62'
    )
    return [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
        "-tune", "zerolatency", "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-threads", "1", "-r", "10", "-g", "20", "-keyint_min", "20", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "48000",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "8", "-hls_delete_threshold", "4",
        "-hls_allow_cache", "0", "-hls_flags", "delete_segments+append_list+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", str(segments), str(playlist),
    ]


def safe_stub_media_file(game_id: str, filename: str) -> Path | None:
    _ensure_reaper()
    if game_id not in GAME_BY_ID or not re.fullmatch(r"(?:stream\.m3u8|segment_\d{6}\.ts)", str(filename or "")):
        return None
    with _LOCK:
        session = _STUB_SESSIONS.get(game_id)
    if (session is None or session.process.poll() is not None) and filename == "stream.m3u8":
        directory = Path(live_stats.STATS_ROOT) / "multiview-stubs" / game_id
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(stub_ffmpeg_command(directory, GAME_BY_ID[game_id]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise RuntimeError(f"Could not start test game ffmpeg: {exc}") from exc
        session = MultiviewSession(directory, process, 1)
        with _LOCK:
            old = _STUB_SESSIONS.get(game_id)
            _STUB_SESSIONS[game_id] = session
        if old is not None:
            terminate(old.process)
        playlist = directory / "stream.m3u8"
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Test game ffmpeg stopped before the stream became ready")
            try:
                if "#EXTINF:" in playlist.read_text(encoding="utf-8", errors="replace"):
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            terminate(process)
            raise RuntimeError("Test game stream did not become ready in time")
    if session is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    path = session.directory / filename
    return path if path.exists() else None


def playlist(base_url: str) -> str:
    return "\n".join((
        "#EXTM3U",
        f'#EXTINF:-1 tvg-id="m3u-picker-multiview-ncaa" tvg-name="{DISPLAY_NAME}" group-title="{GROUP_TITLE}",{DISPLAY_NAME}',
        f'{base_url.rstrip("/")}{STREAM_PATH}',
        "",
    ))


def inject_channel(text: str, base_url: str) -> str:
    lines = str(text or "").splitlines()
    entry = [
        f'#EXTINF:-1 tvg-id="m3u-picker-multiview-ncaa" tvg-name="{DISPLAY_NAME}" group-title="{GROUP_TITLE}",{DISPLAY_NAME}',
        f'{base_url.rstrip("/")}{STREAM_PATH}',
    ]
    insert_at = 1 if lines and lines[0].startswith("#EXTM3U") else 0
    lines[insert_at:insert_at] = entry
    return "\n".join(lines) + "\n"
