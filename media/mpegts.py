from __future__ import annotations

import subprocess
from collections.abc import Iterator

from . import ffmpeg


_TS_READ_SIZE = 188 * 256


def stream(target: str, *, duration: int | None = None) -> Iterator[bytes]:
    """Normalize a provider stream and emit a continuous MPEG-TS byte stream."""
    args = ffmpeg.normalized_live_input_args(
        target,
        video_extra=("-g", "60", "-keyint_min", "60", "-sc_threshold", "0"),
    )
    if duration is not None:
        seconds = max(1, min(int(duration), 24 * 60 * 60))
        args.extend(("-t", str(seconds)))
    args.extend((
        "-mpegts_flags", "+resend_headers",
        "-muxdelay", "0",
        "-muxpreload", "0",
        "-f", "mpegts",
        "pipe:1",
    ))

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(_TS_READ_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        if process.stdout is not None:
            process.stdout.close()
        ffmpeg.terminate(process)
