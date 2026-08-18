from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import sports as _s
from media.ffmpeg import executable as ffmpeg_executable, terminate
from settings import load_settings

from . import live_stats
from . import mlb_stats_carousel
from . import mlb_stats_companions


PIP_SUFFIX = " — Live Scores PiP"
PIP_WIDTH = 480
PIP_HEIGHT = 270
PIP_BORDER = 4
PIP_MARGIN = 24
STARTUP_TIMEOUT = 24.0

_LOCK = threading.RLock()
_SESSIONS: dict[int, "PipSession"] = {}


@dataclass
class PipSession:
    assigned_number: int
    event_key: str
    directory: Path
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""


def _value(value: object) -> str:
    return str(value or "").strip()


def pip_number(row: dict) -> str:
    return f"{int(row.get('assigned_number') or 0)}.2"


def event_title(row: dict) -> str:
    return _value(row.get("event_title") or row.get("display_name") or "MLB Game")


def pip_title(row: dict) -> str:
    title = event_title(row)
    return title if title.endswith(PIP_SUFFIX) else f"{title}{PIP_SUFFIX}"


def pip_tvg_id(row: dict) -> str:
    digest = hashlib.sha256(mlb_stats_companions.logical_event_key(row).encode("utf-8")).hexdigest()[:24]
    return f"m3u-picker-sports-pip-{digest}"


def pip_stream_path(row: dict) -> str:
    return f"/sports/stats-pip/{int(row.get('assigned_number') or 0)}/stream.m3u8"


def pip_play_path(row: dict) -> str:
    return f"/guide/play/stats-pip/{int(row.get('assigned_number') or 0)}"


def live_rows(db_path: Path | str, *, now: datetime | None = None) -> list[dict]:
    """Return one MLB parent row per logical event currently believed live."""
    return mlb_stats_carousel.candidate_rows(db_path, now=now)


def live_row_for_number(db_path: Path | str, assigned_number: int) -> dict | None:
    number = int(assigned_number)
    for row in live_rows(db_path):
        if int(row.get("assigned_number") or 0) == number:
            return row
    return None


def guide_item(row: dict) -> dict:
    title = pip_title(row)
    return {
        "number": pip_number(row),
        "name": title,
        "group": _value(row.get("group_title")) or "Sports Today",
        "logo": _value(row.get("tvg_logo")),
        "tvg_id": pip_tvg_id(row),
        "subtitle": "Game video with rotating MLB live scores",
        "generated": True,
        "play_url": pip_play_path(row),
        "stats_pip": True,
        "stats_parent": int(row.get("assigned_number") or 0),
        "sports_event_key": mlb_stats_companions.logical_event_key(row),
        "epg_mirror_tvg_id": _value(row.get("tvg_id")),
        "epg_mirror_title": title,
        "epg_mirror_subtitle": "Live Scores PiP",
        "epg_mirror_description": f"{event_title(row)} with the MLB live-score carousel in picture-in-picture.",
    }


def inject_pip_channels(text: str, db_path: Path | str, base_url: str) -> str:
    rows = {int(row["assigned_number"]): row for row in live_rows(db_path)}
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
        title = pip_title(row)
        logo = _value(row.get("tvg_logo"))
        attrs = [
            f'tvg-id="{pip_tvg_id(row)}"',
            f'tvg-chno="{pip_number(row)}"',
            f'tvg-name="{title.replace(chr(34), chr(39))}"',
            f'group-title="{(_value(row.get("group_title")) or "Sports Today").replace(chr(34), chr(39))}"',
            'x-sports-pip="mlb-carousel"',
            f'x-sports-parent="{number}"',
        ]
        if logo:
            attrs.append(f'tvg-logo="{logo.replace(chr(34), chr(39))}"')
        output.append(f"#EXTINF:-1 {' '.join(attrs)},{title}")
        output.append(f"{base_url.rstrip('/')}{pip_stream_path(row)}")
    return "\n".join(output) + "\n"


def _xmltv_time(value: datetime) -> str:
    local = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
    return local.strftime("%Y%m%d%H%M%S %z")


def _add_text(parent: ElementTree.Element, tag: str, text: str, **attrs) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag, {key: str(value) for key, value in attrs.items()})
    element.text = str(text)
    return element


def append_xmltv(
    root: ElementTree.Element,
    generated: Iterable[dict],
    timezone_name: str,
    *,
    generated_at: datetime | None = None,
) -> None:
    """Append PiP XMLTV rows for games believed live when the guide is built."""
    del generated  # live eligibility comes from the published generated rows in the DB.
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    anchor = generated_at if isinstance(generated_at, datetime) else datetime.now(timezone)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone)
    else:
        anchor = anchor.astimezone(timezone)
    rows = live_rows(_s.DB_PATH if hasattr(_s, "DB_PATH") else "", now=anchor)
    # sports.__init__ does not own DB_PATH in every import layout; callers that
    # need XMLTV should use append_xmltv_for_db below.
    if not rows:
        return
    _append_xmltv_rows(root, rows, timezone)


def append_xmltv_for_db(
    root: ElementTree.Element,
    db_path: Path | str,
    timezone_name: str,
    *,
    generated_at: datetime | None = None,
) -> None:
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    anchor = generated_at if isinstance(generated_at, datetime) else datetime.now(timezone)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone)
    else:
        anchor = anchor.astimezone(timezone)
    rows = live_rows(db_path, now=anchor)
    if rows:
        _append_xmltv_rows(root, rows, timezone)


