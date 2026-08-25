import re

from flask import Response, jsonify, redirect, request, send_file

import core
import roku_devices
import sports
from guide_epg import enrich_guide_channels
from media import browser, hls
from settings import load_settings
from playback import roku
from sports.generated import generated_publish_lock
from .http import json_error, no_cache


def _resolve_guide_play_target(play_url: str) -> str:
    """Resolve a guide-owned opaque play path without trusting arbitrary URLs."""
    value = str(play_url or "").split("?", 1)[0].strip()
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        return core.manual_stream_target(manual.group(1))
    generated = re.fullmatch(r"/guide/play/sports/(\d+)", value)
    if generated:
        return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


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


def _resolve_roku_host(data: dict) -> tuple[str, str]:
    key = str(data.get("roku_device_key", "") or "").strip()
    if key:
        saved = roku_devices.get_saved(core.DB_PATH, key)
        if saved is None:
            raise ValueError("Saved Roku device was not found.")
        return roku.normalize_host(saved["host"]), key
    return roku.normalize_host(data.get("roku_host", "")), ""


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
        # Generated sports rows and their XMLTV files publish together under this
        # lock. Hold it through enrichment so one response cannot pair an old
        # channel/logo row with programmes from the newly published guide.
        with generated_publish_lock:
            items = core.curated_channels_for_guide()
            sports_settings = sports.get_settings(core.DB_PATH)
            items, epg_status = enrich_guide_channels(
                items,
                core.COMBINED_EPG_PATH,
                timezone_name=str(sports_settings.get("timezone", "America/New_York")),
            )
        response = jsonify(count=len(items), channels=items, epg=epg_status)
        return no_cache(response)

    @app.post("/api/guide/cast/start")
    def api_guide_cast_start():
        data = request.get_json(force=True, silent=True) or {}
        play_url = str(data.get("play_url", "") or "")
        target = _resolve_guide_play_target(play_url)
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
        stopped = hls.stop_session(token) if token else False
        response = jsonify(ok=True, stopped=stopped)
        return no_cache(response)

    @app.get("/api/guide/roku/devices")
    def api_guide_roku_devices():
        return no_cache(jsonify(ok=True, devices=roku_devices.list_saved(core.DB_PATH)))

    @app.post("/api/guide/roku/devices")
    def api_guide_roku_save_device():
        data = request.get_json(force=True, silent=True) or {}
        try:
            host = roku.normalize_host(data.get("roku_host", ""))
            info = roku.device_info(host)
            saved = roku_devices.save_device(core.DB_PATH, host, info)
        except (ValueError, RuntimeError) as exc:
            return json_error(exc, 502)
        return no_cache(jsonify(ok=True, device=saved))

    @app.post("/api/guide/roku/devices/remove")
    def api_guide_roku_remove_device():
        data = request.get_json(force=True, silent=True) or {}
        key = str(data.get("roku_device_key", "") or "").strip()
        if not key:
            return json_error("Choose a saved Roku device first.", 400)
        removed = roku_devices.remove_device(core.DB_PATH, key)
        return no_cache(jsonify(ok=True, removed=removed))

    @app.get("/api/guide/roku/discover")
    def api_guide_roku_discover():
        settings = load_settings()
        lan_host = str(settings.lan_host or "").strip()
        if not lan_host:
            return no_cache(jsonify(ok=True, devices=[], saved_devices=roku_devices.list_saved(core.DB_PATH), subnet=""))
        try:
            devices = roku.discover_devices(lan_host)
            devices = roku_devices.reconcile_discovered(core.DB_PATH, devices)
        except ValueError as exc:
            return no_cache(jsonify(ok=True, devices=[], saved_devices=roku_devices.list_saved(core.DB_PATH), subnet="", warning=str(exc)))
        parts = lan_host.split(".")
        subnet = ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else ""
        return no_cache(jsonify(
            ok=True,
            devices=devices,
            saved_devices=roku_devices.list_saved(core.DB_PATH),
            subnet=subnet,
        ))

    @app.post("/api/guide/roku/test")
    def api_guide_roku_test():
        data = request.get_json(force=True, silent=True) or {}
        try:
            host, requested_key = _resolve_roku_host(data)
            info = roku.device_info(host)
            key = roku_devices.device_key(info)
            saved = roku_devices.get_saved(core.DB_PATH, key) if key else None
            if saved:
                saved = roku_devices.save_device(core.DB_PATH, host, info)
        except (ValueError, RuntimeError) as exc:
            return json_error(exc, 502)
        response = jsonify(
            ok=True,
            roku_host=host,
            roku_device_key=key or requested_key,
            saved=bool(saved),
            device={**info, "device_key": key, "saved": bool(saved)},
        )
        return no_cache(response)

    @app.post("/api/guide/roku/start")
    def api_guide_roku_start():
        data = request.get_json(force=True, silent=True) or {}
        play_url = str(data.get("play_url", "") or "")
        target = _resolve_guide_play_target(play_url)
        if not target:
            return json_error("Curated stream not found.", 404)
        media_origin = _guide_media_origin()
        if not media_origin:
            return json_error("LAN media relay is not configured.", 409)
        try:
            host, requested_key = _resolve_roku_host(data)
            session = hls.start_session(target)
            playlist_path = f"/guide/roku/{session.token}/stream.m3u8"
            media_url = media_origin.rstrip("/") + playlist_path
            roku.launch_dev(host, media_url)
            try:
                info = roku.device_info(host)
            except RuntimeError:
                info = {"name": "Roku"}
            key = roku_devices.device_key(info) or requested_key
            saved = roku_devices.get_saved(core.DB_PATH, key) if key else None
            if saved and info.get("device_id") or saved and info.get("serial_number"):
                saved = roku_devices.save_device(core.DB_PATH, host, info)
        except (ValueError, RuntimeError) as exc:
            if "session" in locals():
                hls.stop_session(session.token)
            return json_error(exc, 502)
        response = jsonify(
            ok=True,
            roku_host=host,
            roku_device_key=key,
            saved=bool(saved),
            device={**info, "device_key": key, "saved": bool(saved)},
            token=session.token,
            playlist_path=playlist_path,
            media_url=media_url,
        )
        return no_cache(response)

    @app.post("/api/guide/roku/stop")
    def api_guide_roku_stop():
        data = request.get_json(force=True, silent=True) or {}
        token = str(data.get("token", "") or "")
        stopped = hls.stop_session(token) if token else False
        host = ""
        try:
            if data.get("roku_device_key") or data.get("roku_host"):
                host, _key = _resolve_roku_host(data)
        except ValueError:
            host = ""
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
        target = core.manual_stream_target(token)
        if not target:
            return Response("Curated stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(
            target,
            preview_session=request.args.get("preview_session", ""),
        )

    @app.get("/guide/play/sports/<int:assigned_number>")
    def guide_play_sports(assigned_number: int):
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(
            target,
            preview_session=request.args.get("preview_session", ""),
        )

    @app.delete("/guide/play/preview/<session_id>")
    def guide_stop_preview(session_id: str):
        return no_cache(jsonify({
            "ok": True,
            "released": browser.stop_preview(session_id),
        }))
