from flask import Response, jsonify, redirect, request, send_file

import core
from media import browser, hls
from playback import roku
from playback.sessions import REMOTE_SESSIONS
from playback.targets import resolve_play_target
from settings import load_settings
from .http import json_error, no_cache


def _guide_media_origin() -> str:
    settings = load_settings()
    if settings.lan_host:
        return f"http://{settings.lan_host}:{settings.external_port}"
    if request.host and request.host.split(":", 1)[0] not in {"localhost", "127.0.0.1"}:
        return f"{request.scheme}://{request.host}"
    return ""


def _cast_cors(response: Response) -> Response:
    origin = str(request.headers.get("Origin", "") or "").strip()
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    if origin:
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Accept-Encoding, Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"
    return response


def register_guide_routes(app):
    @app.get("/api/guide/config")
    def api_guide_config():
        settings = load_settings()
        response = jsonify(
            lan_host=settings.lan_host,
            external_port=str(settings.external_port),
            media_origin=_guide_media_origin(),
            sender_origin=f"{request.scheme}://{request.host}",
        )
        return no_cache(response)

    @app.get("/api/guide/ping")
    def api_guide_ping():
        response = jsonify(ok=True, service="m3u-web-picker-v30-experiments", port=load_settings().external_port)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return response

    @app.get("/api/guide/channels")
    def api_guide_channels():
        items = core.curated_channels_for_guide()
        response = jsonify(count=len(items), channels=items)
        return no_cache(response)

    @app.post("/api/guide/cast/start")
    def api_guide_cast_start():
        data = request.get_json(force=True, silent=True) or {}
        target = resolve_play_target(str(data.get("play_url", "") or ""))
        if not target:
            return json_error("Curated stream not found.", 404)
        try:
            session = hls.start_session(target)
        except RuntimeError as exc:
            return json_error(exc, 502)
        response = jsonify(
            ok=True,
            token=session.token,
            playlist_path=f"/guide/cast/{session.token}/stream.m3u8",
            content_type="application/x-mpegurl",
            segment_format="mpeg2-ts",
        )
        return no_cache(response)

    @app.post("/api/guide/cast/stop")
    def api_guide_cast_stop():
        data = request.get_json(force=True, silent=True) or {}
        token = str(data.get("token", "") or "")
        stopped = hls.stop_session(token) if token else bool(hls.stop_all_sessions())
        response = jsonify(ok=True, stopped=stopped)
        return no_cache(response)

    @app.get("/api/guide/roku/discover")
    def api_guide_roku_discover():
        settings = load_settings()
        lan_host = str(settings.lan_host or "").strip()
        if not lan_host:
            return no_cache(jsonify(ok=True, devices=[], network=""))
        try:
            devices = roku.discover_devices(lan_host)
        except ValueError as exc:
            return no_cache(jsonify(ok=True, devices=[], network="", warning=str(exc)))
        return no_cache(jsonify(ok=True, devices=devices, network=lan_host))

    @app.post("/api/guide/roku/test")
    def api_guide_roku_test():
        data = request.get_json(force=True, silent=True) or {}
        try:
            host = roku.normalize_host(data.get("roku_host", ""))
            info = roku.device_info(host)
        except (ValueError, RuntimeError) as exc:
            return json_error(exc, 502)
        response = jsonify(ok=True, roku_host=host, device=info)
        return no_cache(response)

    @app.get("/api/guide/roku/sessions")
    def api_guide_roku_sessions():
        sessions = [
            {
                "device_key": item.get("device_key", ""),
                "host": item.get("host", ""),
                "name": item.get("name", "Roku"),
                "active": True,
            }
            for item in REMOTE_SESSIONS.snapshot("roku")
        ]
        return no_cache(jsonify(ok=True, sessions=sessions))

    @app.post("/api/guide/roku/start")
    def api_guide_roku_start():
        data = request.get_json(force=True, silent=True) or {}
        target = resolve_play_target(str(data.get("play_url", "") or ""))
        if not target:
            return json_error("Curated stream not found.", 404)
        media_origin = _guide_media_origin()
        if not media_origin:
            return json_error("LAN media relay is not configured.", 409)

        session = None
        try:
            host = roku.normalize_host(data.get("roku_host", ""))
            info = roku.device_info(host)
            device_key = str(info.get("device_key") or host)
            session = hls.start_session(target)
            playlist_path = f"/guide/roku/{session.token}/stream.m3u8"
            media_url = media_origin.rstrip("/") + playlist_path
            roku.launch_dev(host, media_url)
        except (ValueError, RuntimeError) as exc:
            if session is not None:
                hls.stop_session(session.token)
            return json_error(exc, 502)

        previous = REMOTE_SESSIONS.replace(
            "roku",
            device_key,
            {
                "host": host,
                "name": info.get("name", "Roku"),
                "token": session.token,
                "media_url": media_url,
            },
        )
        if previous:
            old_token = str(previous.get("token", "") or "")
            if old_token and old_token != session.token:
                hls.stop_session(old_token)

        response = jsonify(
            ok=True,
            roku_host=host,
            device=info,
            device_key=device_key,
            token=session.token,
            playlist_path=playlist_path,
            media_url=media_url,
        )
        return no_cache(response)

    @app.post("/api/guide/roku/stop")
    def api_guide_roku_stop():
        data = request.get_json(force=True, silent=True) or {}
        token = str(data.get("token", "") or "")
        host = str(data.get("roku_host", "") or "").strip()

        registered = REMOTE_SESSIONS.find_by_token("roku", token) if token else None
        if registered is None and host:
            registered = REMOTE_SESSIONS.find_by_host("roku", host)
        if registered is not None:
            device_key, session_info = registered
            REMOTE_SESSIONS.pop("roku", device_key)
            token = str(session_info.get("token", "") or token)
            host = str(session_info.get("host", "") or host)

        stopped = hls.stop_session(token) if token else False
        home_sent = False
        if host:
            try:
                roku.send_home(host)
                home_sent = True
            except (ValueError, RuntimeError):
                home_sent = False
        response = jsonify(ok=True, stopped=stopped, home_sent=home_sent)
        return no_cache(response)

    @app.route("/guide/roku/<token>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def guide_roku_hls(token: str, filename: str):
        if request.method == "OPTIONS":
            return _cast_cors(Response(status=204))
        path = hls.safe_media_file(token, filename)
        if path is None:
            return _cast_cors(Response("Roku stream not found.\n", status=404, content_type="text/plain; charset=utf-8"))
        if filename == "stream.m3u8":
            response = send_file(path, mimetype="application/x-mpegurl", conditional=True)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        else:
            response = send_file(path, mimetype="video/mp2t", conditional=True)
            response.headers["Cache-Control"] = "public, max-age=30"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Accel-Buffering"] = "no"
        return _cast_cors(response)

    @app.route("/guide/cast/<token>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def guide_cast_hls(token: str, filename: str):
        if request.method == "OPTIONS":
            return _cast_cors(Response(status=204))
        path = hls.safe_media_file(token, filename)
        if path is None:
            return _cast_cors(Response("Cast stream not found.\n", status=404, content_type="text/plain; charset=utf-8"))
        if filename == "stream.m3u8":
            response = send_file(path, mimetype="application/x-mpegurl", conditional=True)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        else:
            response = send_file(path, mimetype="video/mp2t", conditional=True)
            response.headers["Cache-Control"] = "public, max-age=30"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Accel-Buffering"] = "no"
        return _cast_cors(response)

    @app.get("/guide/stream/manual/<token>")
    def guide_manual_stream(token: str):
        target = core.manual_stream_target(token)
        if target:
            response = redirect(target, code=307)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        return Response("Curated stream not found.\n", status=404, content_type="text/plain; charset=utf-8")

    @app.get("/guide/play/manual/<token>")
    def guide_play_manual(token: str):
        target = resolve_play_target(f"/guide/play/manual/{token}")
        if not target:
            return Response("Curated stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(target)

    @app.get("/guide/play/sports/<int:assigned_number>")
    def guide_play_sports(assigned_number: int):
        target = resolve_play_target(f"/guide/play/sports/{assigned_number}")
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(target)
