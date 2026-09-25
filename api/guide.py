import re
import threading
import time

from flask import Response, jsonify, redirect, request, send_file

import core
import roku_devices
import sports
from guide_epg import enrich_guide_channels
from media import browser, director, hls
from settings import load_settings
from playback import roku
from sports.generated import generated_publish_lock
from .http import json_error, no_cache
from . import commercials
from . import episode_test
from . import breaking_bad
from . import custom_channels


def custom_channel_insert_position(items):
    """Place custom TV after local/manual channels and before generated sports."""
    return next((index for index,item in enumerate(items)
        if str(item.get('play_url','')).startswith('/guide/play/sports/')),len(items))


_ROKU_DIRECTOR_LOCK = threading.RLock()
_ROKU_DIRECTOR_RECEIVERS: dict[str, str] = {}
_ROKU_DIRECTOR_RELAUNCHING: set[int] = set()


def schedule_director_roku_relaunch(revision: int) -> None:
    """Reload Guide-launched Roku receivers after channel 0.2 changes source."""
    expected_revision = int(revision)
    with _ROKU_DIRECTOR_LOCK:
        if not _ROKU_DIRECTOR_RECEIVERS or expected_revision in _ROKU_DIRECTOR_RELAUNCHING:
            return
        _ROKU_DIRECTOR_RELAUNCHING.add(expected_revision)

    def relaunch_when_ready() -> None:
        try:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                state = director.state_payload()
                if int(state.get("revision") or 0) != expected_revision:
                    return
                if state.get("ready") and not state.get("switching"):
                    with _ROKU_DIRECTOR_LOCK:
                        receivers = list(_ROKU_DIRECTOR_RECEIVERS.items())
                    for host, media_url in receivers:
                        try:
                            roku.launch_dev(host, media_url)
                        except (ValueError, RuntimeError):
                            pass
                    return
                time.sleep(0.25)
        finally:
            with _ROKU_DIRECTOR_LOCK:
                _ROKU_DIRECTOR_RELAUNCHING.discard(expected_revision)

    threading.Thread(
        target=relaunch_when_ready,
        name=f"remote-channel-roku-relaunch-{expected_revision}",
        daemon=True,
    ).start()


def _resolve_guide_play_target(play_url: str) -> str:
    """Resolve a guide-owned opaque play path without trusting arbitrary URLs."""
    value = str(play_url or "").split("?", 1)[0].strip()
    if value == commercials.PLAY_URL:
        return commercials.local_stream_url()
    if value == episode_test.PLAY_URL:
        return episode_test.local_stream_url()
    if value == breaking_bad.PLAY_URL:
        return breaking_bad.local_stream_url()
    custom = re.fullmatch(r'/guide/play/custom/([a-f0-9]{16})',value)
    if custom:
        try: return custom_channels.local_url(custom.group(1))
        except ValueError: return ''
    if value == director.PLAY_URL:
        settings = load_settings()
        return f"http://127.0.0.1:{settings.port}{director.STREAM_PATH}"
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        return core.manual_stream_target(manual.group(1))
    generated = re.fullmatch(r"/guide/play/sports/(\d+)", value)
    if generated:
        return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


