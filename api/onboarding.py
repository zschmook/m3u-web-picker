from __future__ import annotations

from flask import jsonify, request

import core
import jellyfin_cache
import master_update_worker
import media_pipeline
import onboarding
import sports


# Install the post-success Jellyfin cleanup wrapper before ui_status installs
# the Master Update reporting wrapper. Cleanup warnings then flow into the same
# final update report without changing normal update execution order.
if onboarding.onboarding_enabled():
    jellyfin_cache.install(core)


def _provider_configured() -> bool:
    return bool(core.primary_provider_source() or core.source_mode == "file")


def _payload() -> dict:
    enabled = onboarding.onboarding_enabled()
    provider_configured = _provider_configured()
    state = onboarding.get_state(
        core.DB_PATH,
        provider_configured=provider_configured,
    )
    if enabled:
        state = onboarding.recover_stale_initial_refresh(
            core.DB_PATH,
            provider_configured=provider_configured,
            worker_running=bool(master_update_worker.payload().get("running")),
        )
    return {
        "enabled": enabled,
        "state": state,
        "provider_configured": provider_configured,
        "sports": {
            "settings": sports.get_settings(core.DB_PATH),
            "rules": sports.get_rules(core.DB_PATH),
            "schedule_api": sports.schedule_api_status_payload(core.DB_PATH),
        },
        "jellyfin": jellyfin_cache.get_settings(core.DB_PATH),
        "media_pipeline": media_pipeline.status(),
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

    @app.post("/api/onboarding/initial-refresh")
    def api_onboarding_initial_refresh():
        if not onboarding.onboarding_enabled():
            return jsonify(error="Initial onboarding refresh is not enabled."), 404
        if not _provider_configured():
            return jsonify(error="Primary provider is not configured."), 409

        current_state = onboarding.get_state(
            core.DB_PATH,
            provider_configured=True,
        )
        answers = current_state.get("answers") or {}
        if answers.get("initial_refresh_completed_at"):
            return jsonify(started=False, pending=False, ready=True, state=current_state), 200

        # Do not claim the one-shot while some unrelated Master Update already
        # owns the worker. The finishing-setup gate will wait for that work to
        # become idle and then start its own onboarding-triggered update, whose
        # completion explicitly verifies the public EPG and Combined XMLTV.
        if master_update_worker.payload().get("running"):
            return jsonify(
                started=False,
                already_running=True,
                pending=bool(answers.get("initial_refresh_pending")),
                in_progress=bool(answers.get("initial_refresh_in_progress")),
                state=current_state,
            ), 202

        claimed = onboarding.claim_initial_refresh(
            core.DB_PATH,
            provider_configured=True,
        )
        if not claimed:
            current_state = onboarding.get_state(
                core.DB_PATH,
                provider_configured=True,
            )
            return jsonify(started=False, pending=False, state=current_state), 200

        try:
            started, master = master_update_worker.start(trigger="onboarding")
        except Exception as exc:
            onboarding.finish_initial_refresh(
                core.DB_PATH,
                provider_configured=True,
                success=False,
                error="Could not start the initial Master Update.",
            )
            print(f"Could not start initial post-onboarding Master Update: {exc}")
            return jsonify(
                error="Could not start the initial Master Update.",
                started=False,
                pending=True,
            ), 500

        if not started:
            # A job won the race after the pre-check. Re-arm the onboarding
            # request so the finishing gate can try again once that job is idle.
            onboarding.finish_initial_refresh(
                core.DB_PATH,
                provider_configured=True,
                success=False,
                error="Another Master Update started first. Retry when it finishes.",
            )
            return jsonify(
                started=False,
                already_running=True,
                pending=True,
                master_update=master,
            ), 202

        return jsonify(
            started=True,
            pending=False,
            master_update=master,
            state=onboarding.get_state(core.DB_PATH, provider_configured=True),
        ), 202

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
