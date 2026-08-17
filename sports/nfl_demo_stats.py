from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from media.ffmpeg import terminate
from settings import SETTINGS
from . import live_stats


DEMO_NUMBER = 1
DEMO_GUIDE_NUMBER = "1.1"
DEMO_DATE = "20260815"
DEMO_AWAY = "PHI"
DEMO_HOME = "BAL"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
ROOT = SETTINGS.cast_hls_dir / "sports-stats-demo"
ROOT.mkdir(parents=True, exist_ok=True)

FRAME_RATE = 2
IDLE_SECONDS = 60.0
STARTUP_TIMEOUT = 14.0

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

_LOCK = threading.RLock()
_SESSION: "DemoSession | None" = None


@dataclass
class DemoSession:
    event_id: str
    directory: Path
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    state: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""


def _json(url: str, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("ESPN returned an unexpected response.")
    return data


def _competitors(competition: dict) -> dict[str, dict]:
    output: dict[str, dict] = {}
    rows = competition.get("competitors") if isinstance(competition.get("competitors"), list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        side = str(item.get("homeAway", "") or "").lower()
        if side in {"home", "away"}:
            output[side] = item
    return output


def _abbr(competitor: dict) -> str:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    return str(team.get("abbreviation", "") or "").upper()


def _resolve_event() -> tuple[str, dict]:
    query = urllib.parse.urlencode({"dates": DEMO_DATE, "seasontype": "1", "limit": "100"})
    data = _json(f"{SCOREBOARD_URL}?{query}")
    events = data.get("events") if isinstance(data.get("events"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
        teams = _competitors(competition)
        if _abbr(teams.get("away", {})) == DEMO_AWAY and _abbr(teams.get("home", {})) == DEMO_HOME:
            event_id = str(event.get("id", "") or "").strip()
            if event_id:
                return event_id, event
    raise RuntimeError("ESPN did not return the 2026 Eagles at Ravens preseason game.")


def _team_payload(competitor: dict) -> dict:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    linescores = competitor.get("linescores") if isinstance(competitor.get("linescores"), list) else []
    records = competitor.get("records") if isinstance(competitor.get("records"), list) else []
    record = ""
    if records and isinstance(records[0], dict):
        record = str(records[0].get("summary", "") or "")
    return {
        "name": str(team.get("displayName", "") or team.get("name", "") or "Team"),
        "abbr": str(team.get("abbreviation", "") or "").upper(),
        "score": str(competitor.get("score", "0") or "0"),
        "record": record,
        "quarters": [
            str(item.get("displayValue", "") or item.get("value", "") or "0")
            for item in linescores
            if isinstance(item, dict)
        ],
        "stats": {},
    }


def _boxscore_stats(summary: dict, abbreviation: str) -> dict[str, str]:
    boxscore = summary.get("boxscore") if isinstance(summary.get("boxscore"), dict) else {}
    teams = boxscore.get("teams") if isinstance(boxscore.get("teams"), list) else []
    wanted = str(abbreviation or "").upper()
    for item in teams:
        if not isinstance(item, dict):
            continue
        team = item.get("team") if isinstance(item.get("team"), dict) else {}
        if str(team.get("abbreviation", "") or "").upper() != wanted:
            continue
        statistics = item.get("statistics") if isinstance(item.get("statistics"), list) else []
        return {
            str(stat.get("name", "") or ""): str(stat.get("displayValue", "") or stat.get("value", "") or "")
            for stat in statistics
            if isinstance(stat, dict) and str(stat.get("name", "") or "")
        }
    return {}


def _normalize(event_id: str, event: dict, summary: dict | None = None) -> dict:
    source = summary if isinstance(summary, dict) else {}
    header = source.get("header") if isinstance(source.get("header"), dict) else {}
    competitions = header.get("competitions") if isinstance(header.get("competitions"), list) else []
    if competitions and isinstance(competitions[0], dict):
        competition = competitions[0]
    else:
        event_competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
        competition = event_competitions[0] if event_competitions and isinstance(event_competitions[0], dict) else {}

    teams = _competitors(competition)
    away = _team_payload(teams.get("away", {}))
    home = _team_payload(teams.get("home", {}))
    if source:
        away["stats"] = _boxscore_stats(source, away["abbr"])
        home["stats"] = _boxscore_stats(source, home["abbr"])

    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    last_play = "Completed-game ESPN data"
    plays = source.get("plays") if isinstance(source.get("plays"), list) else []
    if plays and isinstance(plays[-1], dict):
        last_play = str(plays[-1].get("text", "") or plays[-1].get("shortText", "") or last_play)

    return {
        "event_id": event_id,
        "away": away,
        "home": home,
        "status": str(status_type.get("shortDetail", "") or status_type.get("detail", "") or "Final"),
        "last_play": last_play,
        "data_source": "summary" if source else "scoreboard",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def fetch_demo_state() -> dict:
    event_id, event = _resolve_event()
    query = urllib.parse.urlencode({"event": event_id})
    try:
        summary = _json(f"{SUMMARY_URL}?{query}")
    except Exception:
        summary = None
    return _normalize(event_id, event, summary)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf")
        if bold
        else ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "Arial.ttf", "arial.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: object, size: int, *, bold: bool = False, fill=(238, 243, 249)) -> None:
    draw.text(xy, str(text or ""), font=_font(size, bold), fill=fill)


def _stat(team: dict, *names: str, fallback: str = "-") -> str:
    stats = team.get("stats") if isinstance(team.get("stats"), dict) else {}
    for name in names:
        value = str(stats.get(name, "") or "").strip()
        if value:
            return value
    return fallback


def render_frame(state: dict) -> bytes:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), (8, 14, 24))
    draw = ImageDraw.Draw(image)
    panel = (17, 27, 41)
    panel2 = (22, 35, 52)
    muted = (145, 161, 181)
    accent = (79, 140, 255)

    draw.rounded_rectangle((32, 24, width - 32, 176), radius=22, fill=panel, outline=(42, 58, 78), width=2)
    _draw_text(draw, (58, 42), "NFL PRESEASON · EXPERIMENTAL 1.1", 20, bold=True, fill=muted)
    _draw_text(draw, (58, 82), state.get("away", {}).get("abbr", "PHI"), 44, bold=True)
    _draw_text(draw, (418, 70), state.get("away", {}).get("score", "-"), 80, bold=True)
    _draw_text(draw, (width - 500, 70), state.get("home", {}).get("score", "-"), 80, bold=True)
    home_label = str(state.get("home", {}).get("abbr", "BAL"))
    bbox = draw.textbbox((0, 0), home_label, font=_font(44, True))
    _draw_text(draw, (width - 58 - (bbox[2] - bbox[0]), 82), home_label, 44, bold=True)
    status = str(state.get("status", "FINAL") or "FINAL")
    sb = draw.textbbox((0, 0), status, font=_font(30, True))
    _draw_text(draw, ((width - (sb[2] - sb[0])) // 2, 92), status, 30, bold=True, fill=accent)

    draw.rounded_rectangle((32, 196, width - 32, 350), radius=20, fill=panel, outline=(42, 58, 78), width=2)
    _draw_text(draw, (58, 214), "SCORING BY QUARTER", 18, bold=True, fill=muted)
    headers = ["Q1", "Q2", "Q3", "Q4", "T"]
    xs = [360, 500, 640, 780, 940]
    for x, label in zip(xs, headers):
        _draw_text(draw, (x, 246), label, 20, bold=True, fill=muted)
    for y, team in ((282, state.get("away", {})), (318, state.get("home", {}))):
        _draw_text(draw, (58, y), team.get("abbr", ""), 24, bold=True)
        quarters = list(team.get("quarters", []))[:4]
        values = quarters + [str(team.get("score", "-"))]
        while len(values) < 5:
            values.insert(-1 if values else 0, "-")
        for x, value in zip(xs, values[:5]):
            _draw_text(draw, (x, y), value, 22, bold=(x == xs[-1]))

    draw.rounded_rectangle((32, 370, width - 32, 596), radius=20, fill=panel2, outline=(42, 58, 78), width=2)
    _draw_text(draw, (58, 390), "TEAM STATS", 18, bold=True, fill=muted)
    away = state.get("away", {})
    home = state.get("home", {})
    rows = [
        ("First Downs", ("firstDowns",), "-"),
        ("Total Yards", ("totalYards",), "-"),
        ("Passing Yards", ("netPassingYards", "passingYards"), "-"),
        ("Rushing Yards", ("rushingYards",), "-"),
        ("3rd Down", ("thirdDownEff",), "-"),
        ("Turnovers", ("turnovers",), "-"),
    ]
    _draw_text(draw, (85, 430), away.get("abbr", "PHI"), 22, bold=True, fill=accent)
    _draw_text(draw, (width - 140, 430), home.get("abbr", "BAL"), 22, bold=True, fill=accent)
    for index, (label, keys, fallback) in enumerate(rows):
        y = 468 + index * 20
        av = _stat(away, *keys, fallback=fallback)
        hv = _stat(home, *keys, fallback=fallback)
        _draw_text(draw, (85, y), av, 17, bold=True)
        lb = draw.textbbox((0, 0), label, font=_font(17, False))
        _draw_text(draw, ((width - (lb[2] - lb[0])) // 2, y), label, 17, fill=muted)
        hb = draw.textbbox((0, 0), hv, font=_font(17, True))
        _draw_text(draw, (width - 85 - (hb[2] - hb[0]), y), hv, 17, bold=True)

    draw.rounded_rectangle((32, 616, width - 32, 674), radius=16, fill=panel, outline=(42, 58, 78), width=2)
    last_play = str(state.get("last_play", "Completed-game ESPN data") or "Completed-game ESPN data")
    if len(last_play) > 112:
        last_play = last_play[:109] + "..."
    _draw_text(draw, (54, 634), last_play, 17)

    now = datetime.now().astimezone().strftime("%I:%M:%S %p")
    footer = f"ESPN {state.get('data_source', 'scoreboard')} · completed game demo · stream clock {now}"
    _draw_text(draw, (42, 690), footer, 14, fill=muted)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _worker(session: DemoSession) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return
    try:
        state = fetch_demo_state()
        session.state = state
    except Exception as exc:
        session.last_error = str(exc)
        state = {
            "away": {"abbr": "PHI", "score": "-", "quarters": [], "stats": {}},
            "home": {"abbr": "BAL", "score": "-", "quarters": [], "stats": {}},
            "status": "ESPN ERROR",
            "last_play": str(exc),
            "data_source": "error",
        }

    frame = b""
    next_render = 0.0
    try:
        while not session.stop_event.is_set() and process.poll() is None:
            now = time.monotonic()
            if now - session.last_access_monotonic > IDLE_SECONDS:
                break
            if now >= next_render or not frame:
                frame = render_frame(state)
                next_render = now + 1.0
            try:
                process.stdin.write(frame)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
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


def start_session() -> DemoSession:
    global _SESSION
    with _LOCK:
        current = _SESSION
        if current is not None and current.process is not None and current.process.poll() is None:
            current.last_access_monotonic = time.monotonic()
            return current

    event_id, _event = _resolve_event()
    directory = ROOT / "1"
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
        raise RuntimeError(f"Could not start NFL demo ffmpeg: {exc}") from exc

    session = DemoSession(event_id=event_id, directory=directory, process=process)
    session.thread = threading.Thread(target=_worker, args=(session,), name="nfl-stats-demo-1", daemon=True)
    with _LOCK:
        _SESSION = session
    session.thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session()
            raise RuntimeError("NFL demo ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTM3U" in text and text.count("#EXTINF") >= 2:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.10)

    stop_session()
    raise RuntimeError("Timed out waiting for the NFL demo stats stream to become ready.")


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


def get_session() -> DemoSession | None:
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


def state_payload() -> dict:
    session = get_session()
    if session is not None and session.state:
        return {
            "guide_number": DEMO_GUIDE_NUMBER,
            "event_id": session.event_id,
            "active": True,
            "error": session.last_error,
            "state": session.state,
        }
    state = fetch_demo_state()
    return {
        "guide_number": DEMO_GUIDE_NUMBER,
        "event_id": state.get("event_id", ""),
        "active": False,
        "error": "",
        "state": state,
    }


def inject_demo_channel(text: str, base_url: str) -> str:
    lines = str(text or "").splitlines()
    entry = [
        '#EXTINF:-1 tvg-id="m3u-picker-sports-stats-demo-1" tvg-chno="1.1" tvg-name="NFL Stats Demo · Eagles at Ravens" group-title="Sports Stats Lab" x-sports-stats="nfl-demo",NFL Stats Demo · Eagles at Ravens',
        f"{base_url.rstrip('/')}/sports/stats-demo/1/stream.m3u8",
    ]
    if lines and lines[0].startswith("#EXTM3U"):
        lines[1:1] = entry
    else:
        lines = ["#EXTM3U", *entry, *lines]
    return "\n".join(lines) + "\n"
