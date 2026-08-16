from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


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
    return [
        executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-i",
        target,
        "-map",
        "0:v:0?",
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