def _append_xmltv_rows(root: ElementTree.Element, rows: list[dict], timezone: ZoneInfo) -> None:
    channels: list[ElementTree.Element] = []
    for row in rows:
        if any(
            child.tag.rsplit("}", 1)[-1] == "channel" and child.attrib.get("id") == pip_tvg_id(row)
            for child in root
        ):
            continue
        channel = ElementTree.Element("channel", {"id": pip_tvg_id(row)})
        _add_text(channel, "display-name", pip_title(row), lang="en")
        _add_text(channel, "display-name", pip_number(row), lang="en")
        logo = _value(row.get("tvg_logo"))
        if logo:
            ElementTree.SubElement(channel, "icon", {"src": logo})
        channels.append(channel)

    channel_insert_at = len(root)
    for index, child in enumerate(list(root)):
        if child.tag.rsplit("}", 1)[-1] == "programme":
            channel_insert_at = index
            break
    for offset, channel in enumerate(channels):
        root.insert(channel_insert_at + offset, channel)

    for row in rows:
        start, stop = mlb_stats_companions.event_window(row, timezone)
        if start is None or stop is None or stop <= start:
            continue
        programme = ElementTree.SubElement(
            root,
            "programme",
            {
                "start": _xmltv_time(start),
                "stop": _xmltv_time(stop),
                "channel": pip_tvg_id(row),
            },
        )
        _add_text(programme, "title", pip_title(row), lang="en")
        _add_text(programme, "sub-title", "Live Scores PiP", lang="en")
        _add_text(
            programme,
            "desc",
            f"{event_title(row)} with the rotating MLB live-score carousel in the lower-right corner.",
            lang="en",
        )
        for category in ("Sports", "Baseball", "MLB", "Live Scores", "Picture in Picture"):
            _add_text(programme, "category", category, lang="en")
        if not bool(row.get("is_replay")):
            ElementTree.SubElement(programme, "live")


def _carousel_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}{mlb_stats_carousel.STREAM_PATH}"


def _ffmpeg_command(parent_url: str, directory: Path) -> list[str]:
    playlist = directory / "stream.m3u8"
    segments = directory / "segment_%06d.ts"
    pip_w = PIP_WIDTH + (PIP_BORDER * 2)
    pip_h = PIP_HEIGHT + (PIP_BORDER * 2)
    filter_graph = (
        f"[1:v]scale={PIP_WIDTH}:{PIP_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={pip_w}:{pip_h}:{PIP_BORDER}:{PIP_BORDER}:black[pip];"
        f"[0:v][pip]overlay=W-w-{PIP_MARGIN}:H-h-{PIP_MARGIN}:shortest=1[v]"
    )
    return [
        ffmpeg_executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-thread_queue_size",
        "512",
        "-i",
        parent_url,
        "-thread_queue_size",
        "512",
        "-i",
        _carousel_url(),
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


def _directory(number: int) -> Path:
    return Path(live_stats.STATS_ROOT) / "pip" / str(int(number))


def _stop_session_locked(number: int) -> PipSession | None:
    session = _SESSIONS.pop(int(number), None)
    if session is not None:
        session.stop_event.set()
    return session


def stop_session(assigned_number: int) -> bool:
    number = int(assigned_number)
    with _LOCK:
        session = _stop_session_locked(number)
    if session is None:
        return False
    if session.process is not None:
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def _monitor(session: PipSession) -> None:
    process = session.process
    if process is None:
        return
    try:
        while not session.stop_event.wait(1.0):
            if process.poll() is not None:
                break
            if time.monotonic() - session.last_access_monotonic > float(live_stats.IDLE_SECONDS):
                break
    finally:
        terminate(process)
        with _LOCK:
            current = _SESSIONS.get(session.assigned_number)
            if current is session:
                _SESSIONS.pop(session.assigned_number, None)
        shutil.rmtree(session.directory, ignore_errors=True)


def start_session(db_path: Path | str, assigned_number: int) -> PipSession:
    number = int(assigned_number)
    row = live_row_for_number(db_path, number)
    if row is None:
        raise RuntimeError("MLB PiP channel is not currently live.")

    event_key = mlb_stats_companions.logical_event_key(row)
    with _LOCK:
        current = _SESSIONS.get(number)
        if current is not None and current.event_key == event_key and current.process is not None and current.process.poll() is None:
            current.last_access_monotonic = time.monotonic()
            return current
    if current is not None:
        stop_session(number)

    parent_url = _s.generated_stream_target(db_path, number)
    if not parent_url:
        raise RuntimeError("MLB PiP parent stream was not found.")

    directory = _directory(number)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            _ffmpeg_command(parent_url, directory),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start MLB PiP ffmpeg: {exc}") from exc

    session = PipSession(
        assigned_number=number,
        event_key=event_key,
        directory=directory,
        process=process,
    )
    thread = threading.Thread(target=_monitor, args=(session,), name=f"mlb-stats-pip-{number}", daemon=True)
    session.thread = thread
    with _LOCK:
        _SESSIONS[number] = session
    thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stop_session(number)
            raise RuntimeError("MLB PiP ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session(number)
    raise RuntimeError("MLB PiP stream did not become ready in time.")


def get_session(assigned_number: int) -> PipSession | None:
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


def state_payload(db_path: Path | str, assigned_number: int) -> dict[str, Any]:
    number = int(assigned_number)
    row = live_row_for_number(db_path, number)
    session = get_session(number)
    return {
        "assigned_number": number,
        "pip_number": f"{number}.2",
        "available": row is not None,
        "active": session is not None,
        "event_key": mlb_stats_companions.logical_event_key(row) if row else "",
        "parent_stream": bool(_s.generated_stream_target(db_path, number)) if row else False,
        "carousel": mlb_stats_carousel.state_payload(db_path),
        "pip_width": PIP_WIDTH,
        "pip_height": PIP_HEIGHT,
        "pip_margin": PIP_MARGIN,
        "error": session.last_error if session else "",
    }
