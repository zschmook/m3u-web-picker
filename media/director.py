from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import media_pipeline
import app_config
from PIL import Image, ImageDraw, ImageFont
from settings import SETTINGS, load_settings
from .ffmpeg import executable as ffmpeg_executable
from .ffmpeg import terminate


STREAM_PATH = "/director/stream.m3u8"
PLAY_URL = "/guide/play/director/0.2"
CHANNEL_NUMBER = "0.2"
DISPLAY_NAME = "Remote"
GROUP_TITLE = "Remote"
TVG_ID = "m3u-picker-remote"

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
OUTPUT_FRAME_RATE = "60000/1001"
OUTPUT_FRAME_RATE_DISPLAY = 59.94
OUTPUT_GOP_SIZE = 60
STARTUP_TIMEOUT = 20.0
IDLE_SECONDS = 300.0
HANDOFF_GRACE_SECONDS = 30.0
PERSIST_ACTIVITY_SECONDS = 60.0
HANDOFF_SEQUENCE_GAP = 32
WARMUP_SEGMENTS = 3
SOURCE_BUFFER_SECONDS = 10.0
_TIMELINE_STARTED_MONOTONIC = time.monotonic()


@dataclass
class DirectorSession:
    directory: Path
    process: subprocess.Popen
    revision: int
    channel_id: str = ""
    sequence_offset: int = 0
    discontinuity_sequence: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)


_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_CATALOG: dict[str, dict] = {}
_SAVED_STATE = app_config.section("remote_channel")
_SAVED_ID = str(_SAVED_STATE.get("selected_id", "") or "").strip()
try:
    _SAVED_ACTIVE_AT = float(_SAVED_STATE.get("active_at", 0) or 0)
except (TypeError, ValueError):
    _SAVED_ACTIVE_AT = 0.0
_SELECTED_ID = _SAVED_ID if _SAVED_ACTIVE_AT and time.time() - _SAVED_ACTIVE_AT < IDLE_SECONDS else ""
_LAST_PERSISTED_WALL = _SAVED_ACTIVE_AT if _SELECTED_ID else 0.0
_REVISION = 1
_SWITCHING = False
_LAST_ERROR = ""
_SESSION: DirectorSession | None = None
_RETIRED_DIRECTORIES: list[tuple[Path, float]] = []
_REAPER_STARTED = False


def _root_directory() -> Path:
    return Path(SETTINGS.cast_hls_dir) / "director"


def remote_url() -> str:
    settings = load_settings()
    secure_url = str(getattr(settings, "remote_url", "") or "").strip()
    if secure_url.startswith(("https://", "http://")):
        return secure_url.rstrip("/")
    host = str(settings.lan_host or "localhost").strip()
    return f"http://{host}:{settings.external_port}/remote"


def _font(size: int, *, bold: bool = False):
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _centered_text(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: str) -> None:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((OUTPUT_WIDTH - (right - left)) / 2, y), text, font=font, fill=fill)


def _write_idle_slate(path: Path) -> None:
    import qrcode

    url = remote_url()
    image = Image.new("RGB", (OUTPUT_WIDTH, OUTPUT_HEIGHT), "#070b12")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((330, 105, 1590, 975), radius=36, fill="#111827", outline="#374151", width=3)
    _centered_text(draw, 165, "CHANNEL 0.2", _font(58, bold=True), "#f8fafc")
    _centered_text(draw, 242, "Scan to control this channel", _font(34), "#cbd5e1")

    qr = qrcode.QRCode(version=None, box_size=12, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="#05070a", back_color="#ffffff").convert("RGB")
    qr_image = qr_image.resize((430, 430), Image.Resampling.NEAREST)
    qr_x = (OUTPUT_WIDTH - qr_image.width) // 2
    image.paste(qr_image, (qr_x, 330))

    _centered_text(draw, 805, url, _font(31, bold=True), "#93c5fd")
    _centered_text(draw, 865, "Choose any enabled channel. Your TV stays on 0.2.", _font(25), "#94a3b8")
    image.save(path, format="PNG", optimize=True)


