from datetime import datetime

from flask import Response, jsonify, request, send_file

import core


def register_routes(app):
    @app.post("/api/load-url")
    def api_load_url():
        data = request.get_json(force=True, silent=True) or {}
        url = str(data.get("url", "")).strip()

        if not url.startswith(("http://", "https://")):
            return jsonify(error="URL must start with http:// or https://"), 400

        try:
            text = core.download_m3u_text(url)
            core.MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
            core.channels = core.parse_m3u_text(text)
            core.last_source_url = url
            core.source_mode = "url"
            core.apply_saved_selections_to_loaded_channels()
            core.write_current_playlist()
            core.last_refresh = datetime.now().isoformat(timespec="seconds")
            core.save_config()
        except Exception as exc:
            return jsonify(error=str(exc)), 500

        return jsonify(
            count=len(core.channels),
            channels=core.channels,
            selected_ids=sorted(core.selected_ids),
        )

    @app.post("/api/upload")
    def api_upload():
        uploaded = request.files.get("file")
        if not uploaded:
            return jsonify(error="No file uploaded."), 400

        try:
            raw = uploaded.read()
            text = raw.decode("utf-8-sig", errors="replace")
            core.MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
            core.channels = core.parse_m3u_text(text)
            core.source_mode = "file"
            core.apply_saved_selections_to_loaded_channels()
            core.write_current_playlist()
            core.last_refresh = datetime.now().isoformat(timespec="seconds")
            core.save_config()
        except Exception as exc:
            return jsonify(error=str(exc)), 500

        return jsonify(
            count=len(core.channels),
            channels=core.channels,
            selected_ids=sorted(core.selected_ids),
        )

    @app.post("/api/selection")
    def api_selection():
        data = request.get_json(force=True, silent=True) or {}
        ids = data.get("ids", [])

        core.selected_ids = set(int(i) for i in ids)
        count = core.write_current_playlist()
        core.save_config()

        return jsonify(
            count=count,
            path=str(core.PLAYLIST_PATH),
            url="/playlist/custom.m3u",
        )


    @app.get("/api/selection/order")
    def api_selection_order():
        core.write_current_playlist()
        return jsonify(channels=core.selected_channel_order_payload())

    @app.post("/api/selection/order")
    def api_save_selection_order():
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(k).strip() for k in data.get("keys", []) if str(k).strip()]

        count = core.save_channel_order(keys)
        core.write_current_playlist()
        core.save_config()

        return jsonify(count=count, url="/playlist/custom.m3u")

    @app.get("/api/channels")
    def api_channels():
        return jsonify(
            count=len(core.channels),
            channels=core.channels,
            selected_ids=sorted(core.selected_ids),
            source_mode=core.source_mode,
            source_url_configured=bool(core.last_source_url),
        )

    @app.get("/api/epg")
    def api_epg_sources():
        return jsonify(
            sources=core.epg_sources_payload(),
            schedule={
                "after_m3u_minutes": core.EPG_REFRESH_OFFSET_MINUTES,
                "hour": core.SCHEDULE_HOUR,
                "minute": core.SCHEDULE_MINUTE + core.EPG_REFRESH_OFFSET_MINUTES,
            },
        )

    @app.post("/api/epg")
    def api_add_epg_source():
        data = request.get_json(force=True, silent=True) or {}
        name = str(data.get("name", "")).strip()
        url = str(data.get("url", "")).strip()

        try:
            source = core.add_epg_source(name, url)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        # Keep EPG cache separate from the M3U playlist/export cache.
        # Fetch once now so the served /epg/<name>.xml link can work immediately;
        # if it fails, the source is still saved and the scheduled 3:15 refresh retries.
        ok, message = core.refresh_epg_source(source["id"])
        payload = next((item for item in core.epg_sources_payload() if item["id"] == source["id"]), None)
        return jsonify(source=payload, refreshed=ok, message=message)

    @app.delete("/api/epg/<source_id>")
    def api_delete_epg_source(source_id: str):
        deleted = core.delete_epg_source(source_id)
        if not deleted:
            return jsonify(error="EPG source not found."), 404
        return jsonify(deleted=True, sources=core.epg_sources_payload())



    @app.get("/api/status")
    def api_status():
        return jsonify(
            loaded=len(core.channels),
            selected=len(core.selected_ids),
            saved_selections=len(core.load_selected_keys_from_db()),
            playlist_exists=core.PLAYLIST_PATH.exists(),
            playlist_url="/playlist/custom.m3u",
            playlist_all_url="/playlist/all.m3u",
            playlist_path=str(core.PLAYLIST_PATH),
            source_url_configured=bool(core.last_source_url),
            source_mode=core.source_mode,
            last_refresh=core.last_refresh,
            schedule={"hour": core.SCHEDULE_HOUR, "minute": core.SCHEDULE_MINUTE},
            epg_sources=core.epg_sources_payload(),
            epg_schedule={"offset_minutes_after_m3u": core.EPG_REFRESH_OFFSET_MINUTES},
        )

    
    @app.get("/export")
    def export_playlist():
        return send_file(
            core.PLAYLIST_PATH,
            as_attachment=True,
            download_name="download.m3u",
            mimetype="audio/x-mpegurl",
        )

    @app.get("/playlist/custom.m3u")
    def playlist():
        if not core.PLAYLIST_PATH.exists():
            return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")

        return send_file(
            core.PLAYLIST_PATH,
            mimetype="audio/x-mpegurl",
            as_attachment=False,
            download_name=core.PLAYLIST_NAME,
        )

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


    def serve_epg_xml(source_id: str):
        source_id = core.normalize_epg_id(source_id)
        path = core.epg_cache_path(source_id)
        source = core.find_epg_source(source_id)

        if not source:
            return Response("EPG source not found.\n", content_type="text/plain; charset=utf-8", status=404)

        # Generate the cache on first request if startup has not finished it yet.
        # This keeps the /epg/<name>.xml URL usable after a fresh install/rebuild.
        if not path.exists():
            ok, message = core.refresh_epg_source(source_id)
            if not ok or not path.exists():
                return Response(
                    f"EPG cache could not be generated: {message}\n",
                    content_type="text/plain; charset=utf-8",
                    status=502,
                )

        response = Response(path.read_bytes(), mimetype="application/xml")
        response.headers["Content-Disposition"] = f'inline; filename="{source_id}.xml"'
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/epg/<source_id>.xml")
    def epg_xml(source_id: str):
        return serve_epg_xml(source_id)

    @app.get("/epg/<source_id>")
    def epg_xml_without_suffix(source_id: str):
        return serve_epg_xml(source_id)
