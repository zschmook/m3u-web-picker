from __future__ import annotations

import io
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from media.ffmpeg import executable as ffmpeg_executable, terminate

from . import live_stats


CHANNEL_NUMBER = "0.10"
DISPLAY_NAME = "Sports Alert Demo"
TVG_ID = "m3u-picker-sports-alert-demo"
GROUP_TITLE = "Sports Stats Lab"
PLAY_URL = "/guide/play/alert-demo/0.10"
STREAM_PATH = "/sports/alert-demo/stream.m3u8"
PARENT_CHANNEL_NUMBER = "1"

FRAME_WIDTH = 640
FRAME_HEIGHT = 220
FRAME_RATE = 2
ALERT_SLOT_SECONDS = 10.0
ALERT_VISIBLE_SECONDS = 7.0
STARTUP_TIMEOUT = 24.0
DEMO_SEED = 0x010
DEMO_PREVIEW_COUNT = 8
LOGO_SIZE = 72

_LOCK = threading.RLock()
_SESSION: "AlertDemoSession | None" = None
_LOGO_LOCK = threading.RLock()
_LOGO_CACHE: dict[str, Image.Image | None] = {}


@dataclass(frozen=True)
class DemoTeam:
    league: str
    name: str
    abbr: str
    primary: tuple[int, int, int]
    secondary: tuple[int, int, int]

    @property
    def logo_url(self) -> str:
        league_slug = "mlb" if self.league == "MLB" else "nfl"
        return f"https://a.espncdn.com/i/teamlogos/{league_slug}/500/{self.abbr.lower()}.png"


@dataclass(frozen=True)
class DemoAlert:
    league: str
    scoring_team: DemoTeam
    away: DemoTeam
    home: DemoTeam
    away_score: int
    home_score: int
    play: str
    source_channel: str


@dataclass
class AlertDemoSession:
    directory: Path
    parent_url: str
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""


_MLB_TEAMS = (
    DemoTeam("MLB", "Philadelphia Phillies", "PHI", (232, 24, 40), (0, 45, 114)),
    DemoTeam("MLB", "New York Mets", "NYM", (0, 45, 114), (255, 89, 16)),
    DemoTeam("MLB", "New York Yankees", "NYY", (12, 35, 64), (196, 206, 211)),
    DemoTeam("MLB", "Boston Red Sox", "BOS", (189, 48, 57), (12, 35, 64)),
    DemoTeam("MLB", "Chicago Cubs", "CHC", (14, 51, 134), (204, 52, 51)),
    DemoTeam("MLB", "St. Louis Cardinals", "STL", (196, 30, 58), (12, 35, 64)),
    DemoTeam("MLB", "Los Angeles Dodgers", "LAD", (0, 90, 156), (239, 62, 66)),
    DemoTeam("MLB", "San Francisco Giants", "SF", (253, 90, 30), (39, 37, 31)),
)

_NFL_TEAMS = (
    DemoTeam("NFL", "Philadelphia Eagles", "PHI", (0, 76, 84), (165, 172, 175)),
    DemoTeam("NFL", "Dallas Cowboys", "DAL", (0, 53, 148), (134, 147, 151)),
    DemoTeam("NFL", "Kansas City Chiefs", "KC", (227, 24, 55), (255, 184, 28)),
    DemoTeam("NFL", "Buffalo Bills", "BUF", (0, 51, 141), (198, 12, 48)),
    DemoTeam("NFL", "Detroit Lions", "DET", (0, 118, 182), (176, 183, 188)),
    DemoTeam("NFL", "Green Bay Packers", "GB", (24, 48, 40), (255, 184, 28)),
    DemoTeam("NFL", "Baltimore Ravens", "BAL", (36, 23, 115), (158, 124, 12)),
    DemoTeam("NFL", "Pittsburgh Steelers", "PIT", (16, 24, 32), (255, 182, 18)),
)

_MLB_PLAYS = (
    "{player} — solo home run",
    "{player} — 2-run home run",
    "{player} — 3-run home run",
    "{player} — RBI double",
    "{player} — 2-run single",
    "{player} — sacrifice fly",
)

_NFL_PLAYS = (
    "{player} — {yards}-yard rushing TD",
    "{player} — {yards}-yard TD reception",
    "{player} — {yards}-yard pick-six",
    "{player} — kickoff return TD",
    "{player} — punt return TD",
)