def _ensure_reaper() -> None:
    global _REAPER_STARTED, _SESSION, _RETIRED_DIRECTORIES
    with _LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True

    def reap() -> None:
        global _SESSION, _RETIRED_DIRECTORIES, _SELECTED_ID, _REVISION
        while True:
            time.sleep(3.0)
            expired_session = _expire_idle_session()
            expired_directories: list[Path] = []
            with _LOCK:
                now = time.monotonic()
                if expired_session is not None:
                    expired_directories.append(expired_session.directory)
                retained = []
                for directory, expires_at in _RETIRED_DIRECTORIES:
                    if now >= expires_at:
                        expired_directories.append(directory)
                    else:
                        retained.append((directory, expires_at))
                _RETIRED_DIRECTORIES = retained
            if expired_session is not None:
                _terminate_session(expired_session)
            for directory in expired_directories:
                shutil.rmtree(directory, ignore_errors=True)

    threading.Thread(target=reap, name="remote-channel-reaper", daemon=True).start()


def _expire_idle_session(now_monotonic: float | None = None) -> DirectorSession | None:
    """Release an unwatched source and return 0.2 to its QR default."""
    global _SESSION, _SELECTED_ID, _REVISION
    clear_selection = False
    with _LOCK:
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        expired = _SESSION
        if expired is None:
            stale_remembered_selection = bool(
                _SELECTED_ID
                and _LAST_PERSISTED_WALL
                and time.time() - _LAST_PERSISTED_WALL > IDLE_SECONDS
            )
            if not stale_remembered_selection:
                return None
            _SELECTED_ID = ""
            _REVISION += 1
            clear_selection = True
        else:
            if now - expired.last_access_monotonic <= IDLE_SECONDS:
                return None
            _SESSION = None
            if expired.channel_id and _SELECTED_ID == expired.channel_id:
                _SELECTED_ID = ""
                _REVISION += 1
                clear_selection = True
    if clear_selection:
        _save_selection("")
    return expired


def expire_idle() -> None:
    """Apply the idle timeout immediately, including after a process restart."""
    expired = _expire_idle_session()
    if expired is not None:
        _terminate_session(expired)
        shutil.rmtree(expired.directory, ignore_errors=True)


def _terminate_session(session: DirectorSession) -> None:
    terminate(session.process)


def set_channel_catalog(channels: list[dict]) -> None:
    """Install selectable targets while keeping provider URLs server-side."""
    catalog: dict[str, dict] = {}
    for channel in channels:
        channel_id = str(channel.get("id", "") or "").strip()
        target = str(channel.get("target", "") or "").strip()
        number = str(channel.get("number", "") or "").strip()
        selectable_id = (
            channel_id.startswith("/guide/play/manual/")
            or bool(re.fullmatch(r"/guide/play/sports/\d+", channel_id))
        )
        if (
            not channel_id
            or not target
            or channel_id == PLAY_URL
            or number.startswith("0.")
            or not selectable_id
        ):
            continue
        catalog[channel_id] = {
            "id": channel_id,
            "number": number,
            "name": str(channel.get("name", "") or ""),
            "group": str(channel.get("group", "") or ""),
            "logo": str(channel.get("logo", "") or ""),
            "kind": "sports" if channel_id.startswith("/guide/play/sports/") else "tv",
            "target": target,
        }
    with _LOCK:
        _CATALOG.clear()
        _CATALOG.update(catalog)


def _save_selection(channel_id: str) -> None:
    global _LAST_PERSISTED_WALL
    active_at = time.time() if channel_id else 0.0
    _LAST_PERSISTED_WALL = active_at
    try:
        app_config.update_section(
            "remote_channel",
            {"selected_id": str(channel_id or ""), "active_at": active_at},
        )
    except OSError:
        # A live handoff is still useful if a read-only or unhealthy data volume
        # prevents remembering it for the next process start.
        pass


