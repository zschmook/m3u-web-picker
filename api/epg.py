from flask import jsonify, request

import core
import master_update_worker
import sports


def _no_store(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def register_epg_routes(app):
    @app.get("/api/epg")
    def api_epg_sources():
        return jsonify(
            sources=core.epg_sources_payload(),
            builtins=core.epg_builtin_payload(),
            public_epg=core.public_epg_payload(),
            master_update=master_update_worker.payload(),
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
        return _no_store(jsonify(master_update=master_update_worker.payload()))

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
        return _no_store(jsonify(master_update=payload))

    @app.post("/api/master-update/run")
    def api_run_master_update():
        """Kick off a Master Update without tying up a request worker.

        The browser follows the live state from GET /api/master-update.  Keeping
        the long provider/EPG/sports pipeline out of this HTTP request lets
        navigation, static assets, and status polling remain responsive for the
        entire update.
        """
        try:
            started, payload = master_update_worker.start(trigger="manual")
        except Exception as exc:
            print(f"Could not start Master Update worker: {exc}")
            response = jsonify(
                error="Could not start the Master Update worker.",
                master_update=master_update_worker.payload(),
            )
            return _no_store(response), 500

        if not started:
            response = jsonify(
                error="A Master Update is already running.",
                started=False,
                master_update=payload,
            )
            return _no_store(response), 409

        response = jsonify(
            started=True,
            master_update=payload,
        )
        return _no_store(response), 202

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
            master_update=master_update_worker.payload(),
            public_epg=core.public_epg_payload(),
            epg_sources=core.epg_sources_payload(),
            data_dir=str(core.DATA_DIR),
        )
