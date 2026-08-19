from __future__ import annotations

from flask import Response, jsonify, render_template, request, send_file

from media import browser
from sports import multiview
from .http import no_cache


def _media_response(path, filename: str):
    mimetype = "application/x-mpegurl" if filename == "stream.m3u8" else "video/mp2t"
    is_playlist = filename == "stream.m3u8"
    # VLC may stop at the end of the first HLS window when a changing live
    # manifest is served through conditional file responses. Always return a
    # fresh 200 for playlists; media segments remain immutable and cacheable.
    response = send_file(path, mimetype=mimetype, conditional=not is_playlist, etag=not is_playlist)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" if is_playlist else "public, max-age=30"
    if is_playlist:
        response.headers.pop("ETag", None)
        response.headers.pop("Last-Modified", None)
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def register_multiview_routes(app):
    @app.get("/sports/multiview")
    def multiview_director():
        return render_template("multiview.html")

    @app.route("/api/sports/multiview", methods=["GET", "PATCH", "POST"])
    def multiview_state():
        try:
            if request.method == "PATCH":
                return no_cache(jsonify(multiview.update_state(request.get_json(force=True, silent=True) or {})))
            if request.method == "POST":
                return no_cache(jsonify(multiview.reset_state()))
            return no_cache(jsonify(multiview.state_payload()))
        except ValueError as exc:
            return no_cache(jsonify(error=str(exc))), 400
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not update multiview: {exc}")), 502

    @app.route("/sports/multiview/ncaa/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def multiview_media(filename: str):
        if request.method == "OPTIONS":
            response = Response(status=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        try:
            path = multiview.safe_media_file(filename)
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not start multiview: {exc}")), 502
        if path is None:
            return no_cache(jsonify(error="Multiview media not found")), 404
        return _media_response(path, filename)

    @app.get("/sports/multiview/game/<game_id>")
    def multiview_game_player(game_id: str):
        game = multiview.GAME_BY_ID.get(game_id)
        if game is None:
            return no_cache(jsonify(error="Unknown test game")), 404
        return render_template("multiview_game.html", game=game)

    @app.get("/sports/multiview/play/<game_id>")
    def multiview_stub_browser_playback(game_id: str):
        if game_id not in multiview.GAME_BY_ID:
            return no_cache(jsonify(error="Unknown test game")), 404
        target = f'{request.host_url.rstrip("/")}/sports/multiview/stub/{game_id}/stream.m3u8'
        return browser.response_for(target)

    @app.route("/sports/multiview/stub/<game_id>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def multiview_stub_media(game_id: str, filename: str):
        if request.method == "OPTIONS":
            response = Response(status=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        try:
            path = multiview.safe_stub_media_file(game_id, filename)
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not start test game: {exc}")), 502
        if path is None:
            return no_cache(jsonify(error="Test game media not found")), 404
        return _media_response(path, filename)

    @app.get("/playlist/multiview.m3u")
    def multiview_playlist():
        base_url = request.host_url.rstrip("/")
        return Response(multiview.playlist(base_url), mimetype="audio/x-mpegurl")
