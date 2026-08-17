from __future__ import annotations

import io
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from media.ffmpeg import terminate
from settings import SETTINGS
from . import live_stats


GUIDE_NUMBER = "1.2"
TVG_ID = "m3u-picker-sports-stats-fake-mlb-1"
DISPLAY_NAME = "MLB Stats Simulator · Phillies at Astros"
ROOT = SETTINGS.cast_hls_dir / "sports-stats-fake-mlb"
ROOT.mkdir(parents=True, exist_ok=True)

FRAME_RATE = 2
STATE_SECONDS = 5.0
IDLE_SECONDS = 60.0
STARTUP_TIMEOUT = 24.0

_LOCK = threading.RLock()
_SESSION: "FakeSession | None" = None


STATES: list[dict[str, Any]] = [
    {
        "status": "Pregame",
        "away_score": 0,
        "home_score": 0,
        "away_innings": [],
        "home_innings": [],
        "away_hits": 0,
        "home_hits": 0,
        "away_errors": 0,
        "home_errors": 0,
        "balls": 0,
        "strikes": 0,
        "outs": 0,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "T. Turner",
        "pitcher": "H. Brown",
        "last_play": "First pitch coming up.",
    },
    {
        "status": "Top 1st",
        "away_score": 0,
        "home_score": 0,
        "away_innings": ["0"],
        "home_innings": ["-"],
        "away_hits": 1,
        "home_hits": 0,
        "away_errors": 0,
        "home_errors": 0,
        "balls": 1,
        "strikes": 1,
        "outs": 1,
        "on_first": True,
        "on_second": False,
        "on_third": False,
        "batter": "B. Harper",
        "pitcher": "H. Brown",
        "last_play": "Harper singled to right. Turner to third.",
    },
    {
        "status": "Top 1st",
        "away_score": 1,
        "home_score": 0,
        "away_innings": ["1"],
        "home_innings": ["-"],
        "away_hits": 2,
        "home_hits": 0,
        "away_errors": 0,
        "home_errors": 0,
        "balls": 2,
        "strikes": 2,
        "outs": 2,
        "on_first": False,
        "on_second": False,
        "on_third": True,
        "batter": "K. Schwarber",
        "pitcher": "H. Brown",
        "last_play": "Turner scored on a Realmuto sacrifice fly.",
    },
    {
        "status": "Bottom 1st",
        "away_score": 1,
        "home_score": 2,
        "away_innings": ["1"],
        "home_innings": ["2"],
        "away_hits": 2,
        "home_hits": 3,
        "away_errors": 0,
        "home_errors": 0,
        "balls": 0,
        "strikes": 1,
        "outs": 1,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "C. Walker",
        "pitcher": "Z. Wheeler",
        "last_play": "Alvarez homered to right. Altuve scored.",
    },
    {
        "status": "Top 3rd",
        "away_score": 3,
        "home_score": 2,
        "away_innings": ["1", "0", "2"],
        "home_innings": ["2", "0", "-"],
        "away_hits": 5,
        "home_hits": 3,
        "away_errors": 0,
        "home_errors": 0,
        "balls": 1,
        "strikes": 0,
        "outs": 0,
        "on_first": True,
        "on_second": True,
        "on_third": False,
        "batter": "B. Harper",
        "pitcher": "H. Brown",
        "last_play": "Schwarber doubled. Turner scored; Stott to third.",
    },
    {
        "status": "Bottom 5th",
        "away_score": 3,
        "home_score": 4,
        "away_innings": ["1", "0", "2", "0", "0"],
        "home_innings": ["2", "0", "0", "0", "2"],
        "away_hits": 6,
        "home_hits": 7,
        "away_errors": 0,
        "home_errors": 1,
        "balls": 2,
        "strikes": 1,
        "outs": 2,
        "on_first": False,
        "on_second": True,
        "on_third": False,
        "batter": "J. Peña",
        "pitcher": "Z. Wheeler",
        "last_play": "Peña reached on an infield single. Houston has taken the lead.",
    },
    {
        "status": "Top 8th",
        "away_score": 5,
        "home_score": 4,
        "away_innings": ["1", "0", "2", "0", "0", "0", "0", "2"],
        "home_innings": ["2", "0", "0", "0", "2", "0", "0", "-"],
        "away_hits": 9,
        "home_hits": 8,
        "away_errors": 0,
        "home_errors": 1,
        "balls": 3,
        "strikes": 2,
        "outs": 2,
        "on_first": True,
        "on_second": False,
        "on_third": True,
        "batter": "B. Harper",
        "pitcher": "J. Hader",
        "last_play": "Harper fouled off a 3-2 fastball. Runners at the corners.",
    },
    {
        "status": "Top 9th",
        "away_score": 6,
        "home_score": 4,
        "away_innings": ["1", "0", "2", "0", "0", "0", "0", "2", "1"],
        "home_innings": ["2", "0", "0", "0", "2", "0", "0", "0", "-"],
        "away_hits": 11,
        "home_hits": 8,
        "away_errors": 0,
        "home_errors": 1,
        "balls": 1,
        "strikes": 2,
        "outs": 1,
        "on_first": True,
        "on_second": False,
        "on_third": False,
        "batter": "A. Bohm",
        "pitcher": "J. Hader",
        "last_play": "Harper singled home an insurance run. Phillies lead 6-4.",
    },
    {
        "status": "Final",
        "away_score": 6,
        "home_score": 4,
        "away_innings": ["1", "0", "2", "0", "0", "0", "0", "2", "1"],
        "home_innings": ["2", "0", "0", "0", "2", "0", "0", "0", "0"],
        "away_hits": 11,
        "home_hits": 8,
        "away_errors": 0,
        "home_errors": 1,
        "balls": 0,
        "strikes": 0,
        "outs": 3,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "",
        "pitcher": "",
        "last_play": "FINAL: Philadelphia wins 6-4. Simulator restarts in a few seconds.",
    },
]


