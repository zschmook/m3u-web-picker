from flask import jsonify, request

import core
import sports


def register_sports_routes(app):
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
            result = core.run_sports_scan(trigger="manual")
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
