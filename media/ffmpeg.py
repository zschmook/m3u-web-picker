from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import media_pipeline


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


def normalized_live_input_args(target: str, *, video_extra: tuple[str, ...] = ()) -> list[str]:
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
    reconnect = []
    if str(target).lower().startswith(("http://", "https://")):
        # Live providers regularly close a connection for a second while their
        # origin or edge changes. Let FFmpeg absorb the brief interruption;
        # the HLS relay supervisor advances to another provider if FFmpeg still
        # exits after these bounded retries.
        reconnect = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "2",
        ]
    return [
        executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        *reconnect,
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
        # Provider MPEG-TS feeds can begin with a large video PTS offset while
        # audio starts at zero. Rebase video at every new live session so
        # browsers do not wait for or replay that stale timestamp gap.
        "-vf",
        "setpts=PTS-STARTPTS",
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
    reconnect = []
    if str(target).lower().startswith(("http://", "https://")):
        # Recover transport interruptions here. Clean EOF is handled by the
        # browser worker supervisor: reconnect_at_eof can loop individual HLS
        # segments instead of advancing through the live playlist.
        reconnect = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "2",
            "-rw_timeout", "10000000",
        ]
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