def _resolve_guide_hls_targets(play_url: str):
    """Resolve ordered relay candidates and a playback-health callback."""
    value = str(play_url or "").split("?", 1)[0].strip()
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        token = manual.group(1)
        targets = core.manual_stream_candidates(token)

        def remember(target: str | None) -> None:
            core.remember_manual_stream_target(token, target)

        return targets, remember
    target = _resolve_guide_play_target(play_url)
    return ([target] if target else []), None


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
    @app.get("/guide-sw.js")
    def guide_service_worker():
        response = app.send_static_file("js/guide-sw.js")
        response.mimetype = "application/javascript"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/guide"
        return response

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
        items = [director.guide_item(), *items]
        local_channel = breaking_bad.guide_item()
        if local_channel:
            items.insert(0, local_channel)
            epg_status['channel_count'] = epg_status.get('channel_count',0)+1
            epg_status['matched_channels'] = epg_status.get('matched_channels',0)+1
            epg_status['current_channels'] = epg_status.get('current_channels',0)+bool(local_channel['now'])
            epg_status['programme_count'] = epg_status.get('programme_count',0)+len(local_channel['upcoming'])+bool(local_channel['now'])
        commercial = commercials.guide_item()
        custom_items=custom_channels.guide_items()
        custom_position=custom_channel_insert_position(items)
        items[custom_position:custom_position]=custom_items
        for custom in custom_items:
            epg_status['channel_count'] = epg_status.get('channel_count',0)+1
            epg_status['matched_channels'] = epg_status.get('matched_channels',0)+bool(custom['now'] or custom['upcoming'])
            epg_status['current_channels'] = epg_status.get('current_channels',0)+bool(custom['now'])
            epg_status['programme_count'] = epg_status.get('programme_count',0)+len(custom['upcoming'])+bool(custom['now'])
        if commercial:
            items.insert(0, commercial)
        test_channel = episode_test.guide_item()
        if test_channel:
            items.insert(0, test_channel)
        response = jsonify(count=len(items), channels=items, epg=epg_status)
        return no_cache(response)

    @app.post("/api/guide/cast/start")
    def api_guide_cast_start():
        data = request.get_json(force=True, silent=True) or {}
        play_url = str(data.get("play_url", "") or "")
        targets, on_target = _resolve_guide_hls_targets(play_url)
        if not targets:
            return json_error("Curated stream not found.", 404)
        try:
            session = hls.start_session(targets, on_target=on_target)
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
        direct_director = play_url.split("?", 1)[0].strip() == director.PLAY_URL
        targets, on_target = _resolve_guide_hls_targets(play_url)
        if not targets:
            return json_error("Curated stream not found.", 404)
        media_origin = _guide_media_origin()
        if not media_origin:
            return json_error("LAN media relay is not configured.", 409)
        try:
            host, requested_key = _resolve_roku_host(data)
            session = None
            if direct_director:
                from .remote import _manual_channel_payload

                _manual_channel_payload()
                if director.safe_media_file("stream.m3u8") is None:
                    raise RuntimeError("Channel 0.2 did not become ready for Roku playback.")
                playlist_path = director.STREAM_PATH
                media_url = media_origin.rstrip("/") + playlist_path
                with _ROKU_DIRECTOR_LOCK:
                    _ROKU_DIRECTOR_RECEIVERS[host] = media_url
            else:
                session = hls.start_session(targets, on_target=on_target)
                playlist_path = f"/guide/roku/{session.token}/stream.m3u8"
                media_url = media_origin.rstrip("/") + playlist_path
                with _ROKU_DIRECTOR_LOCK:
                    _ROKU_DIRECTOR_RECEIVERS.pop(host, None)
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
            with _ROKU_DIRECTOR_LOCK:
                _ROKU_DIRECTOR_RECEIVERS.pop(locals().get("host", ""), None)
            if "session" in locals() and session is not None:
                hls.stop_session(session.token)
            return json_error(exc, 502)
        response = jsonify(
            ok=True,
            roku_host=host,
            roku_device_key=key,
            saved=bool(saved),
            device={**info, "device_key": key, "saved": bool(saved)},
            token=session.token if session is not None else "",
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
            with _ROKU_DIRECTOR_LOCK:
                _ROKU_DIRECTOR_RECEIVERS.pop(host, None)
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
        return browser.response_for(target)

    @app.get("/guide/play/sports/<int:assigned_number>")
    def guide_play_sports(assigned_number: int):
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(target)

    @app.get("/guide/listen")
    def guide_listen():
        target = _resolve_guide_play_target(request.args.get("play_url", ""))
        if not target:
            return Response("Curated stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return browser.response_for(target, audio_only=True)
