from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Callable

from flask import Response, request, stream_with_context

from .ffmpeg import audio_only_mp3_args, normalized_live_input_args, terminate
import media_pipeline


def response_for(
    target: str,
    *,
    on_stop: Callable[[], None] | None = None,
    audio_only: bool = False,
) -> Response:
    """Transcode one curated IPTV stream for browser playback."""
    session_token = ""
    output_name = "browser-audio" if audio_only else "browser"
    try:
        session_token = media_pipeline.acquire_session(output_name)
        media_pipeline.clear_output_error(output_name)
        if audio_only:
            command = audio_only_mp3_args(target)
        else:
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
            stderr=subprocess.PIPE if audio_only else subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        media_pipeline.release_session(session_token)
        return Response(
            f"Could not start ffmpeg: {exc}\n",
            status=502,
            content_type="text/plain; charset=utf-8",
        )

    client_disconnected = request.environ.get("waitress.client_disconnected")
    stop_lock = threading.Lock()
    cleanup_lock = threading.Lock()
    watcher_stop = threading.Event()
    stopped = False
    cleaned_up = False
    stream_state_lock = threading.Lock()
    stream_state = {"bytes_sent": 0}
    stderr_tail: deque[str] = deque(maxlen=80)

    def audio_error_detail(fallback: str) -> str:
        with stream_state_lock:
            detail = "\n".join(stderr_tail).strip()
        if target:
            detail = detail.replace(target, "[source]")
        return detail or fallback

    def drain_audio_errors() -> None:
        if process.stderr is None:
            return
        try:
            while True:
                raw = process.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    with stream_state_lock:
                        stderr_tail.append(line)
        except (OSError, ValueError):
            pass

    if audio_only and process.stderr is not None:
        threading.Thread(
            target=drain_audio_errors,
            name="browser-audio-errors",
            daemon=True,
        ).start()

        def watch_audio_startup() -> None:
            if watcher_stop.wait(12.0):
                return
            with stream_state_lock:
                empty = stream_state["bytes_sent"] == 0
            if empty:
                media_pipeline.record_output_error(
                    output_name,
                    audio_error_detail("No audio data was produced within 12 seconds."),
                )

        threading.Thread(
            target=watch_audio_startup,
            name="browser-audio-startup",
            daemon=True,
        ).start()

    def notify_stop() -> None:
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        if on_stop is not None:
            on_stop()

    def cleanup_process() -> None:
        nonlocal cleaned_up
        with cleanup_lock:
            if cleaned_up:
                return
            cleaned_up = True
        watcher_stop.set()
        terminate(process)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except Exception:
                pass
        if audio_only and process.stderr is not None:
            try:
                process.stderr.close()
            except Exception:
                pass
        media_pipeline.release_session(session_token)
        notify_stop()

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
                if audio_only:
                    with stream_state_lock:
                        first_chunk = stream_state["bytes_sent"] == 0
                        stream_state["bytes_sent"] += len(chunk)
                    if first_chunk:
                        media_pipeline.clear_output_error(output_name)
                yield chunk
        finally:
            if audio_only:
                with stream_state_lock:
                    empty = stream_state["bytes_sent"] == 0
                if empty:
                    media_pipeline.record_output_error(
                        output_name,
                        audio_error_detail("The channel ended without producing audio data."),
                    )
            cleanup_process()

    response = Response(
        stream_with_context(generate()),
        content_type="audio/mpeg" if audio_only else "video/mp4",
        direct_passthrough=True,
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Content-Disposition"] = (
        'inline; filename="live.mp3"'
        if audio_only
        else 'inline; filename="live.mp4"'
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Content-Type"
    response.call_on_close(cleanup_process)
    return response
