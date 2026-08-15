from datetime import datetime

from flask import jsonify, request

import core
import sports


def register_provider_routes(app):
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

    @app.post("/api/providers/validate")
    def api_validate_provider():
        """Probe provider connectivity/authentication without installing it.

        The first-run wizard uses this to keep its schedule and Continue button
        locked until the provider is actually reachable. Direct M3U URLs are
        probed immediately. Xtream base URLs are probed again with credentials
        once both username and password have been supplied.
        """
        if core.primary_provider_source() or core.source_mode == "file":
            return jsonify(error="A primary provider is already configured."), 409

        data = request.get_json(force=True, silent=True) or {}
        url = str(data.get("url", "") or "").strip()
        username = str(data.get("username", "") or "")
        password = str(data.get("password", "") or "")
        name = str(data.get("name", "") or "Primary")

        if not url.startswith(("http://", "https://")):
            return jsonify(error="URL must start with http:// or https://"), 400
        if bool(username) != bool(password):
            return jsonify(
                valid=False,
                waiting_for_credentials=True,
                error="Enter both the Xtream username and password before validation.",
            ), 409

        try:
            source, _text, _parsed = core.detect_provider_source(
                name,
                url,
                username=username,
                password=password,
                role="primary",
                load_channels=False,
            )
        except ValueError as exc:
            message = core.redact_url_credentials(str(exc))
            return jsonify(
                valid=False,
                waiting_for_credentials=not bool(username and password),
                error=message,
            ), 400
        except Exception as exc:
            message = core.redact_url_credentials(str(exc))
            return jsonify(
                valid=False,
                waiting_for_credentials=not bool(username and password),
                error=message,
            ), 502

        return jsonify(
            valid=True,
            kind=str(source.get("kind", "m3u") or "m3u"),
            xtream_api=bool(source.get("xtream_api")),
            account_status=source.get("account_status"),
            expires_at=source.get("expires_at"),
            credentials_used=bool(username and password),
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
