from datetime import datetime

from flask import Response, jsonify, request, send_file

import core
import sports


def register_routes(app):
    @app.post("/api/load-url")
    def api_load_url():
        data = request.get_json(force=True, silent=True) or {}
        url = str(data.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return jsonify(error="URL must start with http:// or https://"), 400

        try:
            text = core.download_m3u_text(url)
            parsed = core.parse_m3u_text(text)
            with core.state_lock:
                core.atomic_write_text(core.MASTER_CACHE_PATH, text)
                core.channels = parsed
                core.last_source_url = url
                core.source_mode = "url"
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

    @app.post("/api/upload")
    def api_upload():
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
        return jsonify(count=count, path=str(core.PLAYLIST_PATH), url="/playlist/custom.m3u")

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
        return jsonify(count=count, url="/playlist/custom.m3u")

    @app.get("/api/channels")
    def api_channels():
        combined = core.combined_channels_for_api()
        return jsonify(
            count=len(combined),
            channels=combined,
            selected_ids=core.selected_ids_payload(),
            source_mode=core.source_mode,
            source_url_configured=bool(core.last_source_url),
        )

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
            playlist_url="/playlist/custom.m3u",
            playlist_all_url="/playlist/all.m3u",
            playlist_path=str(core.PLAYLIST_PATH),
            source_url_configured=bool(core.last_source_url),
            source_mode=core.source_mode,
            last_refresh=core.last_refresh,
            schedule={"hour": core.SCHEDULE_HOUR, "minute": core.SCHEDULE_MINUTE},
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
        payload = sports.status_payload(core.DB_PATH)
        payload["number_conflicts"] = core.sports_number_conflicts()
        return jsonify(payload)

    @app.patch("/api/sports/settings")
    def api_update_sports_settings():
        data = request.get_json(force=True, silent=True) or {}
        try:
            settings = sports.update_settings(core.DB_PATH, data)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception as exc:
            print(f"Could not save sports settings: {exc}")
            return jsonify(error="Could not save sports settings."), 500
        payload = sports.status_payload(core.DB_PATH)
        payload["settings"] = settings
        payload["number_conflicts"] = core.sports_number_conflicts()
        return jsonify(payload)

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
            result = core.run_sports_scan(trigger="manual", refresh_source=True)
        except core.SportsScanError as exc:
            return jsonify(error=str(exc)), 409
        except Exception as exc:
            print(f"Unexpected sports update error: {exc}")
            return jsonify(error="Sports update failed. Existing sports channels were kept."), 500
        return jsonify(
            result=result,
            sports=sports.status_payload(core.DB_PATH),
            channels=core.combined_channels_for_api(),
            selected_ids=core.selected_ids_payload(),
        )

    @app.get("/api/sports/status")
    def api_sports_status():
        payload = sports.status_payload(core.DB_PATH)
        payload["number_conflicts"] = core.sports_number_conflicts()
        return jsonify(payload)

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