_DEMO_PLAYERS = (
    "Jordan Vega",
    "Marcus Reed",
    "Tyler Brooks",
    "Alex Ramirez",
    "Chris Daniels",
    "Devin Carter",
    "Ryan Knox",
    "Malik Hayes",
    "Evan Cole",
    "Nico Bennett",
)


def guide_item() -> dict:
    return {
        "number": CHANNEL_NUMBER,
        "name": DISPLAY_NAME,
        "group": GROUP_TITLE,
        "logo": "",
        "tvg_id": TVG_ID,
        "subtitle": "Channel 1 with simulated MLB/NFL scoring notifications",
        "generated": True,
        "play_url": PLAY_URL,
        "sports_alert_demo": True,
    }


def inject_demo_channel(text: str, base_url: str) -> str:
    payload = str(text or "")
    if f'tvg-id="{TVG_ID}"' in payload:
        return payload
    lines = payload.splitlines()
    entry = [
        (
            f'#EXTINF:-1 tvg-id="{TVG_ID}" tvg-chno="{CHANNEL_NUMBER}" '
            f'tvg-name="{DISPLAY_NAME}" group-title="{GROUP_TITLE}",{DISPLAY_NAME}'
        ),
        f"{base_url.rstrip('/')}{STREAM_PATH}",
    ]
    insert_at = 1 if lines and lines[0].startswith("#EXTM3U") else 0
    lines[insert_at:insert_at] = entry
    return "\n".join(lines) + "\n"


def _font(size: int, *, bold: bool = False):
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


