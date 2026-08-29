from __future__ import annotations

import queue
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import Response, request, stream_with_context

from .ffmpeg import live_input_args, normalized_output_args, terminate
from .logo_detector import LiveLogoDetector
import media_pipeline


_LOCK = threading.RLock()
_STREAMS: dict[str, "SharedMpegtsStream"] = {}
_END = object()
STALE_SUBSCRIBER_SECONDS = 60.0
SLATE_PATH = Path(__file__).resolve().parent.parent / "static" / "images" / "commercial-in-progress-preview.gif"


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
    identity: str = ""
    control_address: str = ""
    commercial_active: bool = False
    analyzer: LiveLogoDetector | None = None
    context_signature: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.monotonic)
    finished: bool = False


def _available_control_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{listener.getsockname()[1]}"


def _escaped_filter_address(address: str) -> str:
    # The filter graph parser and the zmq option parser each consume an
    # escaping layer. These are literal argv characters (no shell involved).
    return address.replace(":", r"\\\:")


def _stream_signature(
    sports_generated: bool = False,
    profile_identity: str = "",
    profile_db_path: Path | None = None,
    epg_path: Path | None = None,
    timezone_name: str = "America/New_York",
) -> tuple[str, ...]:
    return (
        "1" if sports_generated else "0",
        str(profile_identity or ""),
        str(profile_db_path or ""),
        str(epg_path or ""),
        str(timezone_name or "America/New_York"),
    )


def _command(
    target: str,
    control_address: str,
    analysis_path: Path | None,
    *,
    commercial_active: bool = False,
) -> list[str]:
    overlay_x = "0" if commercial_active else "main_w"
    volume = "0" if commercial_active else "1"
    live_video = (
        "[live_base]settb=AVTB,setpts=PTS-STARTPTS,split=2[live][analysis_video_base];"
        "[analysis_video_base]fps=2,scale=-2:360[analysis_video];"
        if analysis_path is not None
        else "[live_base]settb=AVTB,setpts=PTS-STARTPTS[live];"
    )
    graph = (
        "[1:v][0:v]scale2ref=w=rw:h=rh[slate_base][live_base];"
        "[slate_base]settb=AVTB,setpts=PTS-STARTPTS[slate];"
        + live_video +
        f"[live][slate]overlay@commercial_overlay=x={overlay_x}:y=0:"
        "eof_action=repeat:shortest=0,"
        f"zmq=bind_address={_escaped_filter_address(control_address)}[selected_video];"
        "[0:a:0]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"volume@commercial_audio=volume={volume},"
        "asetpts=PTS-STARTPTS[selected_audio]"
    )
    command = live_input_args(target) + [
        # Pace the looping overlay while the broadcast remains the timing
        # master for both clean/composited video and audible/muted audio.
        "-re", "-stream_loop", "-1", "-i", str(SLATE_PATH),
        "-filter_complex", graph,
    ] + normalized_output_args(
        video_map="[selected_video]", audio_map="[selected_audio]",
    ) + [
        "-f", "mpegts", "-mpegts_flags", "+resend_headers",
        "-muxdelay", "0", "-muxpreload", "0", "pipe:1",
    ]
    if analysis_path is not None:
        command += ["-map", "[analysis_video]", "-q:v", "6", "-f", "image2", str(analysis_path)]
    return command


def _set_stream_commercial(
    stream: SharedMpegtsStream,
    active: bool,
    *,
    manual: bool = False,
) -> tuple[bool, str]:
    # Enforce the playback setting at the final control boundary. Analyzer
    # callbacks are asynchronous and can arrive after the user has disabled
    # filtering; no stale automatic decision may put the slate on screen.
    if active and not manual and not media_pipeline.commercial_filtering_active():
        return False, "Automatic commercial filtering is disabled."
    if stream.process.poll() is not None:
        return False, "FFmpeg session has ended."
    try:
        import zmq

        context = zmq.Context.instance()
        commands = (
            ("overlay@commercial_overlay", "x", "0" if active else "main_w"),
            ("volume@commercial_audio", "volume", "0" if active else "1"),
        )
        for target, command, argument in commands:
            socket_client = context.socket(zmq.REQ)
            socket_client.setsockopt(zmq.LINGER, 0)
            socket_client.setsockopt(zmq.RCVTIMEO, 1500)
            socket_client.setsockopt(zmq.SNDTIMEO, 1500)
            try:
                socket_client.connect(stream.control_address)
                socket_client.send_string(f"{target} {command} {argument}")
                reply = socket_client.recv_string()
                if not reply.startswith("0 "):
                    return False, reply
            finally:
                socket_client.close()
    except Exception as exc:
        return False, str(exc)
    with _LOCK:
        stream.commercial_active = active
    return True, ""


