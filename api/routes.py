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

    @app.get("/api/channels")
    def api_channels():
        return jsonify(
            count=len(core.channels),
            channels=core.channels,
            selected_ids=sorted(core.selected_ids),
            source_mode=core.source_mode,
            source_url_configured=bool(core.last_source_url),
        )

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