def _public_channel(channel_id: str) -> dict | None:
    channel = _CATALOG.get(str(channel_id or ""))
    if channel is None:
        return None
    return {key: value for key, value in channel.items() if key != "target"}


def state_payload() -> dict:
    with _LOCK:
        session = _SESSION
        ready = bool(session is not None and session.process.poll() is None)
        return {
            "channel_number": CHANNEL_NUMBER,
            "active": bool(_SELECTED_ID),
            "ready": ready,
            "switching": _SWITCHING,
            "revision": _REVISION,
            "selected_id": _SELECTED_ID,
            "selected": _public_channel(_SELECTED_ID),
            "last_error": _LAST_ERROR,
            "idle_timeout_seconds": int(IDLE_SECONDS),
            "remote_url": remote_url(),
            "output": {
                "mode": "stream_copy" if _SELECTED_ID else "idle_slate",
                "normalized": False,
            },
        }


def _encoder_args() -> list[str]:
    encoder = str(media_pipeline.active_encoder() or "libx264")
    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll",
            "-rc", "cbr", "-b:v", "7M", "-maxrate", "7M", "-bufsize", "14M",
            "-zerolatency", "1", "-delay", "0", "-bf", "0",
        ]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", "7M", "-maxrate", "7M", "-bufsize", "14M"]
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", "7M", "-maxrate", "7M", "-bufsize", "14M", "-threads", "4",
    ]


def _common_output_args(
    directory: Path,
    revision: int,
    *,
    start_number: int,
    timestamp_offset: float,
) -> list[str]:
    playlist = directory / "stream.m3u8"
    segments = directory / f"segment_{revision}_%010d.ts"
    return [
        *_encoder_args(),
        "-r", str(OUTPUT_FRAME_RATE),
        "-g", str(OUTPUT_GOP_SIZE),
        "-keyint_min", str(OUTPUT_GOP_SIZE),
        "-sc_threshold", "0",
        "-force_key_frames", "expr:gte(t,n_forced*1)",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-max_muxing_queue_size", "1024", "-max_interleave_delta", "1000000",
        "-output_ts_offset", f"{timestamp_offset:.6f}",
        "-f", "hls", "-hls_time", "1", "-hls_list_size", "12",
        "-start_number", str(start_number),
        "-hls_delete_threshold", "30", "-hls_allow_cache", "0",
        "-hls_segment_options", "mpegts_flags=+initial_discontinuity+resend_headers",
        "-hls_flags", "delete_segments+discont_start+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", str(segments), str(playlist),
    ]


def stream_copy_command(
    directory: Path,
    target: str,
    revision: int,
    *,
    start_number: int | None = None,
) -> list[str]:
    """Remux the selected provider feed without altering either A/V track."""
    playlist = directory / "stream.m3u8"
    segments = directory / f"segment_{revision}_%010d.ts"
    return [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts+discardcorrupt", "-thread_queue_size", "512",
        "-i", str(target),
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
        "-max_interleave_delta", "1000000",
        "-f", "hls", "-hls_time", "1", "-hls_list_size", "12",
        "-start_number", str(int(time.time()) if start_number is None else int(start_number)),
        "-hls_delete_threshold", "30", "-hls_allow_cache", "0",
        "-hls_segment_options", "mpegts_flags=+initial_discontinuity+resend_headers",
        "-hls_flags", "delete_segments+discont_start+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", str(segments), str(playlist),
    ]


def _playlist_duration(path: Path) -> float:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    return sum(
        float(value)
        for value in re.findall(r"^#EXTINF:([0-9.]+)", text, re.MULTILINE)
    )


