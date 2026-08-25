from __future__ import annotations

import subprocess
import threading

from flask import Response, request, stream_with_context

from .ffmpeg import normalized_live_input_args, terminate
import media_pipeline


_PREVIEW_LOCK = threading.RLock()
_PREVIEW_PROCESSES: dict[str, subprocess.Popen] = {}


def stop_preview(session_id: str) -> bool:
    """Explicitly stop a browser preview that may outlive its media request."""
    key = str(session_id or "").strip()
    if not key:
        return False
    with _PREVIEW_LOCK:
        process = _PREVIEW_PROCESSES.pop(key, None)
    if process is None:
        return False
    terminate(process)
    return True


def response_for(target: str, *, preview_session: str = "") -> Response:
    """Transcode one curated IPTV stream to browser-friendly fragmented MP4."""
    session_token = ""
    try:
        session_token = media_pipeline.acquire_session("browser")
        command = normalized_live_input_args(target) + [
            "-f",
            "mp4",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration",
            "1000000",
            "pipe:1",
        ]
    except RuntimeError as exc:
        media_pipeline.release_session(session_token)
        return Response(
            f"Browser playback is unavailable: {exc}\n",
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
        media_pipeline.release_session(session_token)
        return Response(
            f"Could not start ffmpeg: {exc}\n",
            status=502,
            content_type="text/plain; charset=utf-8",
        )
    preview_key = str(preview_session or "").strip()
    if preview_key:
        with _PREVIEW_LOCK:
            previous = _PREVIEW_PROCESSES.get(preview_key)
            _PREVIEW_PROCESSES[preview_key] = process
        if previous is not None and previous is not process:
            terminate(previous)
    client_disconnected = request.environ.get("waitress.client_disconnected")

    def generate():
        try:
            if process.stdout is None:
                return
            while True:
                if callable(client_disconnected) and client_disconnected():
                    break
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if preview_key:
                with _PREVIEW_LOCK:
                    if _PREVIEW_PROCESSES.get(preview_key) is process:
                        _PREVIEW_PROCESSES.pop(preview_key, None)
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass
            terminate(process)
            media_pipeline.release_session(session_token)

    response = Response(
        stream_with_context(generate()),
        content_type="video/mp4",
        direct_passthrough=True,
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Content-Disposition"] = 'inline; filename="live.mp4"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Content-Type"
    return response
