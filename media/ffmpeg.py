from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import media_pipeline


HTTP_READ_TIMEOUT_MICROSECONDS = "10000000"


def _http_reconnect_args(target: str) -> list[str]:
    if not str(target).lower().startswith(("http://", "https://")):
        return []
    return [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "2",
        # A provider can leave its TCP connection open while sending no media.
        # Turn that silent hang into an FFmpeg exit so the owning player can
        # reopen the live source instead of buffering forever.
        "-rw_timeout", HTTP_READ_TIMEOUT_MICROSECONDS,
    ]


def executable() -> str:
    configured = str(os.environ.get("M3U_FFMPEG", "") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError(f"Configured ffmpeg executable does not exist: {candidate}")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed.")
    return ffmpeg


def _input_header_args(input_headers: dict[str, str] | None) -> list[str]:
    if not input_headers:
        return []
    lines = []
    for name, value in input_headers.items():
        name = str(name or "").strip()
        value = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]+", name) or "\r" in value or "\n" in value:
            raise ValueError("Invalid media input header.")
        lines.append(f"{name}: {value}\r\n")
    return ["-headers", "".join(lines)]


def normalized_live_input_args(
    target: str,
    *,
    video_extra: tuple[str, ...] = (),
    input_headers: dict[str, str] | None = None,
    preserve_av_timing: bool = False,
) -> list[str]:
    """Common ffmpeg input + H.264/AAC normalization arguments.

    Browser fMP4 and remote HLS intentionally share these settings so device
    adapters only choose their container/muxer details.
    """
    encoder = media_pipeline.active_encoder()
    if encoder == "libx264":
        encoder_options = ["-preset", "ultrafast", "-tune", "zerolatency"]
    elif encoder == "h264_nvenc":
        # NVENC's quality defaults can hold a large reordered timestamp window
        # on MPEG-TS inputs. For live playback that appeared as a frozen player
        # followed by video roughly 100 seconds behind the audio.
        encoder_options = [
            "-preset", "p1",
            "-tune", "ll",
            "-zerolatency", "1",
            "-delay", "0",
            "-bf", "0",
            "-rc-lookahead", "0",
        ]
    else:
        encoder_options = []
    # Live providers regularly close a connection for a second while their
    # origin or edge changes. Let FFmpeg absorb the brief interruption. A read
    # timeout also exposes half-open sources to the owning stream supervisor.
    reconnect = _http_reconnect_args(target)
    return [
        executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        *reconnect,
        *_input_header_args(input_headers),
        "-i",
        target,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        encoder,
        *encoder_options,
        "-pix_fmt",
        "yuv420p",
        # The browser bridge still rebases its video track. HLS keeps FFmpeg's
        # shared input origin: resetting either track on its own would discard
        # the offset that aligns their content when joining a live feed.
        *([] if preserve_av_timing else ["-vf", "setpts=PTS-STARTPTS"]),
        *video_extra,
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
    ]


def audio_only_mp3_args(target: str) -> list[str]:
    """Transcode one live input to a browser-friendly audio-only stream."""
    # Recover transport interruptions here. Clean EOF is handled by the
    # browser worker supervisor: reconnect_at_eof can loop individual HLS
    # segments instead of advancing through the live playlist.
    reconnect = _http_reconnect_args(target)
    live_playlist = []
    if str(target).lower().startswith(("http://", "https://")) and urlsplit(target).path.lower().endswith(".m3u8"):
        # Join the newest available segment, including after worker recovery,
        # instead of replaying the default three-segment startup window.
        live_playlist = [
            "-live_start_index", "-1",
            # Xtream playlists can use tokenized segment paths without a file
            # extension. Permit those names while restricting nested resources
            # to network protocols (never local files).
            "-allowed_extensions", "ALL",
            "-allowed_segment_extensions", "ALL",
            "-extension_picky", "0",
            "-protocol_whitelist", "http,https,tcp,tls,crypto,httpproxy",
        ]
    return [
        executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        # Providers may send a whole recent window immediately, then close.
        # Consume that window at playback speed so reopening does not race
        # ahead of the live clock and append overlapping buffered audio.
        "-re",
        *reconnect,
        *live_playlist,
        "-i",
        target,
        "-map",
        "0:a:0?",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-f",
        "mp3",
        "-write_xing",
        "0",
        # A replacement worker appends frames to the same HTTP response. Avoid
        # inserting a fresh ID3 header between those MP3 frame sequences.
        "-id3v2_version",
        "0",
        "pipe:1",
    ]


def fragmented_mp4_copy_args(target: str) -> list[str]:
    """Remux the compositor's already-normalized H.264/AAC output."""
    return [
        executable(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-copyts", "-start_at_zero", "-i", target,
        "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
    ]


def terminate(process: subprocess.Popen, *, timeout: float = 2.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