def slate_command(
    directory: Path,
    revision: int,
    *,
    start_number: int | None = None,
    timestamp_offset: float = 0.0,
) -> list[str]:
    slate = directory / "idle.png"
    _write_idle_slate(slate)
    return [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", str(OUTPUT_FRAME_RATE), "-re", "-i", str(slate),
        "-re", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v:0", "-map", "1:a:0",
        *_common_output_args(
            directory,
            revision,
            start_number=int(time.time()) if start_number is None else int(start_number),
            timestamp_offset=timestamp_offset,
        ),
    ]


def _next_hls_start_number() -> int:
    """Keep a warmed replacement beyond every sequence the old stream can emit."""
    baseline = int(time.time())
    with _LOCK:
        session = _SESSION
    if session is None:
        return baseline
    try:
        text = (session.directory / "stream.m3u8").read_text(
            encoding="utf-8",
            errors="replace",
        )
        match = re.search(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)\s*$", text, re.MULTILINE)
        if match:
            last_sequence = int(match.group(1)) + max(0, text.count("#EXTINF")) - 1
            baseline = max(baseline, last_sequence)
    except OSError:
        pass
    return baseline + HANDOFF_SEQUENCE_GAP


def _manifest_window(path: Path) -> tuple[int, int]:
    """Return the private sequence range currently advertised by FFmpeg."""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError("Remote channel playlist has no media sequence.")
    first = int(match.group(1))
    count = text.count("#EXTINF")
    if count < 1:
        raise RuntimeError("Remote channel playlist has no media segments.")
    return first, first + count - 1


def _new_session(
    channel_id: str,
    revision: int,
    displaced: DirectorSession | None = None,
) -> DirectorSession:
    _ensure_reaper()
    with _LOCK:
        channel = dict(_CATALOG.get(channel_id) or {}) if channel_id else {}
    if channel_id and not channel:
        raise ValueError("Choose an enabled channel.")

    root = _root_directory()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"revision-{revision}-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=False)
    start_number = _next_hls_start_number()
    timestamp_offset = max(
        0.0,
        time.monotonic() - _TIMELINE_STARTED_MONOTONIC,
    )
    command = (
        stream_copy_command(
            directory,
            str(channel["target"]),
            revision,
            start_number=start_number,
        )
        if channel_id
        else slate_command(
            directory,
            revision,
            start_number=start_number,
            timestamp_offset=timestamp_offset,
        )
    )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start remote channel ffmpeg: {exc}") from exc

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            shutil.rmtree(directory, ignore_errors=True)
            raise RuntimeError("Remote channel ffmpeg stopped before the stream became ready.")
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        ready = (
            _playlist_duration(playlist) >= SOURCE_BUFFER_SECONDS
            if channel_id
            else text.count("#EXTINF") >= WARMUP_SEGMENTS
        )
        if ready:
            return DirectorSession(directory, process, revision, channel_id=channel_id)
        time.sleep(0.10)

    terminate(process)
    shutil.rmtree(directory, ignore_errors=True)
    raise RuntimeError("Timed out waiting for the remote channel to become ready.")


def _activate(channel_id: str, revision: int) -> DirectorSession:
    global _SESSION
    with _START_LOCK:
        with _LOCK:
            displaced = _SESSION
            # Playlist requests can queue while a phone-triggered replacement is
            # warming.  Reuse the winner instead of serially rebuilding the same
            # revision once each waiting request acquires the start lock.
            if (
                displaced is not None
                and displaced.revision == revision
                and displaced.process.poll() is None
            ):
                displaced.last_access_monotonic = time.monotonic()
                return displaced
        session = _new_session(channel_id, revision, displaced)
        with _LOCK:
            if revision != _REVISION:
                stale = True
                displaced = None
            else:
                stale = False
                displaced = _SESSION
                if displaced is None:
                    session.discontinuity_sequence = max(1, revision)
                else:
                    new_first, _new_last = _manifest_window(
                        session.directory / "stream.m3u8"
                    )
                    _old_first, old_last = _manifest_window(
                        displaced.directory / "stream.m3u8"
                    )
                    # FFmpeg owns the private filenames, while the stable public
                    # playlist owns one gap-free logical sequence.  Mapping the
                    # warmed replacement to exactly old-last + 1 prevents clients
                    # from either replaying overlapping IDs or skipping a hole.
                    session.sequence_offset = (
                        old_last + displaced.sequence_offset + 1 - new_first
                    )
                    session.discontinuity_sequence = (
                        displaced.discontinuity_sequence + 1
                    )
                _SESSION = session
                if displaced is not None and displaced.directory != session.directory:
                    _RETIRED_DIRECTORIES.append(
                        (displaced.directory, time.monotonic() + HANDOFF_GRACE_SECONDS)
                    )
        if stale:
            _terminate_session(session)
            shutil.rmtree(session.directory, ignore_errors=True)
            raise RuntimeError("A newer remote-channel selection replaced this request.")
        if displaced is not None and displaced is not session:
            terminate(displaced.process)
        return session


