from __future__ import annotations

import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field

from flask import Response, request, stream_with_context

from .ffmpeg import normalized_live_input_args, terminate
import media_pipeline


_LOCK = threading.RLock()
_STREAMS: dict[str, "SharedMpegtsStream"] = {}
_END = object()
STALE_SUBSCRIBER_SECONDS = 15.0


@dataclass
class Subscriber:
    output: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=8))
    last_consumed_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class SharedMpegtsStream:
    target: str
    process: subprocess.Popen
    pipeline_token: str
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    finished: bool = False


def _command(target: str) -> list[str]:
    return normalized_live_input_args(target) + [
        "-f", "mpegts", "-mpegts_flags", "+resend_headers",
        "-muxdelay", "0", "-muxpreload", "0", "pipe:1",
    ]


def _finish(stream: SharedMpegtsStream) -> None:
    with _LOCK:
        if stream.finished:
            return
        stream.finished = True
        if _STREAMS.get(stream.target) is stream:
            _STREAMS.pop(stream.target, None)
        subscribers = list(stream.subscribers.values())
        stream.subscribers.clear()
    for subscriber in subscribers:
        output = subscriber.output
        try:
            output.put_nowait(_END)
        except queue.Full:
            try:
                output.get_nowait()
                output.put_nowait(_END)
            except (queue.Empty, queue.Full):
                pass
    terminate(stream.process)
    media_pipeline.release_session(stream.pipeline_token)


def _evict_stale_subscribers(stream: SharedMpegtsStream, now: float) -> bool:
    with _LOCK:
        stale = [
            subscriber_id for subscriber_id, subscriber in stream.subscribers.items()
            if subscriber.output.full()
            and now - subscriber.last_consumed_monotonic >= STALE_SUBSCRIBER_SECONDS
        ]
        for subscriber_id in stale:
            stream.subscribers.pop(subscriber_id, None)
        return not stream.subscribers and _STREAMS.get(stream.target) is stream


def _broadcast(stream: SharedMpegtsStream) -> None:
    try:
        if stream.process.stdout is None:
            return
        while True:
            chunk = stream.process.stdout.read(64 * 1024)
            if not chunk:
                break
            if _evict_stale_subscribers(stream, time.monotonic()):
                return
            with _LOCK:
                subscribers = list(stream.subscribers.values())
            for subscriber in subscribers:
                output = subscriber.output
                try:
                    output.put_nowait(chunk)
                except queue.Full:
                    # A stalled client must not block the provider stream or
                    # other viewers. MPEG-TS recovers at the next keyframe.
                    try:
                        output.get_nowait()
                        output.put_nowait(chunk)
                    except (queue.Empty, queue.Full):
                        pass
    finally:
        _finish(stream)


def _subscribe(target: str) -> tuple[SharedMpegtsStream, str, Subscriber]:
    with _LOCK:
        stream = _STREAMS.get(target)
        created = False
        if stream is None or stream.process.poll() is not None:
            pipeline_token = media_pipeline.acquire_session("mpegts")
            try:
                process = subprocess.Popen(
                    _command(target), stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=0,
                )
            except (OSError, RuntimeError):
                media_pipeline.release_session(pipeline_token)
                raise
            stream = SharedMpegtsStream(target, process, pipeline_token)
            _STREAMS[target] = stream
            created = True
        subscriber_id = uuid.uuid4().hex
        subscriber = Subscriber()
        stream.subscribers[subscriber_id] = subscriber
        if created:
            threading.Thread(target=_broadcast, args=(stream,), daemon=True).start()
        return stream, subscriber_id, subscriber


def _unsubscribe(stream: SharedMpegtsStream, subscriber_id: str) -> None:
    with _LOCK:
        stream.subscribers.pop(subscriber_id, None)
        last = not stream.subscribers and _STREAMS.get(stream.target) is stream
    if last:
        _finish(stream)


def response_for(target: str) -> Response:
    """Serve a shared per-channel MPEG-TS encoder to one downstream client."""
    try:
        stream, subscriber_id, subscriber = _subscribe(target)
    except RuntimeError as exc:
        return Response(f"MPEG-TS playback is unavailable: {exc}\n", status=503, content_type="text/plain; charset=utf-8")
    except OSError as exc:
        return Response(f"Could not start ffmpeg: {exc}\n", status=502, content_type="text/plain; charset=utf-8")
    client_disconnected = request.environ.get("waitress.client_disconnected")

    def generate():
        try:
            while True:
                if callable(client_disconnected) and client_disconnected():
                    break
                try:
                    chunk = subscriber.output.get(timeout=1.0)
                except queue.Empty:
                    continue
                if chunk is _END:
                    break
                subscriber.last_consumed_monotonic = time.monotonic()
                yield chunk
        finally:
            _unsubscribe(stream, subscriber_id)

    response = Response(stream_with_context(generate()), content_type="video/mp2t", direct_passthrough=True)
    response.headers.update({
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache", "Expires": "0", "X-Accel-Buffering": "no",
        "Content-Disposition": 'inline; filename="live.ts"',
        "X-Content-Type-Options": "nosniff",
    })
    return response
