from __future__ import annotations

from flask import Response, jsonify, request, send_file

import core
from sports import live_stats
from sports import live_stats_transport
from .http import no_cache


# ESPN's rich MLB summary/Gamecast endpoint occasionally rejects non-browser
# clients. Install the resilient transport once at route import time so the
# synthetic .1 stream falls back to the scoreboard feed instead of failing.
live_stats_transport.install(live_stats)


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


def register_sports_stats_routes(app):
    @app.get("/api/sports/stats/<int:assigned_number>")
    def sports_stats_state(assigned_number: int):
        try:
            payload = live_stats.state_payload(core.DB_PATH, assigned_number)
        except RuntimeError as exc:
            return no_cache(jsonify(error=str(exc))), 404
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not load ESPN MLB stats: {exc}")), 502
        return no_cache(jsonify(payload))

    @app.route("/sports/stats/<int:assigned_number>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def sports_stats_media(assigned_number: int, filename: str):
        if request.method == "OPTIONS":
            response = Response(status=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Accept-Encoding, Content-Type, Range"
            return response
        try:
            path = live_stats.safe_media_file(core.DB_PATH, assigned_number, filename)
        except RuntimeError as exc:
            return _media_response_error(str(exc), 404)
        except Exception as exc:
            return _media_response_error(f"Could not start MLB stats stream: {exc}", 502)
        if path is None:
            return _media_response_error("MLB stats stream not found.", 404)
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