@dataclass
class FakeSession:
    directory: Path
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    state: dict[str, Any] = field(default_factory=dict)
    state_index: int = 0
    last_error: str = ""


def _state(index: int) -> dict[str, Any]:
    raw = STATES[index % len(STATES)]
    return {
        "espn_event_id": "SIMULATED",
        "away": {
            "name": "Philadelphia Phillies",
            "abbr": "PHI",
            "score": str(raw["away_score"]),
            "record": "SIM",
            "stats": {
                "hits": str(raw["away_hits"]),
                "errors": str(raw["away_errors"]),
                "walks": "3",
                "strikeouts": "7",
            },
            "innings": list(raw["away_innings"]),
        },
        "home": {
            "name": "Houston Astros",
            "abbr": "HOU",
            "score": str(raw["home_score"]),
            "record": "SIM",
            "stats": {
                "hits": str(raw["home_hits"]),
                "errors": str(raw["home_errors"]),
                "walks": "2",
                "strikeouts": "8",
            },
            "innings": list(raw["home_innings"]),
        },
        "status": raw["status"],
        "balls": raw["balls"],
        "strikes": raw["strikes"],
        "outs": raw["outs"],
        "on_first": raw["on_first"],
        "on_second": raw["on_second"],
        "on_third": raw["on_third"],
        "batter": raw["batter"],
        "pitcher": raw["pitcher"],
        "last_play": raw["last_play"],
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulated": True,
    }