def commercial_status() -> dict:
    with _LOCK:
        streams = sorted([
            {
                "identity": stream.identity,
                "commercial_active": stream.commercial_active,
                "viewers": len(stream.subscribers),
                "logo_detector": stream.analyzer.status() if stream.analyzer else {"state": "disabled"},
                "created_at": stream.created_at,
            }
            for stream in _STREAMS.values()
            if stream.process.poll() is None
        ], key=lambda stream: stream["created_at"], reverse=True)
    return {"eligible_streams": len(streams), "streams": streams}


def active_stream_profile_snapshot(stream_identity: str = "") -> dict:
    with _LOCK:
        candidates = [
            stream for stream in _STREAMS.values()
            if stream.process.poll() is None and stream.analyzer is not None
        ]
        if stream_identity:
            candidates = [
                stream for stream in candidates if stream.identity == stream_identity
            ]
        candidates.sort(key=lambda stream: stream.created_at, reverse=True)
        for stream in candidates:
            snapshot = stream.analyzer.profile_snapshot()
            if snapshot.get("channel_identity") and not snapshot.get("sports_generated"):
                return snapshot
    return {}


def active_profile_snapshot() -> dict:
    return active_stream_profile_snapshot()


def set_inspection_archive(stream_identity: str, directory: Path | None) -> bool:
    """Persist low-rate original analysis frames for an unattended test run."""
    with _LOCK:
        stream = next(
            (
                candidate for candidate in _STREAMS.values()
                if candidate.process.poll() is None
                and candidate.analyzer is not None
                and candidate.identity == stream_identity
            ),
            None,
        )
    if stream is None or stream.analyzer is None:
        return False
    stream.analyzer.set_inspection_archive(directory)
    return True


def apply_program_feedback(stream_identity: str = "") -> bool:
    """Apply a user's program correction to the live classifier immediately."""
    with _LOCK:
        candidates = [
            stream for stream in _STREAMS.values()
            if stream.process.poll() is None
            and stream.analyzer is not None
            and (not stream_identity or stream.identity == stream_identity)
        ]
        candidates.sort(key=lambda stream: stream.created_at, reverse=True)
        stream = candidates[0] if candidates else None
    if stream is None or stream.analyzer is None:
        return False
    stream.analyzer.apply_program_feedback()
    # The analyzer callback normally restores playback. This direct command is
    # an idempotent safety net for any stale global/manual detector state.
    _set_stream_commercial(stream, False)
    return True


def apply_commercial_feedback(stream_identity: str = "") -> bool:
    """Give a user's commercial label to the active fingerprint episode."""
    with _LOCK:
        candidates = [
            stream for stream in _STREAMS.values()
            if stream.process.poll() is None
            and stream.analyzer is not None
            and (not stream_identity or stream.identity == stream_identity)
        ]
        candidates.sort(key=lambda stream: stream.created_at, reverse=True)
        stream = candidates[0] if candidates else None
    if stream is None or stream.analyzer is None:
        return False
    return stream.analyzer.apply_commercial_feedback()


def set_commercial(active: bool, stream_identity: str = "") -> dict:
    with _LOCK:
        streams = [
            stream for stream in _STREAMS.values()
            if stream.process.poll() is None and (not stream_identity or stream.identity == stream_identity)
        ]
    results = []
    for stream in streams:
        # This path is reached only by the explicit Start/End Commercial test.
        ok, error = _set_stream_commercial(stream, active, manual=True)
        results.append({"identity": stream.identity, "ok": ok, "error": error})
    return {
        "requested_active": bool(active),
        "eligible_streams": len(streams),
        "switched_streams": sum(1 for item in results if item["ok"]),
        "results": results,
    }


def apply_automatic_filtering_setting(enabled: bool) -> dict:
    """Apply the automatic overlay setting without interrupting live streams."""
    import commercial_detection

    detection = commercial_detection.payload()
    if detection.get("source") == "manual":
        return {
            "automatic_filtering_enabled": bool(enabled),
            "eligible_streams": 0,
            "switched_streams": 0,
            "manual_override_active": True,
            "results": [],
        }
    desired = bool(
        enabled
        and detection.get("active")
        and detection.get("source") == "logo"
    )
    with _LOCK:
        streams = [
            stream for stream in _STREAMS.values()
            if stream.process.poll() is None
        ]
    results = []
    for stream in streams:
        ok, error = _set_stream_commercial(stream, desired)
        results.append({"identity": stream.identity, "ok": ok, "error": error})
    return {
        "automatic_filtering_enabled": bool(enabled),
        "eligible_streams": len(streams),
        "switched_streams": sum(1 for item in results if item["ok"]),
        "manual_override_active": False,
        "results": results,
    }


