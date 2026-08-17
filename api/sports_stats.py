from __future__ import annotations

from flask import Response, jsonify, request, send_file

import core
from sports import live_stats
from sports import live_stats_transport
from sports import mlb_fake_stats
from sports import mlb_stats_enrichment
from sports import mlb_stats_scorebug
from sports import nfl_demo_stats
from .http import no_cache


# MLB StatsAPI is the primary live-game source. The transport layer keeps ESPN
# available as a fallback, enrichment adds cold daily standings context (GB),
# and the scorebug layer applies canonical team colors plus compact inning/outs.
live_stats_transport.install(live_stats)
mlb_stats_enrichment.install(live_stats)
mlb_stats_scorebug.install(live_stats)


def _install_low_latency_stats_hls() -> None:
    """Tune the experimental synthetic stats streams for fast HLS startup.

    The first prototype reused x264's ``stillimage`` tune. At two frames per
    second that can delay the HLS muxer long enough that the old 14-second
    readiness timeout races the second segment. ``zerolatency`` keeps the same
    simple image2pipe -> H.264 -> HLS path but publishes segments much sooner.
    """
    if getattr(live_stats, "_low_latency_stats_hls_installed", False):
        return

    original_command = live_stats._ffmpeg_command

    def low_latency_command(directory):
        command = list(original_command(directory))
        for index, value in enumerate(command[:-1]):
            if value == "-tune":
                command[index + 1] = "zerolatency"
                break
        return command

    live_stats._ffmpeg_command = low_latency_command
    live_stats.STARTUP_TIMEOUT = 24.0
    nfl_demo_stats.STARTUP_TIMEOUT = 24.0
    mlb_fake_stats.STARTUP_TIMEOUT = 24.0
    live_stats._low_latency_stats_hls_installed = True


_install_low_latency_stats_hls()


def _media_response(path, filename: str):
    if filename == "stream.m3u8":
        response = send_file(path, mimetype="application/x-mpegurl", conditional=True)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        response = send_file(path, mimetype="video/mp2t", conditional=True)
        response.headers["Cache-Control"] = "public, max-age=30"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Accept-Encoding, Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _options_response():
    response = Response(status=204)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Accept-Encoding, Content-Type, Range"
    return response


def register_sports_stats_routes(app):
    @app.get("/api/sports/stats/<int:assigned_number>")
    def sports_stats_state(assigned_number: int):
        try:
            payload = live_stats.state_payload(core.DB_PATH, assigned_number)
        except RuntimeError as exc:
            return no_cache(jsonify(error=str(exc))), 404
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not load MLB live stats: {exc}")), 502
        return no_cache(jsonify(payload))

    @app.route("/sports/stats/<int:assigned_number>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def sports_stats_media(assigned_number: int, filename: str):
        if request.method == "OPTIONS":
            return _options_response()
        try:
            path = live_stats.safe_media_file(core.DB_PATH, assigned_number, filename)
        except RuntimeError as exc:
            return _media_response_error(str(exc), 404)
        except Exception as exc:
            return _media_response_error(f"Could not start MLB stats stream: {exc}", 502)
        if path is None:
            return _media_response_error("MLB stats stream not found.", 404)
        return _media_response(path, filename)

    @app.get("/api/sports/stats-demo/1")
    def sports_stats_demo_state():
        try:
            return no_cache(jsonify(nfl_demo_stats.state_payload()))
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not load ESPN NFL demo stats: {exc}")), 502

    @app.route("/sports/stats-demo/<int:demo_number>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def sports_stats_demo_media(demo_number: int, filename: str):
        if request.method == "OPTIONS":
            return _options_response()
        if demo_number != nfl_demo_stats.DEMO_NUMBER:
            return _media_response_error("NFL stats demo channel not found.", 404)
        try:
            path = nfl_demo_stats.safe_media_file(filename)
        except RuntimeError as exc:
            return _media_response_error(str(exc), 404)
        except Exception as exc:
            return _media_response_error(f"Could not start NFL stats demo stream: {exc}", 502)
        if path is None:
            return _media_response_error("NFL stats demo stream not found.", 404)
        return _media_response(path, filename)

    @app.get("/api/sports/stats-fake/1.2")
    def sports_stats_fake_state():
        return no_cache(jsonify(mlb_fake_stats.state_payload()))

    @app.route("/sports/stats-fake/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def sports_stats_fake_media(filename: str):
        if request.method == "OPTIONS":
            return _options_response()
        try:
            path = mlb_fake_stats.safe_media_file(filename)
        except RuntimeError as exc:
            return _media_response_error(str(exc), 404)
        except Exception as exc:
            return _media_response_error(f"Could not start fake MLB stats stream: {exc}", 502)
        if path is None:
            return _media_response_error("Fake MLB stats stream not found.", 404)
        return _media_response(path, filename)


def _media_response_error(message: str, status: int):
    response = Response(
        f"{message}\n",
        status=status,
        content_type="text/plain; charset=utf-8",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response
