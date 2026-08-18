from __future__ import annotations

import io
import re
import shutil
import subprocess
import threading
import time
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
FRAME_HEIGHT = 240
FRAME_RATE = 2
ALERT_SLOT_SECONDS = 10.0
ALERT_VISIBLE_SECONDS = 7.0
STARTUP_TIMEOUT = 24.0

# Intentionally ridiculous canned situations. The prototype is proving the
# overlay/transport idea, not sports-event rules yet.
DEMO_ALERTS = (
    (
        "RUNNY McRUN FACE TD",
        "DOPES 34   IDIOTS 32",
        "1:34 LEFT IN Q4  ·  CH 8000-ELEVENTY",
    ),
    (
        "4TH & FOREVER — SOMEHOW CONVERTED",
        "WOMBATS 27   FIGHTING BEIGE 24",
        "0:31 LEFT IN Q4  ·  CH 31337",
    ),
    (
        "PUNT RETURN CHAOS!",
        "POSSUMS 19   TAX ACCOUNTANTS 17",
        "0:07 LEFT IN Q4  ·  CH 404",
    ),
    (
        "ON-SIDE KICK RECOVERED BY THE WRONG GUYS",
        "FERAL CATS 30   PARKING ENFORCEMENT 28",
        "0:42 LEFT IN Q4  ·  CH 8675309",
    ),
    (
        "WE HAVE A SCORIGAMI SITUATION",
        "MEAT SWEATS 11   LAWN DARTS 5",
        "2:12 LEFT IN Q4  ·  CH 42",
    ),
    (
        "BASES LOADED, NOBODY KNOWS WHY",
        "MUD HENS 6   SPACE COWBOYS 6",
        "BOTTOM 11TH  ·  CH 1776.5",
    ),
    (
        "GOALIE PULLED. BOTH TEAMS CONFUSED.",
        "ICE GOBLINS 3   BEIGE SWEATERS 2",
        "1:02 LEFT IN 3RD  ·  CH 66.6",
    ),
    (
        "OVERTIME! TALL PEOPLE STILL RUNNING",
        "TALL PEOPLE 121   OTHER TALL PEOPLE 121",
        "END Q4  ·  CH 9001",
    ),
)

_LOCK = threading.RLock()
_SESSION: "AlertDemoSession | None" = None


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


def guide_item() -> dict:
    return {
        "number": CHANNEL_NUMBER,
        "name": DISPLAY_NAME,
        "group": GROUP_TITLE,
        "logo": "",
        "tvg_id": TVG_ID,
        "subtitle": "Channel 1 with rotating simulated game alerts",
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


def _current_alert(elapsed: float) -> tuple[int, tuple[str, str, str] | None]:
    slot = int(max(0.0, elapsed) // ALERT_SLOT_SECONDS)
    within = max(0.0, elapsed) % ALERT_SLOT_SECONDS
    if within >= ALERT_VISIBLE_SECONDS:
        return slot, None
    return slot, DEMO_ALERTS[slot % len(DEMO_ALERTS)]


def render_overlay(elapsed: float) -> bytes:
    image = Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0, 0))
    _slot, alert = _current_alert(elapsed)
    if alert is not None:
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle(
            (8, 8, FRAME_WIDTH - 8, FRAME_HEIGHT - 8),
            radius=22,
            fill=(8, 14, 24, 224),
            outline=(255, 186, 36, 245),
            width=4,
        )
        draw.rounded_rectangle((24, 22, 205, 60), radius=12, fill=(205, 38, 45, 245))
        draw.text((39, 28), "GAME UPDATE!", font=_font(22, bold=True), fill=(255, 255, 255, 255))
        draw.text(
            (FRAME_WIDTH - 213, 31),
            "SIMULATED ALERT DEMO",
            font=_font(14, bold=True),
            fill=(171, 184, 201, 255),
        )

        headline, score, situation = alert
        headline_font = _fit_text(draw, headline, max_width=FRAME_WIDTH - 64, start_size=31, minimum=20)
        score_font = _fit_text(draw, score, max_width=FRAME_WIDTH - 64, start_size=29, minimum=20)
        situation_font = _fit_text(draw, situation, max_width=FRAME_WIDTH - 64, start_size=23, minimum=17)
        draw.text((32, 79), headline, font=headline_font, fill=(255, 255, 255, 255))
        draw.text((32, 126), score, font=score_font, fill=(255, 208, 82, 255))
        draw.text((32, 174), situation, font=situation_font, fill=(225, 232, 241, 255))

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
        "[0:v][alert]overlay=x=(W-w)/2:y=H-h-60:format=auto:eof_action=pass[v]"
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
        "alert_index": slot % len(DEMO_ALERTS),
        "alert": list(alert) if alert is not None else None,
        "alerts": [list(item) for item in DEMO_ALERTS],
        "visible_seconds": ALERT_VISIBLE_SECONDS,
        "slot_seconds": ALERT_SLOT_SECONDS,
        "last_error": session.last_error if session is not None else "",
    }
