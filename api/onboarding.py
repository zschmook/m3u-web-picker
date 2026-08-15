from __future__ import annotations

from flask import jsonify, request

import core
import jellyfin_cache
import onboarding
import sports


# Install the post-success Jellyfin cleanup only for the experimental -dev
# runtime, and do it before ui_status installs the master-update reporting
# wrapper. That keeps normal imports/tests behavior unchanged while allowing
# cache-cleanup warnings to flow into the same update report in -dev.
if onboarding.dev_onboarding_enabled():
    jellyfin_cache.install(core)


def _provider_configured() -> bool:
    return bool(core.primary_provider_source() or core.source_mode == "file")


def _payload() -> dict:
    return {
        "dev_enabled": onboarding.dev_onboarding_enabled(),
        "state": onboarding.get_state(
            core.DB_PATH,
            provider_configured=_provider_configured(),
        ),
        "provider_configured": _provider_configured(),
        "sports": {
            "settings": sports.get_settings(core.DB_PATH),
            "rules": sports.get_rules(core.DB_PATH),
            "schedule_api": sports.schedule_api_status_payload(core.DB_PATH),
        },
        "jellyfin": jellyfin_cache.get_settings(core.DB_PATH),
    }


def register_onboarding_routes(app):
    @app.get("/api/onboarding")
    def api_onboarding():
        return jsonify(_payload())

    @app.patch("/api/onboarding")
    def api_update_onboarding():
        data = request.get_json(force=True, silent=True) or {}
        try:
            state = onboarding.update_state(
                core.DB_PATH,
                provider_configured=_provider_configured(),
                current_step=data.get("current_step") if "current_step" in data else None,
                answers=data.get("answers") if isinstance(data.get("answers"), dict) else None,
            )
        except (TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(state=state)

    @app.post("/api/onboarding/complete")
    def api_complete_onboarding():
        if not _provider_configured():
            return jsonify(error="Configure a primary provider before finishing setup."), 409
        state = onboarding.mark_complete(
            core.DB_PATH,
            provider_configured=True,
        )
        return jsonify(state=state, complete=True)

    @app.get("/api/jellyfin-cache")
    def api_jellyfin_cache():
        return jsonify(jellyfin=jellyfin_cache.get_settings(core.DB_PATH))

    @app.post("/api/jellyfin-cache/validate")
    def api_validate_jellyfin_cache():
        data = request.get_json(force=True, silent=True) or {}
        result = jellyfin_cache.validate_host_path(str(data.get("host_path", "") or ""))
        return jsonify(result), 200 if result.get("ok") else 400

    @app.patch("/api/jellyfin-cache")
    def api_update_jellyfin_cache():
        data = request.get_json(force=True, silent=True) or {}
        try:
            settings = jellyfin_cache.update_settings(
                core.DB_PATH,
                using_jellyfin=data.get("using_jellyfin") if "using_jellyfin" in data else None,
                cleanup_enabled=data.get("cleanup_enabled") if "cleanup_enabled" in data else None,
                acknowledged=data.get("acknowledged") if "acknowledged" in data else None,
                host_path=data.get("host_path") if "host_path" in data else None,
            )
        except ValueError as exc:
            return jsonify(error=str(exc), jellyfin=jellyfin_cache.get_settings(core.DB_PATH)), 400
        return jsonify(jellyfin=settings)