def render_frame(state: dict[str, Any], state_index: int) -> bytes:
    payload = live_stats.render_mlb_frame(state)
    image = Image.open(io.BytesIO(payload)).convert("RGB")
    draw = ImageDraw.Draw(image)
    # Cover the normal ESPN footer so nobody can mistake the simulator for live data.
    draw.rectangle((0, 668, image.width, image.height), fill=(8, 14, 24))
    label = (
        f"SIMULATED MLB DATA · state {state_index + 1}/{len(STATES)} · "
        f"changes every {STATE_SECONDS:g}s · {datetime.now().astimezone().strftime('%I:%M:%S %p')}"
    )
    draw.text((42, 682), label, font=live_stats._font(15), fill=(144, 160, 180))
    draw.rounded_rectangle((500, 18, 780, 48), radius=12, fill=(115, 62, 20))
    draw.text((545, 24), "SIMULATED GAME", font=live_stats._font(16, bold=True), fill=(255, 244, 226))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _worker(session: FakeSession) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return
    try:
        while not session.stop_event.is_set() and process.poll() is None:
            now = time.monotonic()
            if now - session.last_access_monotonic > IDLE_SECONDS:
                break
            elapsed = now - session.started_monotonic
            index = int(elapsed // STATE_SECONDS) % len(STATES)
            session.state_index = index
            session.state = _state(index)
            try:
                process.stdin.write(render_frame(session.state, index))
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                session.last_error = str(exc)
                break
            time.sleep(1.0 / FRAME_RATE)
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


def start_session() -> FakeSession:
    global _SESSION
    with _LOCK:
        current = _SESSION
        if current is not None and current.process is not None and current.process.poll() is None:
            current.last_access_monotonic = time.monotonic()
            return current

    if current is not None:
        stop_session()

    directory = ROOT / uuid.uuid4().hex
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
        raise RuntimeError(f"Could not start fake MLB stats ffmpeg: {exc}") from exc

    session = FakeSession(directory=directory, process=process)
    session.thread = threading.Thread(target=_worker, args=(session,), name="mlb-stats-fake-1-2", daemon=True)
    with _LOCK:
        _SESSION = session
    session.thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session()
            raise RuntimeError("Fake MLB stats ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTM3U" in text and text.count("#EXTINF") >= 2:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.10)

    stop_session()
    raise RuntimeError("Timed out waiting for the fake MLB stats stream to become ready.")


def stop_session() -> bool:
    global _SESSION
    with _LOCK:
        session = _SESSION
        _SESSION = None
    if session is None:
        return False
    session.stop_event.set()
    if session.process is not None:
        try:
            if session.process.stdin:
                session.process.stdin.close()
        except OSError:
            pass
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def get_session() -> FakeSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(filename: str) -> Path | None:
    session = get_session()
    if session is None and filename == "stream.m3u8":
        session = start_session()
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


def state_payload() -> dict[str, Any]:
    session = get_session()
    if session is not None:
        return {
            "guide_number": GUIDE_NUMBER,
            "active": True,
            "state_index": session.state_index,
            "state_count": len(STATES),
            "error": session.last_error,
            "state": session.state or _state(0),
        }
    return {
        "guide_number": GUIDE_NUMBER,
        "active": False,
        "state_index": 0,
        "state_count": len(STATES),
        "error": "",
        "state": _state(0),
    }


def inject_demo_channel(text: str, base_url: str) -> str:
    lines = str(text or "").splitlines()
    if any(f'tvg-id="{TVG_ID}"' in line for line in lines):
        return "\n".join(lines) + "\n"

    entry = [
        f'#EXTINF:-1 tvg-id="{TVG_ID}" tvg-chno="{GUIDE_NUMBER}" tvg-name="{DISPLAY_NAME}" group-title="Sports Stats Lab" x-sports-stats="mlb-fake",{DISPLAY_NAME}',
        f"{base_url.rstrip('/')}/sports/stats-fake/stream.m3u8",
    ]
    insert_at = 1 if lines and lines[0].startswith("#EXTM3U") else 0
    for index, line in enumerate(lines):
        if "/sports/stats-demo/1/stream.m3u8" in line:
            insert_at = index + 1
            break
    if not lines or not lines[0].startswith("#EXTM3U"):
        lines.insert(0, "#EXTM3U")
        insert_at = max(1, insert_at)
    lines[insert_at:insert_at] = entry
    return "\n".join(lines) + "\n"


def guide_item() -> dict[str, Any]:
    return {
        "number": GUIDE_NUMBER,
        "name": DISPLAY_NAME,
        "group": "Sports Stats Lab",
        "logo": "",
        "tvg_id": TVG_ID,
        "subtitle": "Continuously changing simulated MLB game",
        "generated": True,
        "play_url": "/guide/play/stats-fake/1.2",
        "stats_demo": True,
        "stats_fake": True,
    }