def _build_stream(
    target: str,
    *,
    identity: str = "",
    sports_generated: bool = False,
    profile_identity: str = "",
    profile_db_path: Path | None = None,
    epg_path: Path | None = None,
    timezone_name: str = "America/New_York",
    context_signature: tuple[str, ...] | None = None,
) -> SharedMpegtsStream:
    stream: SharedMpegtsStream | None = None
    pipeline_token = media_pipeline.acquire_session("mpegts", identity=identity)
    control_address = _available_control_address()
    import commercial_detection
    detection_state = commercial_detection.payload()
    initial_commercial = bool(
        detection_state.get("active") and detection_state.get("source") == "manual"
    )

    analyzer: LiveLogoDetector | None = None
    try:
        def apply_detection(active: bool) -> None:
            if stream is None:
                return
            detected = commercial_detection.apply_logo_state(active)
            if detected.get("source") != "manual":
                with _LOCK:
                    current = _STREAMS.get(target)
                if current is stream:
                    filtering_enabled = media_pipeline.commercial_filtering_active()
                    _set_stream_commercial(
                        stream,
                        bool(filtering_enabled and detected.get("active")),
                        manual=False,
                    )

        # Analysis is part of every FFmpeg stream. The user-facing setting only
        # controls whether a positive decision is applied to playback; keeping
        # this running lets per-channel learning continue while filtering is off.
        analyzer = LiveLogoDetector.create(
            apply_detection,
            sports_generated=sports_generated,
            channel_identity=profile_identity or identity,
            profile_db_path=profile_db_path,
            epg_path=epg_path,
            timezone_name=timezone_name,
        )
        process = subprocess.Popen(
            _command(
                target, control_address, analyzer.frame_pattern if analyzer else None,
                commercial_active=initial_commercial,
            ), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
    except (OSError, RuntimeError):
        if analyzer is not None:
            analyzer.stop()
        media_pipeline.release_session(pipeline_token)
        raise
    stream = SharedMpegtsStream(
        target, process, pipeline_token, identity=identity,
        control_address=control_address, commercial_active=initial_commercial,
        analyzer=analyzer, context_signature=context_signature or _stream_signature(
            sports_generated=sports_generated,
            profile_identity=profile_identity,
            profile_db_path=profile_db_path,
            epg_path=epg_path,
            timezone_name=timezone_name,
        ),
        created_at=time.monotonic(),
    )
    _STREAMS[target] = stream
    if analyzer is not None:
        analyzer.start()
    return stream


def recycle_streams() -> int:
    """End active shared streams so clients reconnect with current settings."""
    with _LOCK:
        streams = [stream for stream in _STREAMS.values() if stream.process.poll() is None]
    for stream in streams:
        _finish(stream)
    return len(streams)


def _finish(stream: SharedMpegtsStream) -> None:
    with _LOCK:
        if stream.finished:
            return
        stream.finished = True
        if _STREAMS.get(stream.target) is stream:
            _STREAMS.pop(stream.target, None)
        last_stream = not _STREAMS
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
    if stream.analyzer is not None:
        stream.analyzer.stop()
    if last_stream:
        import commercial_detection

        commercial_detection.clear_logo_state()
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


def _subscribe(
    target: str,
    *,
    identity: str = "",
    sports_generated: bool = False,
    profile_identity: str = "",
    profile_db_path: Path | None = None,
    epg_path: Path | None = None,
    timezone_name: str = "America/New_York",
) -> tuple[SharedMpegtsStream, str, Subscriber]:
    with _LOCK:
        requested_signature = _stream_signature(
            sports_generated=sports_generated,
            profile_identity=profile_identity,
            profile_db_path=profile_db_path,
            epg_path=epg_path,
            timezone_name=timezone_name,
        )
        stream = _STREAMS.get(target)
        created = False
        if (
            stream is None or stream.process.poll() is not None
            or stream.context_signature != requested_signature
        ):
            if stream is not None and stream.process.poll() is None:
                _finish(stream)
            stream = _build_stream(
                target,
                identity=identity,
                sports_generated=sports_generated,
                profile_identity=profile_identity,
                profile_db_path=profile_db_path,
                epg_path=epg_path,
                timezone_name=timezone_name,
                context_signature=requested_signature,
            )
            created = True
        elif identity and not stream.identity:
            stream.identity = identity
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


def response_for(
    target: str,
    *,
    identity: str = "",
    sports_generated: bool = False,
    profile_identity: str = "",
    profile_db_path: Path | None = None,
    epg_path: Path | None = None,
    timezone_name: str = "America/New_York",
) -> Response:
    """Serve a shared per-channel MPEG-TS encoder to one downstream client."""
    try:
        stream, subscriber_id, subscriber = _subscribe(
            target,
            identity=identity,
            sports_generated=sports_generated,
            profile_identity=profile_identity,
            profile_db_path=profile_db_path,
            epg_path=epg_path,
            timezone_name=timezone_name,
        )
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