def _fit_text(draw: ImageDraw.ImageDraw, value: str, *, max_width: int, start_size: int, minimum: int = 18):
    size = start_size
    while size > minimum:
        font = _font(size, bold=True)
        box = draw.textbbox((0, 0), value, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 1
    return _font(minimum, bold=True)


def _demo_alert_for_slot(slot: int) -> DemoAlert:
    rng = random.Random(DEMO_SEED + max(0, int(slot)) * 1009)
    league = rng.choice(("MLB", "NFL"))
    teams = _MLB_TEAMS if league == "MLB" else _NFL_TEAMS
    away, home = rng.sample(teams, 2)
    scoring_team = rng.choice((away, home))
    player = rng.choice(_DEMO_PLAYERS)

    if league == "MLB":
        away_score = rng.randint(0, 8)
        home_score = rng.randint(0, 8)
        scoring_runs = rng.choice((1, 1, 1, 2, 2, 3))
        if scoring_team is away:
            away_score = max(away_score, home_score - 2) + scoring_runs
        else:
            home_score = max(home_score, away_score - 2) + scoring_runs
        play = rng.choice(_MLB_PLAYS).format(player=player)
    else:
        away_score = rng.choice(range(3, 39))
        home_score = rng.choice(range(3, 39))
        if scoring_team is away:
            away_score += 6
        else:
            home_score += 6
        play = rng.choice(_NFL_PLAYS).format(
            player=player,
            yards=rng.choice((1, 3, 6, 8, 12, 18, 24, 31, 42, 58, 73)),
        )

    # Demo destinations intentionally stay in the generated sports-channel
    # range. The production alert selector will be sports-only and UI-gated.
    source_channel = str(rng.choice((1000, 1001, 1010, 1020, 1100, 1200)))
    return DemoAlert(
        league=league,
        scoring_team=scoring_team,
        away=away,
        home=home,
        away_score=away_score,
        home_score=home_score,
        play=play,
        source_channel=source_channel,
    )


def _current_alert(elapsed: float) -> tuple[int, DemoAlert | None]:
    slot = int(max(0.0, elapsed) // ALERT_SLOT_SECONDS)
    within = max(0.0, elapsed) % ALERT_SLOT_SECONDS
    if within >= ALERT_VISIBLE_SECONDS:
        return slot, None
    return slot, _demo_alert_for_slot(slot)


def _team_payload(team: DemoTeam, score: int) -> dict:
    return {
        "name": team.name,
        "abbr": team.abbr,
        "score": int(score),
        "logo_url": team.logo_url,
    }


def _alert_payload(alert: DemoAlert | None) -> dict | None:
    if alert is None:
        return None
    return {
        "league": alert.league,
        "scoring_team": alert.scoring_team.name,
        "play": alert.play,
        "away": _team_payload(alert.away, alert.away_score),
        "home": _team_payload(alert.home, alert.home_score),
        "source_channel": alert.source_channel,
    }


def _fallback_team_icon(team: DemoTeam, size: int = LOGO_SIZE) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon, "RGBA")
    pad = max(3, size // 18)
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=max(12, size // 4),
        fill=(*team.primary, 255),
        outline=(*team.secondary, 255),
        width=max(3, size // 14),
    )
    # Deliberately graphical only: no team abbreviation text. The real alert
    # renderer can drop actual logos into this exact slot.
    inset = max(16, size // 4)
    draw.ellipse(
        (inset, inset, size - inset, size - inset),
        fill=(*team.secondary, 255),
    )
    return icon


def _fetch_logo(team: DemoTeam) -> Image.Image | None:
    request = urllib.request.Request(
        team.logo_url,
        headers={"User-Agent": "M3U-Web-Picker sports-alert-demo"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            data = response.read(512 * 1024)
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        image.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(
            image,
            (
                (LOGO_SIZE - image.width) // 2,
                (LOGO_SIZE - image.height) // 2,
            ),
        )
        return canvas
    except Exception:
        return None


def _team_icon(team: DemoTeam) -> Image.Image:
    key = team.logo_url
    with _LOGO_LOCK:
        if key in _LOGO_CACHE:
            cached = _LOGO_CACHE[key]
            return cached.copy() if cached is not None else _fallback_team_icon(team)

    fetched = _fetch_logo(team)
    with _LOGO_LOCK:
        _LOGO_CACHE[key] = fetched
    return fetched.copy() if fetched is not None else _fallback_team_icon(team)


def _score_row(image: Image.Image, draw: ImageDraw.ImageDraw, alert: DemoAlert) -> None:
    y = 120
    away_icon = _team_icon(alert.away)
    home_icon = _team_icon(alert.home)
    image.alpha_composite(away_icon, (95, y - 20))
    image.alpha_composite(home_icon, (FRAME_WIDTH - 95 - LOGO_SIZE, y - 20))

    score_font = _font(48, bold=True)
    separator_font = _font(32, bold=True)
    away_score = str(alert.away_score)
    home_score = str(alert.home_score)

    away_box = draw.textbbox((0, 0), away_score, font=score_font)
    away_width = away_box[2] - away_box[0]

    center = FRAME_WIDTH // 2
    draw.text(
        (center - 58 - away_width, y - 8),
        away_score,
        font=score_font,
        fill=(255, 255, 255, 255),
    )
    draw.text(
        (center - 9, y + 2),
        "–",
        font=separator_font,
        fill=(151, 163, 181, 255),
    )
    draw.text(
        (center + 58, y - 8),
        home_score,
        font=score_font,
        fill=(255, 255, 255, 255),
    )

    channel_text = f"On channel: {alert.source_channel}"
    channel_font = _font(17, bold=True)
    channel_box = draw.textbbox((0, 0), channel_text, font=channel_font)
    channel_width = channel_box[2] - channel_box[0]
    draw.text(
        ((FRAME_WIDTH - channel_width) // 2, 183),
        channel_text,
        font=channel_font,
        fill=(171, 184, 201, 255),
    )


def render_overlay(elapsed: float) -> bytes:
    image = Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0, 0))
    _slot, alert = _current_alert(elapsed)
    if alert is not None:
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle(
            (10, 10, FRAME_WIDTH - 10, FRAME_HEIGHT - 10),
            radius=22,
            fill=(8, 14, 24, 226),
            outline=(255, 255, 255, 72),
            width=2,
        )
        pill = (24, 22, 137, 50)
        draw.rounded_rectangle(
            pill,
            radius=10,
            fill=(199, 32, 45, 245),
        )
        league_label = f"{alert.league} SCORE"
        league_font = _font(15, bold=True)
        league_box = draw.textbbox((0, 0), league_label, font=league_font)
        league_width = league_box[2] - league_box[0]
        league_height = league_box[3] - league_box[1]
        league_x = pill[0] + ((pill[2] - pill[0]) - league_width) // 2 - league_box[0]
        league_y = pill[1] + ((pill[3] - pill[1]) - league_height) // 2 - league_box[1]
        draw.text(
            (league_x, league_y),
            league_label,
            font=league_font,
            fill=(255, 255, 255, 255),
        )

        headline = f"{alert.scoring_team.name} scored"
        headline_font = _fit_text(
            draw,
            headline,
            max_width=FRAME_WIDTH - 185,
            start_size=26,
            minimum=18,
        )
        draw.text(
            (151, 22),
            headline,
            font=headline_font,
            fill=(255, 255, 255, 255),
        )

        play_font = _fit_text(
            draw,
            alert.play,
            max_width=FRAME_WIDTH - 64,
            start_size=22,
            minimum=16,
        )
        draw.text(
            (32, 67),
            alert.play,
            font=play_font,
            fill=(210, 219, 231, 255),
        )
        _score_row(image, draw, alert)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def parent_target() -> str:
    # Import lazily to avoid making sports modules part of core's import cycle.
    import core
    import sports

    for item in core.curated_channels_for_guide():
        if str(item.get("number", "") or "").strip() != PARENT_CHANNEL_NUMBER:
            continue
        play_url = str(item.get("play_url", "") or "").split("?", 1)[0].strip()
        manual = re.fullmatch(r"/guide/play/manual/([^/]+)", play_url)
        if manual:
            return core.manual_stream_target(manual.group(1))
        generated = re.fullmatch(r"/guide/play/sports/(\d+)", play_url)
        if generated:
            return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


def _ffmpeg_command(parent_url: str, directory: Path) -> list[str]:
    playlist = directory / "stream.m3u8"
    segments = directory / "segment_%06d.ts"
    filter_graph = (
        "[1:v]format=rgba[alert];"
        "[0:v][alert]overlay=x=(W-w)/2:y=H-h-48:format=auto:eof_action=pass[v]"
    )
    return [
        ffmpeg_executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-thread_queue_size",
        "512",
        "-i",
        parent_url,
        "-thread_queue_size",
        "512",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-framerate",
        str(FRAME_RATE),
        "-i",
        "pipe:0",
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
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
        str(segments),
        str(playlist),
    ]


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "alert-demo"


def _stop_session_locked() -> AlertDemoSession | None:
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
    if session.process is not None:
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def _run(session: AlertDemoSession) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return
    frame_period = 1.0 / FRAME_RATE
    try:
        while not session.stop_event.is_set():
            if process.poll() is not None:
                break
            if time.monotonic() - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
                break
            elapsed = time.monotonic() - session.started_monotonic
            try:
                process.stdin.write(render_overlay(elapsed))
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


def start_session() -> AlertDemoSession:
    global _SESSION
    target = parent_target()
    if not target:
        raise RuntimeError("Channel 1 is not available for the sports alert demo.")

    with _LOCK:
        current = _SESSION
        if current is not None and current.process is not None and current.process.poll() is None and current.parent_url == target:
            current.last_access_monotonic = time.monotonic()
            return current
    if current is not None:
        stop_session()

    directory = _directory()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            _ffmpeg_command(target, directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start sports alert demo ffmpeg: {exc}") from exc

    session = AlertDemoSession(directory=directory, parent_url=target, process=process)
    thread = threading.Thread(target=_run, args=(session,), name="sports-alert-demo", daemon=True)
    session.thread = thread
    with _LOCK:
        _SESSION = session
    thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error = session.last_error
            stop_session()
            raise RuntimeError(error or "Sports alert demo ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session()
    raise RuntimeError("Sports alert demo stream did not become ready in time.")


def get_session() -> AlertDemoSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(filename: str) -> Path | None:
    if not re.fullmatch(r"(?:stream\.m3u8|segment_\d{6}\.ts)", str(filename or "")):
        return None
    session = get_session()
    if session is None and filename == "stream.m3u8":
        session = start_session()
    if session is None:
        return None
    path = session.directory / filename
    return path if path.exists() else None


def state_payload() -> dict:
    session = get_session()
    elapsed = time.monotonic() - session.started_monotonic if session is not None else 0.0
    slot, alert = _current_alert(elapsed)
    return {
        "channel_number": CHANNEL_NUMBER,
        "parent_channel_number": PARENT_CHANNEL_NUMBER,
        "active": session is not None,
        "alert_index": slot,
        "alert": _alert_payload(alert),
        "alerts": [
            _alert_payload(_demo_alert_for_slot(index))
            for index in range(DEMO_PREVIEW_COUNT)
        ],
        "visible_seconds": ALERT_VISIBLE_SECONDS,
        "slot_seconds": ALERT_SLOT_SECONDS,
        "last_error": session.last_error if session is not None else "",
    }