def select_channel(channel_id: str) -> dict:
    global _SELECTED_ID, _REVISION, _SWITCHING, _LAST_ERROR
    value = str(channel_id or "").strip()
    with _LOCK:
        if value not in _CATALOG:
            raise ValueError("Choose an enabled channel.")
        if value == PLAY_URL or str(_CATALOG[value].get("number", "")).startswith("0."):
            raise ValueError("Virtual channels cannot be selected as their own source.")
        current_id = _SELECTED_ID
        current_session = _SESSION
        if (
            value == current_id
            and current_session is not None
            and current_session.process.poll() is None
        ):
            current_session.last_access_monotonic = time.monotonic()
            return state_payload()
        _REVISION += 1
        revision = _REVISION
        _SWITCHING = True
        _LAST_ERROR = ""
    try:
        _activate(value, revision)
    except Exception as exc:
        with _LOCK:
            if revision == _REVISION:
                _SWITCHING = False
                _SELECTED_ID = current_id
                _LAST_ERROR = str(exc)
        raise
    with _LOCK:
        if revision == _REVISION:
            _SELECTED_ID = value
            _SWITCHING = False
            _LAST_ERROR = ""
        result = state_payload()
    _save_selection(value)
    return result


def stop() -> dict:
    global _SELECTED_ID, _REVISION, _SWITCHING, _LAST_ERROR
    with _LOCK:
        previous_id = _SELECTED_ID
        _REVISION += 1
        revision = _REVISION
        _SWITCHING = True
        _LAST_ERROR = ""
    try:
        _activate("", revision)
    except Exception as exc:
        with _LOCK:
            if revision == _REVISION:
                _SELECTED_ID = previous_id
                _SWITCHING = False
                _LAST_ERROR = str(exc)
        raise
    with _LOCK:
        if revision == _REVISION:
            _SELECTED_ID = ""
            _SWITCHING = False
        result = state_payload()
    _save_selection("")
    return result


def safe_media_file(filename: str) -> Path | None:
    global _LAST_PERSISTED_WALL
    name = str(filename or "")
    if not re.fullmatch(r"(?:stream\.m3u8|segment_\d+_\d+\.ts)", name):
        return None
    expire_idle()
    with _LOCK:
        session = _SESSION
        revision = _REVISION
        selected_id = _SELECTED_ID
        switching = _SWITCHING
    if name == "stream.m3u8" and (session is None or session.process.poll() is not None):
        # During a handoff, the old GPU is intentionally stopped only after the
        # CPU source buffer is ready.  Its frozen playlist and segments remain a
        # valid holding pattern until the replacement is atomically published.
        holding_playlist = (
            switching
            and session is not None
            and (session.directory / "stream.m3u8").is_file()
        )
        if not holding_playlist:
            session = _activate(selected_id, revision)
    if session is not None:
        if session.process.poll() is None:
            session.last_access_monotonic = time.monotonic()
        persist_id = ""
        with _LOCK:
            wall_now = time.time()
            if _SELECTED_ID and wall_now - _LAST_PERSISTED_WALL >= PERSIST_ACTIVITY_SECONDS:
                _LAST_PERSISTED_WALL = wall_now
                persist_id = _SELECTED_ID
        if persist_id:
            _save_selection(persist_id)
        path = session.directory / name
        if path.exists() and path.is_file():
            return path
    if name != "stream.m3u8":
        with _LOCK:
            retired = [directory for directory, _expires_at in _RETIRED_DIRECTORIES]
        for directory in reversed(retired):
            path = directory / name
            if path.exists() and path.is_file():
                return path
    return None


