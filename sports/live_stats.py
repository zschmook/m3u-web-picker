from __future__ import annotations

import io
import json
import re
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

import sports as _s
from media.ffmpeg import executable as ffmpeg_executable, terminate
from settings import SETTINGS


ESPN_MLB_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_MLB_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
STATS_ROOT = SETTINGS.cast_hls_dir / "sports-stats"
STATS_ROOT.mkdir(parents=True, exist_ok=True)

POLL_SECONDS = 3.0
FRAME_RATE = 2
IDLE_SECONDS = 60.0
STARTUP_TIMEOUT = 14.0

_LOCK = threading.RLock()
_SESSIONS: dict[int, "StatsSession"] = {}


@dataclass
class StatsSession:
    assigned_number: int
    event_key: str
    espn_event_id: str
    directory: Path
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    created_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_state: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""


def _http_json(url: str, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "M3U-Web-Picker/31 sports-stats",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("ESPN returned an unexpected response.")
    return data


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _generated_mlb_row(db_path: Path | str, assigned_number: int) -> dict | None:
    number = int(assigned_number)
    for row in _s.generated_rows(db_path):
        if int(row.get("assigned_number") or 0) != number:
            continue
        if str(row.get("league_id", "") or "").strip().lower() != "mlb":
            return None
        return row
    return None


def mlb_stats_rows(db_path: Path | str) -> list[dict]:
    return [
        row
        for row in _s.generated_rows(db_path)
        if str(row.get("league_id", "") or "").strip().lower() == "mlb"
        and int(row.get("assigned_number") or 0) > 0
    ]


def _event_date(row: dict) -> str:
    value = str(row.get("event_start", "") or "").strip()
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y%m%d")
        except ValueError:
            pass
    return datetime.now().astimezone().strftime("%Y%m%d")


def _team_aliases(competitor: dict) -> set[str]:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    values = {
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("location"),
        team.get("abbreviation"),
    }
    return {_norm(value) for value in values if _norm(value)}


def _event_match_score(row: dict, event: dict) -> int:
    haystack = _norm(
        " ".join(
            str(row.get(key, "") or "")
            for key in ("event_title", "display_name", "subtitle")
        )
    )
    competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
    if not competitions:
        return -1
    competitors = competitions[0].get("competitors") if isinstance(competitions[0], dict) else []
    if not isinstance(competitors, list) or len(competitors) < 2:
        return -1

    matched_teams = 0
    score = 0
    for competitor in competitors[:2]:
        aliases = _team_aliases(competitor if isinstance(competitor, dict) else {})
        best = 0
        for alias in aliases:
            if not alias:
                continue
            if alias in haystack:
                best = max(best, 8 + min(4, len(alias) // 5))
            else:
                words = [word for word in alias.split() if len(word) >= 4]
                overlap = sum(1 for word in words if word in haystack)
                best = max(best, overlap * 2)
        if best >= 4:
            matched_teams += 1
        score += best

    # Never bind a .1 channel to an ESPN game unless both teams can be accounted for.
    return score if matched_teams >= 2 else -1


def resolve_espn_event(row: dict) -> tuple[str, dict]:
    query = urllib.parse.urlencode({"dates": _event_date(row), "limit": "100"})
    data = _http_json(f"{ESPN_MLB_SCOREBOARD}?{query}")
    events = data.get("events") if isinstance(data.get("events"), list) else []
    ranked = sorted(
        (( _event_match_score(row, event), event) for event in events if isinstance(event, dict)),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0:
        title = str(row.get("event_title") or row.get("display_name") or "MLB game")
        raise RuntimeError(f"Could not match ESPN live data for {title}.")
    event = ranked[0][1]
    event_id = str(event.get("id", "") or "").strip()
    if not event_id:
        raise RuntimeError("Matched ESPN game did not include an event id.")
    return event_id, event


def _competitor_map(competition: dict) -> dict[str, dict]:
    output: dict[str, dict] = {}
    competitors = competition.get("competitors") if isinstance(competition.get("competitors"), list) else []
    for item in competitors:
        if not isinstance(item, dict):
            continue
        side = str(item.get("homeAway", "") or "").strip().lower()
        if side in {"home", "away"}:
            output[side] = item
    return output


def _team_payload(competitor: dict) -> dict:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    statistics = competitor.get("statistics") if isinstance(competitor.get("statistics"), list) else []
    stats = {
        str(item.get("name", "") or ""): str(item.get("displayValue", "") or item.get("value", "") or "")
        for item in statistics
        if isinstance(item, dict) and str(item.get("name", "") or "")
    }
    linescores = competitor.get("linescores") if isinstance(competitor.get("linescores"), list) else []
    innings = [
        str(item.get("displayValue", "") or item.get("value", "") or "")
        for item in linescores
        if isinstance(item, dict)
    ]
    records = competitor.get("records") if isinstance(competitor.get("records"), list) else []
    record = ""
    for item in records:
        if isinstance(item, dict) and str(item.get("type", "") or "") in {"total", "overall"}:
            record = str(item.get("summary", "") or "")
            break
    if not record and records and isinstance(records[0], dict):
        record = str(records[0].get("summary", "") or "")
    return {
        "name": str(team.get("displayName", "") or team.get("name", "") or "Team"),
        "abbr": str(team.get("abbreviation", "") or "").upper(),
        "score": str(competitor.get("score", "0") or "0"),
        "record": record,
        "logo": str(team.get("logo", "") or ""),
        "stats": stats,
        "innings": innings,
    }


def _find_boxscore_team_stats(summary: dict, team_abbr: str) -> dict[str, str]:
    boxscore = summary.get("boxscore") if isinstance(summary.get("boxscore"), dict) else {}
    teams = boxscore.get("teams") if isinstance(boxscore.get("teams"), list) else []
    wanted = _norm(team_abbr)
    for item in teams:
        if not isinstance(item, dict):
            continue
        team = item.get("team") if isinstance(item.get("team"), dict) else {}
        aliases = {_norm(team.get("abbreviation")), _norm(team.get("displayName")), _norm(team.get("name"))}
        if wanted not in aliases:
            continue
        statistics = item.get("statistics") if isinstance(item.get("statistics"), list) else []
        return {
            str(stat.get("name", "") or ""): str(stat.get("displayValue", "") or stat.get("value", "") or "")
            for stat in statistics
            if isinstance(stat, dict) and str(stat.get("name", "") or "")
        }
    return {}


def _athlete_name(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    athlete = value.get("athlete") if isinstance(value.get("athlete"), dict) else value
    return str(
        athlete.get("shortName", "")
        or athlete.get("displayName", "")
        or athlete.get("fullName", "")
        or ""
    )


def normalize_mlb_summary(summary: dict, *, espn_event_id: str = "") -> dict:
    header = summary.get("header") if isinstance(summary.get("header"), dict) else {}
    competitions = header.get("competitions") if isinstance(header.get("competitions"), list) else []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    competitors = _competitor_map(competition)
    away = _team_payload(competitors.get("away", {}))
    home = _team_payload(competitors.get("home", {}))
    away["stats"].update(_find_boxscore_team_stats(summary, away["abbr"]))
    home["stats"].update(_find_boxscore_team_stats(summary, home["abbr"]))

    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    situation = summary.get("situation") if isinstance(summary.get("situation"), dict) else {}
    last_play = situation.get("lastPlay") if isinstance(situation.get("lastPlay"), dict) else {}
    if not last_play:
        plays = summary.get("plays") if isinstance(summary.get("plays"), list) else []
        last_play = plays[-1] if plays and isinstance(plays[-1], dict) else {}

    state = {
        "espn_event_id": espn_event_id or str(header.get("id", "") or ""),
        "away": away,
        "home": home,
        "status": str(status_type.get("shortDetail", "") or status_type.get("detail", "") or status.get("displayClock", "") or "MLB"),
        "state": str(status_type.get("state", "") or ""),
        "period": int(status.get("period", 0) or 0),
        "clock": str(status.get("displayClock", "") or ""),
        "balls": int(situation.get("balls", 0) or 0),
        "strikes": int(situation.get("strikes", 0) or 0),
        "outs": int(situation.get("outs", 0) or 0),
        "on_first": bool(situation.get("onFirst")),
        "on_second": bool(situation.get("onSecond")),
        "on_third": bool(situation.get("onThird")),
        "batter": _athlete_name(situation.get("batter")),
        "pitcher": _athlete_name(situation.get("pitcher")),
        "last_play": str(last_play.get("text", "") or last_play.get("shortText", "") or ""),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return state


def fetch_mlb_state(espn_event_id: str) -> dict:
    query = urllib.parse.urlencode({"event": str(espn_event_id)})
    return normalize_mlb_summary(
        _http_json(f"{ESPN_MLB_SUMMARY}?{query}"),
        espn_event_id=str(espn_event_id),
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
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


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: object, *, size: int, bold: bool = False, fill=(238, 243, 249)) -> None:
    draw.text(xy, str(value or ""), font=_font(size, bold=bold), fill=fill)


def _stat_value(team: dict, *names: str, fallback: str = "-") -> str:
    stats = team.get("stats") if isinstance(team.get("stats"), dict) else {}
    for name in names:
        value = str(stats.get(name, "") or "").strip()
        if value:
            return value
    return fallback


def _diamond(draw: ImageDraw.ImageDraw, center: tuple[int, int], state: dict) -> None:
    cx, cy = center
    size = 26
    bases = [
        ((cx + 58, cy), bool(state.get("on_first"))),
        ((cx, cy - 58), bool(state.get("on_second"))),
        ((cx - 58, cy), bool(state.get("on_third"))),
    ]
    for (bx, by), occupied in bases:
        points = [(bx, by - size), (bx + size, by), (bx, by + size), (bx - size, by)]
        draw.polygon(points, fill=(70, 144, 255) if occupied else (34, 47, 65), outline=(120, 143, 170))
    home = [(cx, cy + 32), (cx + 20, cy + 52), (cx + 14, cy + 76), (cx - 14, cy + 76), (cx - 20, cy + 52)]
    draw.polygon(home, fill=(34, 47, 65), outline=(120, 143, 170))


def render_mlb_frame(state: dict, *, width: int = 1280, height: int = 720) -> bytes:
    image = Image.new("RGB", (width, height), (8, 14, 24))
    draw = ImageDraw.Draw(image)
    panel = (17, 27, 41)
    panel2 = (22, 35, 52)
    muted = (144, 160, 180)
    accent = (79, 140, 255)

    draw.rounded_rectangle((32, 26, width - 32, 164), radius=22, fill=panel, outline=(42, 58, 78), width=2)
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    _text(draw, (60, 46), away.get("abbr") or away.get("name"), size=38, bold=True)
    _text(draw, (60, 92), away.get("record"), size=20, fill=muted)
    _text(draw, (410, 48), away.get("score", "0"), size=72, bold=True)

    _text(draw, (width - 470, 48), home.get("score", "0"), size=72, bold=True)
    home_label = str(home.get("abbr") or home.get("name") or "HOME")
    bbox = draw.textbbox((0, 0), home_label, font=_font(38, bold=True))
    _text(draw, (width - 60 - (bbox[2] - bbox[0]), 46), home_label, size=38, bold=True)
    record = str(home.get("record", "") or "")
    rb = draw.textbbox((0, 0), record, font=_font(20))
    _text(draw, (width - 60 - (rb[2] - rb[0]), 92), record, size=20, fill=muted)

    status = str(state.get("status", "MLB") or "MLB")
    sb = draw.textbbox((0, 0), status, font=_font(26, bold=True))
    _text(draw, ((width - (sb[2] - sb[0])) // 2, 62), status, size=26, bold=True, fill=accent)

    draw.rounded_rectangle((32, 184, 760, 484), radius=20, fill=panel, outline=(42, 58, 78), width=2)
    _text(draw, (58, 205), "LINE SCORE", size=20, bold=True, fill=muted)
    inning_count = max(9, len(away.get("innings", [])), len(home.get("innings", [])))
    inning_count = min(inning_count, 12)
    col_x = [190 + i * 45 for i in range(inning_count)]
    for i, x in enumerate(col_x, start=1):
        _text(draw, (x, 248), i, size=18, bold=True, fill=muted)
    for y, team in ((294, away), (347, home)):
        _text(draw, (60, y), team.get("abbr"), size=24, bold=True)
        innings = list(team.get("innings", []))
        for index, x in enumerate(col_x):
            _text(draw, (x, y), innings[index] if index < len(innings) else "-", size=20)

    table_x = 190 + inning_count * 45 + 24
    for offset, label in enumerate(("R", "H", "E")):
        _text(draw, (table_x + offset * 58, 248), label, size=18, bold=True, fill=muted)
    for y, team in ((294, away), (347, home)):
        values = (
            team.get("score", "0"),
            _stat_value(team, "hits", "H"),
            _stat_value(team, "errors", "E", fallback="0"),
        )
        for offset, value in enumerate(values):
            _text(draw, (table_x + offset * 58, y), value, size=20, bold=(offset == 0))

    stat_rows = [
        ("Hits", _stat_value(away, "hits"), _stat_value(home, "hits")),
        ("Walks", _stat_value(away, "walks", "baseOnBalls"), _stat_value(home, "walks", "baseOnBalls")),
        ("Strikeouts", _stat_value(away, "strikeouts", "totalStrikeouts"), _stat_value(home, "strikeouts", "totalStrikeouts")),
        ("Errors", _stat_value(away, "errors", fallback="0"), _stat_value(home, "errors", fallback="0")),
    ]
    _text(draw, (58, 408), "TEAM STATS", size=18, bold=True, fill=muted)
    x = 200
    for label, av, hv in stat_rows:
        _text(draw, (x, 445), label, size=15, fill=muted)
        _text(draw, (x, 468), f"{av}  /  {hv}", size=18, bold=True)
        x += 132

    draw.rounded_rectangle((782, 184, width - 32, 484), radius=20, fill=panel, outline=(42, 58, 78), width=2)
    _text(draw, (808, 205), "AT BAT", size=20, bold=True, fill=muted)
    _diamond(draw, (1016, 318), state)
    _text(draw, (808, 260), f"B  {state.get('balls', 0)}", size=24, bold=True)
    _text(draw, (808, 302), f"S  {state.get('strikes', 0)}", size=24, bold=True)
    _text(draw, (808, 344), f"O  {state.get('outs', 0)}", size=24, bold=True)
    batter = str(state.get("batter", "") or "")
    pitcher = str(state.get("pitcher", "") or "")
    if batter:
        _text(draw, (808, 402), f"Batter: {batter}", size=18, bold=True)
    if pitcher:
        _text(draw, (808, 432), f"Pitcher: {pitcher}", size=18)

    draw.rounded_rectangle((32, 504, width - 32, 658), radius=20, fill=panel2, outline=(42, 58, 78), width=2)
    _text(draw, (58, 525), "LAST PLAY", size=18, bold=True, fill=muted)
    last_play = str(state.get("last_play", "") or "Waiting for live play data...")
    # Simple line wrapping tuned for a 1280x720 stats screen.
    words = last_play.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > 78 and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    for index, line in enumerate(lines[:3]):
        _text(draw, (58, 560 + index * 30), line, size=21, bold=(index == 0))

    footer = f"ESPN live data • event {state.get('espn_event_id', '')} • updated {str(state.get('updated_at', ''))[11:19]}"
    _text(draw, (42, 682), footer, size=15, fill=muted)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _ffmpeg_command(directory: Path) -> list[str]:
    playlist = directory / "stream.m3u8"
    segments = directory / "segment_%06d.ts"
    return [
        ffmpeg_executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-framerate",
        str(FRAME_RATE),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FRAME_RATE),
        "-g",
        str(FRAME_RATE * 2),
        "-keyint_min",
        str(FRAME_RATE * 2),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ac",
        "2",
        "-ar",
        "48000",
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
        str(segments),
        str(playlist),
    ]


def _stop_session_locked(number: int) -> StatsSession | None:
    session = _SESSIONS.pop(int(number), None)
    if session is not None:
        session.stop_event.set()
    return session


def stop_session(assigned_number: int) -> bool:
    with _LOCK:
        session = _stop_session_locked(int(assigned_number))
    if session is None:
        return False
    process = session.process
    if process is not None:
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        terminate(process)
    try:
        shutil.rmtree(session.directory, ignore_errors=True)
    except Exception:
        pass
    return True


def _worker(session: StatsSession) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return
    next_poll = 0.0
    frame = render_mlb_frame({
        "away": {"abbr": "MLB", "score": "-", "stats": {}, "innings": []},
        "home": {"abbr": "STATS", "score": "-", "stats": {}, "innings": []},
        "status": "Connecting to ESPN...",
        "espn_event_id": session.espn_event_id,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    try:
        while not session.stop_event.is_set() and process.poll() is None:
            now = time.monotonic()
            if now - session.last_access_monotonic > IDLE_SECONDS:
                break
            if now >= next_poll:
                try:
                    state = fetch_mlb_state(session.espn_event_id)
                    session.last_state = state
                    session.last_error = ""
                    frame = render_mlb_frame(state)
                except Exception as exc:
                    session.last_error = str(exc)
                next_poll = now + POLL_SECONDS
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
            current = _SESSIONS.get(session.assigned_number)
            if current is session:
                _SESSIONS.pop(session.assigned_number, None)
        try:
            shutil.rmtree(session.directory, ignore_errors=True)
        except Exception:
            pass


def start_session(db_path: Path | str, assigned_number: int) -> StatsSession:
    number = int(assigned_number)
    row = _generated_mlb_row(db_path, number)
    if row is None:
        raise RuntimeError("MLB stats channel was not found.")

    event_key = str(row.get("event_key", "") or "")
    with _LOCK:
        current = _SESSIONS.get(number)
        if current is not None and current.event_key == event_key and current.process is not None and current.process.poll() is None:
            current.last_access_monotonic = time.monotonic()
            return current
    if current is not None:
        stop_session(number)

    espn_event_id, _scoreboard_event = resolve_espn_event(row)
    directory = STATS_ROOT / str(number)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)

    try:
        process = subprocess.Popen(
            _ffmpeg_command(directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start MLB stats ffmpeg: {exc}") from exc

    session = StatsSession(
        assigned_number=number,
        event_key=event_key,
        espn_event_id=espn_event_id,
        directory=directory,
        process=process,
    )
    thread = threading.Thread(target=_worker, args=(session,), name=f"mlb-stats-{number}", daemon=True)
    session.thread = thread
    with _LOCK:
        _SESSIONS[number] = session
    thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session(number)
            raise RuntimeError("MLB stats ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTM3U" in text and text.count("#EXTINF") >= 2:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.10)

    stop_session(number)
    raise RuntimeError("Timed out waiting for the MLB stats stream to become ready.")


def get_session(assigned_number: int) -> StatsSession | None:
    number = int(assigned_number)
    with _LOCK:
        session = _SESSIONS.get(number)
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(db_path: Path | str, assigned_number: int, filename: str) -> Path | None:
    session = get_session(assigned_number)
    if session is None and filename == "stream.m3u8":
        session = start_session(db_path, assigned_number)
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


def state_payload(db_path: Path | str, assigned_number: int) -> dict:
    number = int(assigned_number)
    row = _generated_mlb_row(db_path, number)
    if row is None:
        raise RuntimeError("MLB stats channel was not found.")
    session = get_session(number)
    if session is not None and session.last_state:
        return {
            "assigned_number": number,
            "stats_number": f"{number}.1",
            "event_key": str(row.get("event_key", "") or ""),
            "espn_event_id": session.espn_event_id,
            "active": True,
            "error": session.last_error,
            "state": session.last_state,
        }
    event_id, _event = resolve_espn_event(row)
    return {
        "assigned_number": number,
        "stats_number": f"{number}.1",
        "event_key": str(row.get("event_key", "") or ""),
        "espn_event_id": event_id,
        "active": False,
        "error": "",
        "state": fetch_mlb_state(event_id),
    }


def inject_stats_channels(text: str, db_path: Path | str, base_url: str) -> str:
    rows = {int(row["assigned_number"]): row for row in mlb_stats_rows(db_path)}
    if not rows:
        return text
    output: list[str] = []
    for line in str(text or "").splitlines():
        output.append(line)
        match = re.search(r"/sports/stream/(\d+)(?:\?.*)?$", line.strip())
        if not match:
            continue
        number = int(match.group(1))
        row = rows.get(number)
        if row is None:
            continue
        stats_number = f"{number}.1"
        event_title = str(row.get("event_title") or row.get("display_name") or "MLB Game").strip()
        display_name = f"MLB Stats · {event_title}"
        logo = str(row.get("tvg_logo", "") or "").strip()
        attrs = [
            f'tvg-id="m3u-picker-sports-stats-{number}"',
            f'tvg-chno="{stats_number}"',
            f'tvg-name="{display_name.replace(chr(34), chr(39))}"',
            f'group-title="{str(row.get("group_title", "Sports Today") or "Sports Today").replace(chr(34), chr(39))}"',
            'x-sports-stats="mlb"',
            f'x-sports-parent="{number}"',
        ]
        if logo:
            attrs.append(f'tvg-logo="{logo.replace(chr(34), chr(39))}"')
        output.append(f"#EXTINF:-1 {' '.join(attrs)},{display_name}")
        output.append(f"{base_url.rstrip('/')}/sports/stats/{number}/stream.m3u8")
    return "\n".join(output) + "\n"
