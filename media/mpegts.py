from __future__ import annotations

import subprocess

from flask import Response, stream_with_context

from .ffmpeg import normalized_live_input_args, terminate


def response_for(target: str) -> Response:
    """Transcode one curated IPTV stream to an HDHomeRun/Plex-friendly MPEG-TS."""
    try:
        command = normalized_live_input_args(target) + [
            "-f",
            "mpegts",
            "-mpegts_flags",
            "+resend_headers",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "pipe:1",
        ]
    except RuntimeError:
        return Response(
            "HDHomeRun playback is unavailable because ffmpeg is not installed.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        return Response(
            f"Could not start ffmpeg: {exc}\n",
            status=502,
            content_type="text/plain; charset=utf-8",
        )

    def generate():
        try:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass
            terminate(process)

    response = Response(
        stream_with_context(generate()),
        content_type="video/mp2t",
        direct_passthrough=True,
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Content-Disposition"] = 'inline; filename="live.ts"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
