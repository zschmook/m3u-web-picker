"""Temporary, request-driven local commercials channel (separate playlist)."""
import json
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import threading

from flask import Response, jsonify, request, stream_with_context, render_template
from media.ffmpeg import terminate
from media.commercials_debug import DebugSession, snapshot

PLAY_URL = "/guide/play/temp/commercials"
STREAM_PATH = "/stream/temp/commercials.ts"


def local_stream_url():
    from settings import load_settings
    return f"http://127.0.0.1:{load_settings().port}{STREAM_PATH}"


def guide_item():
    try:
        count = len(clip_paths())
    except (OSError, ValueError, KeyError, TypeError):
        count = 0
    if not count:
        return None
    return {
        "number": "0.01", "name": "Commercials Only", "group": "Temporary",
        "logo": "", "tvg_id": "temp-commercials", "generated": True,
        "subtitle": f"{count} vintage ads/promos · starts when played",
        "play_url": PLAY_URL, "available": True,
    }


def clip_paths():
    root = Path(os.environ.get("M3U_COMMERCIALS_DIR", "commercials/clips")).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    paths = []
    for row in manifest["clips"]:
        name = row.get("clip", "")
        if not row.get("include_in_ad_pool", False) or not re.fullmatch(r"ad_\d+\.mp4", name):
            continue
        path = (root / name).resolve()
        if path.parent == root and path.is_file():
            paths.append(path)
    return list(dict.fromkeys(paths))


def concat_text(paths):
    # FFmpeg concat quoting, not shell quoting. No shell is used to start it.
    return "ffconcat version 1.0\n" + "".join(
        "file '" + p.as_posix().replace("'", "'\\''") + "'\n" for p in paths
    )


def command(playlist):
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-re", "-stream_loop", "-1", "-f", "concat", "-safe", "0",
        "-i", str(playlist), "-map", "0:v:0", "-map", "0:a:0",
        "-c", "copy", "-bsf:v", "h264_mp4toannexb,dump_extra=freq=keyframe",
        "-f", "mpegts", "-mpegts_flags", "+resend_headers",
        "-muxdelay", "0", "-muxpreload", "0", "pipe:1",
    ]


def stream_chunks(paths, disconnected=None):
    random.SystemRandom().shuffle(paths)
    debug = DebugSession(paths)
    with tempfile.TemporaryDirectory(prefix="commercials-channel-") as directory:
        playlist = Path(directory) / "queue.ffconcat"
        playlist.write_text(concat_text(paths), encoding="utf-8")
        args = command(playlist)
        args[1:1] = ['-progress', 'pipe:2', '-stats_period', '1']
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except OSError as exc:
            debug.error(str(exc))
            debug.close()
            raise
        def monitor():
            size = 0
            for raw in iter(process.stderr.readline, b''):
                line = raw.decode(errors='replace').strip()
                key, _, value = line.partition('=')
                if key == 'total_size' and value.isdigit():
                    size = int(value)
                if key == 'out_time_us':
                    try:
                        debug.advance(max(0,int(value))/1_000_000, size)
                    except ValueError:
                        pass
                elif '=' not in line and line:
                    debug.error(line)
        worker = threading.Thread(target=monitor, name='commercials-debug', daemon=True)
        worker.start()
        try:
            while not (callable(disconnected) and disconnected()):
                chunk = process.stdout.read(188 * 128)
                if not chunk:
                    break
                yield chunk
        finally:
            terminate(process)
            worker.join(timeout=3)
            debug.close(process.poll())
            process.stderr.close()
            process.stdout.close()


def register_commercial_routes(app):
    @app.get('/api/commercials/stats')
    def commercials_stats():
        return jsonify(snapshot()), {'Cache-Control':'no-store'}

    @app.get('/commercials/stats')
    def commercials_debug_page():
        return render_template('commercials_debug.html')

    @app.get(PLAY_URL)
    def commercials_browser_play():
        from media import browser
        return browser.response_for(local_stream_url())

    @app.route("/playlist/commercials.m3u", methods=["GET", "HEAD"])
    def commercials_playlist():
        text = '#EXTM3U\n#EXTINF:-1 tvg-id="temp-commercials" tvg-chno="0.01" group-title="Temporary",Commercials Only\n'
        text += request.url_root.rstrip("/") + "/stream/temp/commercials.ts\n"
        return Response(text, mimetype="audio/x-mpegurl", headers={"Cache-Control": "no-store"})

    @app.route("/stream/temp/commercials.ts", methods=["GET", "HEAD"])
    def commercials_stream():
        try:
            paths = clip_paths()
        except (OSError, ValueError, KeyError, TypeError):
            paths = []
        if not paths:
            return Response("Commercial clips are unavailable.\n", status=503)
        if request.method == "HEAD":
            return Response(content_type="video/mp2t")
        disconnected = request.environ.get("waitress.client_disconnected")
        return Response(
            stream_with_context(stream_chunks(paths, disconnected)),
            content_type="video/mp2t", direct_passthrough=True,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
