from datetime import datetime
import os
import re
import shutil
import subprocess

from flask import Response, jsonify, redirect, request, send_file, stream_with_context

import core
import sports
from . import cast_hls


def _ffmpeg_browser_response(target: str) -> Response:
    """Transcode one curated IPTV stream to browser-friendly fragmented MP4.

    The provider URL never reaches the guide UI. ffmpeg reads it server-side and
    emits H.264/AAC fMP4 on stdout. The process is torn down when the browser
    closes or switches channels. This is intentionally an experimental,
    one-viewer-at-a-time style path rather than a general media server.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return Response(
            "Browser playback is unavailable because ffmpeg is not installed.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-i",
        target,
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-max_muxing_queue_size",
        "2048",
        "-f",
        "mp4",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration",
        "1000000",
        "pipe:1",
    ]

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except OSError as exc:
        return Response(
            f"Could not start ffmpeg: {exc}\n",
            status=502,
            content_type="text/plain; charset=utf-8",
        )

    def generate():
        try:
            if process.stdout is None:
                return
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    response = Response(
        stream_with_context(generate()),
        content_type="video/mp4",
        direct_passthrough=True,
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Content-Disposition"] = 'inline; filename="live.mp4"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The Google Cast Default Media Receiver fetches media directly from this
    # endpoint, so the response must be usable cross-origin from the receiver.
    # The provider URL still never leaves the server; this only exposes the
    # already-curated ffmpeg output.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Content-Type"
    return response


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


def _cast_cors(response: Response) -> Response:
    # Cast adaptive-stream requests originate from the receiver application,
    # not from the localhost sender page. Echo the actual Origin when present
    # so the response satisfies CAF's adaptive-media CORS requirements.
    origin = str(request.headers.get("Origin", "") or "").strip()
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    if origin:
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Origin, Accept, Accept-Encoding, Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"
    return response


def register_routes(app):
    @app.post("/api/load-url")
    def api_load_url():
        if core.primary_provider_source() or core.source_mode == "file":
            return jsonify(error="Remove the current primary source before adding another one."), 409
        data = request.get_json(force=True, silent=True) or {}
        url = str(data.get("url", "")).strip()
        username = str(data.get("username", "") or "")
        password = str(data.get("password", "") or "")
        name = str(data.get("name", "") or "Primary")
        if not url.startswith(("http://", "https://")):
            return jsonify(error="URL must start with http:// or https://"), 400

        try:
            source, text, parsed = core.detect_provider_source(
                name,
                url,
                username=username,
                password=password,
                role="primary",
            )
            core.install_primary_provider(source, text, parsed)
        except ValueError as exc:
            return jsonify(error=core.redact_url_credentials(str(exc))), 400
        except Exception as exc:
            return jsonify(error=core.redact_url_credentials(str(exc))), 500

        return jsonify(
            count=len(core.combined_channels_for_api()),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
            providers=core.provider_sources_payload(),
        )

    @app.post("/api/upload")
    def api_upload():
        if core.primary_provider_source() or core.source_mode == "file":
            return jsonify(error="Remove the current primary source before adding another one."), 409
        uploaded = request.files.get("file")
        if not uploaded:
            return jsonify(error="No file uploaded."), 400
        try:
            raw = uploaded.read()
            text = raw.decode("utf-8-sig", errors="replace")
            parsed = core.parse_m3u_text(text)
            with core.state_lock:
                core.atomic_write_text(core.MASTER_CACHE_PATH, text)
                core.channels = parsed
                core.provider_sources = []
                core.last_source_url = ""
                core.source_mode = "file"
                sports.discover_catalog_from_channels(core.DB_PATH, core.channels)
                core.apply_saved_selections_to_loaded_channels()
                core.last_refresh = datetime.now().astimezone().isoformat(timespec="seconds")
                core.save_config()
                core.write_current_playlist()
        except Exception as exc:
            return jsonify(error=str(exc)), 500

        return jsonify(
            count=len(core.combined_channels_for_api()),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
        )

    @app.post("/api/selection")
    def api_selection():
        data = request.get_json(force=True, silent=True) or {}
        ids = data.get("ids", [])
        valid_provider_ids = {int(channel["id"]) for channel in core.channels}
        core.selected_ids = {
            int(value)
            for value in ids
            if str(value).lstrip("-").isdigit()
            and int(value) >= 0
            and int(value) in valid_provider_ids
        }
        count = core.write_current_playlist()
        core.save_config()
        return jsonify(count=count, path=str(core.PLAYLIST_PATH), url="/playlist/channels.m3u")

    @app.get("/api/selection/order")
    def api_selection_order():
        core.write_current_playlist()
        return jsonify(channels=core.selected_channel_order_payload())

    @app.post("/api/selection/order")
    def api_save_selection_order():
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(key).strip() for key in data.get("keys", []) if str(key).strip()]
        count = core.save_channel_order(keys)
        core.write_current_playlist()
        core.save_config()
        return jsonify(count=count, url="/playlist/channels.m3u")

    @app.get("/api/channels")
    def api_channels():
        combined = core.combined_channels_for_api()
        return jsonify(
            count=len(combined),
            channels=combined,
            selected_ids=core.selected_ids_payload(),
            source_mode=core.source_mode,
            source_url_configured=bool(core.last_source_url),
            providers=core.provider_sources_payload(),
        )

    @app.get("/api/providers")
    def api_provider_sources():
        return jsonify(sources=core.provider_sources_payload())

    @app.get("/api/providers/progress")
    def api_provider_progress():
        return jsonify(core.provider_progress_payload())

    @app.post("/api/providers/fallback")
    def api_add_fallback_provider():
        if not core.primary_provider_source():
            return jsonify(error="Load a URL primary provider before adding fallbacks."), 409
        data = request.get_json(force=True, silent=True) or {}
        try:
            source = core.add_fallback_provider(
                str(data.get("name", "")),
                str(data.get("url", "")),
                username=str(data.get("username", "") or ""),
                password=str(data.get("password", "") or ""),
            )
        except ValueError as exc:
            return jsonify(error=core.redact_url_credentials(str(exc))), 400
        except Exception as exc:
            return jsonify(error=core.redact_url_credentials(str(exc))), 500
        payload = next(
            (item for item in core.provider_sources_payload() if item["id"] == source["id"]),
            None,
        )
        return jsonify(source=payload, sources=core.provider_sources_payload()), 201

    @app.delete("/api/providers/primary")
    def api_remove_primary_provider():
        if not core.remove_primary_source():
            return jsonify(error="Primary source not found."), 404
        return jsonify(
            removed=True,
            sources=core.provider_sources_payload(),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
            source_mode=core.source_mode,
        )

    @app.delete("/api/providers/<source_id>")
    def api_delete_fallback_provider(source_id: str):
        if not core.delete_fallback_provider(source_id):
            return jsonify(error="Fallback provider not found."), 404
        return jsonify(deleted=True, sources=core.provider_sources_payload())

    @app.get("/api/epg")
    def api_epg_sources():
        return jsonify(
            sources=core.epg_sources_payload(),
            builtins=core.epg_builtin_payload(),
            public_epg=core.public_epg_payload(),
            master_update=core.master_update_payload(),
        )

    @app.post("/api/epg")
    def api_add_epg_source():
        data = request.get_json(force=True, silent=True) or {}
        try:
            source = core.add_epg_source(
                str(data.get("name", "")),
                str(data.get("url", "")),
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        ok, message = core.refresh_epg_source(str(source.get("id", "")))
        try:
            core.ensure_epg_exports_current(force=True)
        except Exception as exc:
            print(f"Could not rebuild combined guide after EPG refresh: {exc}")
        payload = next(
            (item for item in core.epg_sources_payload() if item["id"] == source["id"]),
            None,
        )
        return jsonify(source=payload, refreshed=ok, message=message)

    @app.delete("/api/epg/<source_id>")
    def api_delete_epg_source(source_id: str):
        if not core.delete_epg_source(source_id):
            return jsonify(error="EPG source not found."), 404
        try:
            core.ensure_epg_exports_current(force=True)
        except Exception as exc:
            print(f"Could not rebuild combined guide after EPG deletion: {exc}")
        return jsonify(deleted=True, sources=core.epg_sources_payload())

    @app.get("/api/master-update")
    def api_master_update():
        return jsonify(master_update=core.master_update_payload())

    @app.patch("/api/master-update")
    def api_update_master_update():
        data = request.get_json(force=True, silent=True) or {}
        try:
            payload = core.update_master_settings(
                enabled=data.get("enabled") if "enabled" in data else None,
                refresh_time=data.get("time") if "time" in data else None,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not save master update settings: {exc}")
            return jsonify(error="Could not save master update settings."), 500
        return jsonify(master_update=payload)

    @app.post("/api/master-update/run")
    def api_run_master_update():
        try:
            result = core.run_master_update(trigger="manual")
        except core.SportsScanError as exc:
            return jsonify(error=str(exc), master_update=core.master_update_payload()), 409
        except Exception as exc:
            print(f"Unexpected master update error: {exc}")
            return jsonify(
                error="Master update failed. Existing outputs were kept.",
                master_update=core.master_update_payload(),
            ), 500
        return jsonify(
            result=result,
            master_update=core.master_update_payload(),
            sports=core.enrich_sports_status(sports.status_payload(core.DB_PATH)),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
        )

    @app.get("/api/public-epg")
    def api_public_epg():
        return jsonify(public_epg=core.public_epg_payload())

    @app.patch("/api/public-epg")
    def api_update_public_epg():
        data = request.get_json(force=True, silent=True) or {}
        enabled_codes = data.get("enabled_codes")
        if not isinstance(enabled_codes, list):
            return jsonify(error="enabled_codes must be a list of country codes."), 400
        try:
            payload = core.update_public_epg_countries(enabled_codes)
            core.ensure_epg_exports_current(force=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not save public EPG settings: {exc}")
            return jsonify(error="Could not save public EPG settings."), 500
        return jsonify(public_epg=payload)

    @app.get("/api/status")
    def api_status():
        generated_count = len(sports.generated_rows(core.DB_PATH))
        return jsonify(
            loaded=len(core.channels),
            selected=len(core.selected_ids) + generated_count,
            manual_selected=len(core.selected_ids),
            generated_sports=generated_count,
            saved_selections=len(core.load_selected_keys_from_db()),
            playlist_exists=core.PLAYLIST_PATH.exists(),
            playlist_url="/playlist/channels.m3u",
            playlist_all_url="/playlist/all.m3u",
            sports_epg_url="/epg/sports.xml",
            combined_epg_url="/epg/epg.xml",
            playlist_path=str(core.PLAYLIST_PATH),
            source_url_configured=bool(core.last_source_url),
            source_mode=core.source_mode,
            last_refresh=core.last_refresh,
            master_update=core.master_update_payload(),
            public_epg=core.public_epg_payload(),
            epg_sources=core.epg_sources_payload(),
            data_dir=str(core.DATA_DIR),
        )

    @app.get("/api/groups")
    def api_groups():
        return jsonify(groups=core.list_custom_groups())

    @app.post("/api/groups")
    def api_create_group():
        data = request.get_json(force=True, silent=True) or {}
        try:
            group = core.create_custom_group(str(data.get("name", "")))
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(group=group), 201

    @app.get("/api/groups/<slug>/channels")
    def api_group_channels(slug: str):
        return jsonify(channel_keys=core.group_member_keys(slug))

    @app.post("/api/groups/<slug>/channels")
    def api_add_group_channels(slug: str):
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(key).strip() for key in data.get("channel_keys", []) if str(key).strip()]
        try:
            added = core.add_channels_to_group(slug, keys)
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(added=added)

    @app.delete("/api/groups/<slug>/channels")
    def api_remove_group_channels(slug: str):
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(key).strip() for key in data.get("channel_keys", []) if str(key).strip()]
        try:
            removed = core.remove_channels_from_group(slug, keys)
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(removed=removed)

    @app.get("/api/sports/settings")
    def api_sports_settings():
        payload = core.enrich_sports_status(sports.status_payload(core.DB_PATH))
        return jsonify(payload)

    @app.patch("/api/sports/settings")
    def api_update_sports_settings():
        data = request.get_json(force=True, silent=True) or {}
        previous_enabled = bool(sports.get_settings(core.DB_PATH).get("enabled"))
        try:
            settings = sports.update_settings(core.DB_PATH, data)
            enabled_changed = (
                "enabled" in data
                and bool(settings.get("enabled")) != previous_enabled
            )
            if enabled_changed:
                # The master switch changes the served outputs immediately.
                # Generated rows remain cached internally for 24 hours while off.
                sports.rebuild_epg_exports(
                    core.DB_PATH,
                    base_epg_path=core.active_base_epg_path(),
                    base_channel_ids=core.selected_xmltv_ids(),
                    fallback_epg_paths=core.configured_epg_fallback_paths(core.active_base_epg_path()),
                    sports_epg_path=core.SPORTS_EPG_PATH,
                    combined_epg_path=core.COMBINED_EPG_PATH,
                )
                core.write_current_playlist()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not save sports settings: {exc}")
            return jsonify(error="Could not save sports settings."), 500
        payload = sports.status_payload(core.DB_PATH)
        payload["settings"] = settings
        return jsonify(core.enrich_sports_status(payload))

    @app.get("/api/sports/schedule-api")
    def api_sports_schedule_api():
        return jsonify(schedule_api=sports.schedule_api_status(core.DB_PATH))

    @app.patch("/api/sports/schedule-api")
    def api_update_sports_schedule_api():
        data = request.get_json(force=True, silent=True) or {}
        try:
            payload = sports.update_schedule_api_config(
                core.DB_PATH,
                enabled=data.get("enabled") if "enabled" in data else None,
                url=data.get("url") if "url" in data else None,
                api_key=data.get("api_key") if "api_key" in data else None,
                clear_key=bool(data.get("clear_key", False)),
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not save schedule API settings: {exc}")
            return jsonify(error="Could not save schedule API settings."), 500
        return jsonify(schedule_api=payload)

    @app.post("/api/sports/schedule-api/refresh")
    def api_refresh_sports_schedule_api():
        try:
            result = sports.refresh_schedule_api_if_due(core.DB_PATH, force=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not refresh schedule API: {exc}")
            return jsonify(error="Could not refresh schedule API."), 500
        return jsonify(
            result=result,
            schedule_api=sports.schedule_api_status(core.DB_PATH),
        )

    @app.get("/api/sports/catalog")
    def api_sports_catalog():
        return jsonify(
            items=sports.catalog_payload(
                core.DB_PATH,
                query=str(request.args.get("q", "")),
                scope_type=str(request.args.get("type", "")),
            )
        )

    @app.post("/api/sports/rules")
    def api_add_sports_rule():
        data = request.get_json(force=True, silent=True) or {}
        try:
            if isinstance(data.get("items"), list):
                rules = sports.add_rules(core.DB_PATH, data["items"])
            else:
                sports.add_rule(core.DB_PATH, data)
                rules = sports.get_rules(core.DB_PATH)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not add sports selection: {exc}")
            return jsonify(error="Could not add the sports selection."), 500
        return jsonify(rules=rules)

    @app.patch("/api/sports/rules/<int:rule_id>")
    def api_update_sports_rule(rule_id: int):
        data = request.get_json(force=True, silent=True) or {}
        try:
            sports.update_rule(core.DB_PATH, rule_id, data)
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(rules=sports.get_rules(core.DB_PATH))

    @app.delete("/api/sports/rules/<int:rule_id>")
    def api_delete_sports_rule(rule_id: int):
        sports.delete_rule(core.DB_PATH, rule_id)
        return jsonify(rules=sports.get_rules(core.DB_PATH))

    @app.post("/api/sports/scan")
    def api_sports_scan():
        try:
            result = core.run_master_update(trigger="manual")
        except core.SportsScanError as exc:
            return jsonify(error=str(exc), sports=core.enrich_sports_status(sports.status_payload(core.DB_PATH))), 409
        except Exception as exc:
            print(f"Unexpected sports update error: {exc}")
            return jsonify(
                error="Sports update failed. Existing sports channels were kept.",
                sports=core.enrich_sports_status(sports.status_payload(core.DB_PATH)),
            ), 500
        return jsonify(
            result=result,
            sports=core.enrich_sports_status(sports.status_payload(core.DB_PATH)),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
        )

    @app.post("/api/sports/scan/cancel")
    def api_cancel_sports_scan():
        accepted, message = core.request_sports_scan_cancel()
        payload = core.enrich_sports_status(sports.status_payload(core.DB_PATH))
        status = 202 if accepted else 409
        return jsonify(accepted=accepted, message=message, sports=payload), status

    @app.get("/api/sports/status")
    def api_sports_status():
        payload = core.enrich_sports_status(sports.status_payload(core.DB_PATH))
        return jsonify(payload)

    @app.get("/api/sports/guide-check")
    def api_sports_guide_check():
        return jsonify(
            sports.validate_guide_exports(
                core.DB_PATH,
                playlist_path=core.PLAYLIST_PATH,
                sports_epg_path=core.SPORTS_EPG_PATH,
                combined_epg_path=core.COMBINED_EPG_PATH,
            )
        )

    @app.get("/api/guide/config")
    def api_guide_config():
        lan_host = str(os.environ.get("M3U_LAN_HOST", "") or "").strip()
        external_port = str(os.environ.get("M3U_EXTERNAL_PORT", "1000") or "1000").strip()
        if lan_host:
            media_origin = f"http://{lan_host}:{external_port}"
        elif request.host and request.host.split(":", 1)[0] not in {"localhost", "127.0.0.1"}:
            media_origin = f"{request.scheme}://{request.host}"
        else:
            media_origin = ""
        response = jsonify(
            lan_host=lan_host,
            external_port=external_port,
            media_origin=media_origin,
            sender_origin=f"{request.scheme}://{request.host}",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/api/guide/ping")
    def api_guide_ping():
        response = jsonify(ok=True, service="m3u-web-picker-v30-experiments", port=int(os.environ.get("M3U_EXTERNAL_PORT", "1000")))
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return response

    @app.get("/api/guide/channels")
    def api_guide_channels():
        items = core.curated_channels_for_guide()
        response = jsonify(count=len(items), channels=items)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.post("/api/guide/cast/start")
    def api_guide_cast_start():
        data = request.get_json(force=True, silent=True) or {}
        play_url = str(data.get("play_url", "") or "")
        target = _resolve_guide_play_target(play_url)
        if not target:
            return jsonify(error="Curated stream not found."), 404
        try:
            session = cast_hls.start_session(target)
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 502
        response = jsonify(
            ok=True,
            token=session.token,
            playlist_path=f"/guide/cast/{session.token}/stream.m3u8",
            content_type="application/x-mpegurl",
            segment_format="mpeg2-ts",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.post("/api/guide/cast/stop")
    def api_guide_cast_stop():
        data = request.get_json(force=True, silent=True) or {}
        token = str(data.get("token", "") or "")
        stopped = cast_hls.stop_session(token) if token else bool(cast_hls.stop_all_sessions())
        response = jsonify(ok=True, stopped=stopped)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.route("/guide/cast/<token>/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def guide_cast_hls(token: str, filename: str):
        if request.method == "OPTIONS":
            return _cast_cors(Response(status=204))
        path = cast_hls.safe_media_file(token, filename)
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
        return _ffmpeg_browser_response(target)

    @app.get("/guide/play/sports/<int:assigned_number>")
    def guide_play_sports(assigned_number: int):
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        return _ffmpeg_browser_response(target)

    def serve_named_epg(source_id: str):
        source = core.find_epg_source(source_id)
        if not source:
            return Response(
                "EPG source not found.\n",
                content_type="text/plain; charset=utf-8",
                status=404,
            )
        path = core.epg_cache_path(source_id)
        if not path.exists():
            ok, message = core.refresh_epg_source(source_id)
            if not ok or not path.exists():
                return Response(
                    f"EPG cache could not be generated: {message}\n",
                    content_type="text/plain; charset=utf-8",
                    status=502,
                )
        response = send_file(
            path,
            mimetype="application/xml",
            as_attachment=False,
            download_name=f"{core.normalize_epg_id(source_id)}.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/sports/stream/<int:assigned_number>")
    def generated_sports_stream(assigned_number: int):
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        response = redirect(target, code=307)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/epg/<source_id>.xml")
    def named_epg(source_id: str):
        if source_id in {"sports", "combined", "epg"}:
            return Response("Not found.\n", status=404)
        return serve_named_epg(source_id)

    @app.get("/epg/sports.xml")
    def sports_epg():
        core.ensure_epg_exports_current()
        response = send_file(
            core.SPORTS_EPG_PATH,
            mimetype="application/xml",
            as_attachment=False,
            download_name="sports.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-M3U-Picker-Guide-Revision"] = str(int(core.SPORTS_EPG_PATH.stat().st_mtime))
        return response

    @app.get("/epg/epg.xml")
    @app.get("/epg/combined.xml")
    def combined_epg():
        core.ensure_epg_exports_current()
        response = send_file(
            core.COMBINED_EPG_PATH,
            mimetype="application/xml",
            as_attachment=False,
            download_name="epg.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-M3U-Picker-Guide-Revision"] = str(int(core.COMBINED_EPG_PATH.stat().st_mtime))
        return response

    @app.get("/playlist/channels.m3u")
    @app.get("/playlist/custom.m3u")
    def playlist():
        guide_url = request.url_root.rstrip("/") + "/epg/epg.xml"
        if not core.PLAYLIST_PATH.exists():
            text = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"\n'
        else:
            text = core.PLAYLIST_PATH.read_text(encoding="utf-8-sig", errors="replace")
            lines = text.splitlines()
            header = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"'
            if lines and lines[0].startswith("#EXTM3U"):
                lines[0] = header
            else:
                lines.insert(0, header)
            base_url = request.url_root.rstrip("/")
            lines = [
                f"{base_url}{line}" if line.startswith("/sports/stream/") else line
                for line in lines
            ]
            text = "\n".join(lines) + "\n"
        response = Response(text, mimetype="audio/x-mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/playlist/all.m3u")
    def playlist_all():
        return Response(
            core.m3u_from_channels(core.all_grouped_channels()),
            mimetype="audio/x-mpegurl",
        )

    @app.get("/playlist/group/<slug>.m3u")
    def playlist_group(slug: str):
        _, items = core.group_channels_for_slug(slug)
        return Response(core.m3u_from_channels(items), mimetype="audio/x-mpegurl")