def public_manifest(path: Path) -> str:
    """Expose a stable sequence and an explicit timeline generation to HLS clients."""
    text = path.read_text(encoding="utf-8", errors="replace")
    with _LOCK:
        session = _SESSION
        if session is None or session.directory != path.parent:
            raise RuntimeError("Remote channel playlist changed while it was being served.")
        sequence_offset = session.sequence_offset
        discontinuity_sequence = session.discontinuity_sequence

    match = re.search(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError("Remote channel playlist has no media sequence.")
    public_sequence = int(match.group(1)) + sequence_offset
    text = text[:match.start()] + f"#EXT-X-MEDIA-SEQUENCE:{public_sequence}" + text[match.end():]

    # FFmpeg's discont_start marker eventually rolls out of the live window.
    # Keep the first listed segment's discontinuity generation constant on both
    # sides of that rollover by adjusting the header value when the marker exists.
    before_first_segment = text.split("#EXTINF", 1)[0]
    starts_with_discontinuity = "#EXT-X-DISCONTINUITY" in before_first_segment
    header_sequence = max(
        0,
        discontinuity_sequence - (1 if starts_with_discontinuity else 0),
    )
    text = re.sub(
        r"^#EXT-X-DISCONTINUITY-SEQUENCE:\d+\s*$\n?",
        "",
        text,
        flags=re.MULTILINE,
    )
    marker = re.search(r"^#EXT-X-MEDIA-SEQUENCE:\d+\s*$", text, re.MULTILINE)
    if marker is None:
        raise RuntimeError("Remote channel playlist sequence rewrite failed.")
    insert_at = marker.end()
    return (
        text[:insert_at]
        + f"\n#EXT-X-DISCONTINUITY-SEQUENCE:{header_sequence}"
        + text[insert_at:]
    )


def touch_session() -> None:
    with _LOCK:
        if _SESSION is not None and _SESSION.process.poll() is None:
            _SESSION.last_access_monotonic = time.monotonic()


def playlist(base_url: str) -> str:
    return "\n".join((
        "#EXTM3U",
        f'#EXTINF:-1 tvg-id="{TVG_ID}" tvg-chno="{CHANNEL_NUMBER}" tvg-name="{DISPLAY_NAME}" group-title="{GROUP_TITLE}",{DISPLAY_NAME}',
        f'{base_url.rstrip("/")}{STREAM_PATH}',
        "",
    ))


def guide_item() -> dict:
    return {
        "number": CHANNEL_NUMBER,
        "name": DISPLAY_NAME,
        "group": GROUP_TITLE,
        "logo": "",
        "tvg_id": TVG_ID,
        "subtitle": "One channel controlled from your phone",
        "generated": True,
        "play_url": PLAY_URL,
        "remote": True,
    }


def inject_channel(text: str, base_url: str) -> str:
    lines = str(text or "").splitlines()
    entry = [
        f'#EXTINF:-1 tvg-id="{TVG_ID}" tvg-chno="{CHANNEL_NUMBER}" tvg-name="{DISPLAY_NAME}" group-title="{GROUP_TITLE}",{DISPLAY_NAME}',
        f'{base_url.rstrip("/")}{STREAM_PATH}',
    ]
    insert_at = 1 if lines and lines[0].startswith("#EXTM3U") else 0
    lines[insert_at:insert_at] = entry
    return "\n".join(lines) + "\n"
