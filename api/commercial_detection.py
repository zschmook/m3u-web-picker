from __future__ import annotations

from flask import Response, jsonify, request

import commercial_detection
import commercial_profiles
import core
from media import mpegts


def register_commercial_detection_routes(app):
    def channel_profile_payload() -> dict:
        stream_identity = str(request.args.get("stream_identity", "") or "").strip()
        snapshot = mpegts.active_stream_profile_snapshot(stream_identity)
        identity = str(snapshot.get("channel_identity", "") or "")
        if not identity:
            return {}
        channel_profile = commercial_profiles.profile(core.DB_PATH, identity)
        scored = commercial_profiles.score_features(
            channel_profile,
            dict(snapshot.get("features") or {}),
        )
        return {
            "channel_identity": identity,
            "ready": bool(channel_profile.get("ready")),
            "program_samples": int(channel_profile.get("program_samples") or 0),
            "commercial_samples": int(channel_profile.get("commercial_samples") or 0),
            "user_confirmed_commercial_samples": int(
                channel_profile.get("user_confirmed_commercial_samples") or 0
            ),
            "score": round(float(scored.get("score") or 0) * 100, 1),
            "weights": {
                name: round(float(value or 0) * 100, 1)
                for name, value in dict(scored.get("weights") or {}).items()
            },
            "retention_days": commercial_profiles.RETENTION_DAYS,
            "history_window_minutes": 30,
            "history": commercial_profiles.recent(
                core.DB_PATH,
                identity,
                limit=288,
                minutes=30,
            ),
        }

    @app.get("/api/commercial-break")
    def api_commercial_break_status():
        return jsonify(
            {
                **commercial_detection.payload(),
                "injection": mpegts.commercial_status(),
                "channel_profile": channel_profile_payload(),
            }
        )

    @app.patch("/api/commercial-break")
    def api_commercial_break_manual():
        data = request.get_json(force=True, silent=True) or {}
        action = str(data.get("action", "") or "").strip().lower()
        stream_identity = str(data.get("stream_identity", "") or "").strip()
        if action not in {"start", "end"}:
            return jsonify(error="Action must be 'start' or 'end'."), 400
        active = action == "start"
        state = commercial_detection.set_manual(active)
        return jsonify({
            **state,
            "injection": mpegts.set_commercial(active, stream_identity=stream_identity),
        })

    @app.post("/api/commercial-break/feedback")
    def api_commercial_break_feedback():
        data = request.get_json(force=True, silent=True) or {}
        label = str(data.get("label", "") or "").strip().lower()
        stream_identity = str(data.get("stream_identity", "") or "").strip()
        if label not in {"program", "commercial"}:
            return jsonify(error="Label must be 'program' or 'commercial'."), 400
        snapshot = mpegts.active_stream_profile_snapshot(stream_identity)
        identity = str(snapshot.get("channel_identity", "") or "")
        if not identity:
            return jsonify(error="No non-sports FFmpeg stream is available."), 409
        commercial_profiles.record(
            core.DB_PATH,
            identity,
            label=label,
            source="user",
            features=dict(snapshot.get("features") or {}),
            detector_state=snapshot.get("detector_state", ""),
            commercial_reason=snapshot.get("commercial_reason", ""),
        )
        applied_to_stream = bool(
            label == "program" and mpegts.apply_program_feedback(stream_identity)
        )
        profile = commercial_profiles.profile(core.DB_PATH, identity)
        return jsonify(
            ok=True,
            label=label,
            channel_identity=identity,
            program_samples=profile.get("program_samples", 0),
            commercial_samples=profile.get("commercial_samples", 0),
            ready=bool(profile.get("ready")),
            effective_weight=commercial_profiles.USER_SAMPLE_WEIGHT,
            applied_to_stream=applied_to_stream,
        )

    @app.get("/api/commercial-break/profiles/export")
    def api_commercial_profile_export():
        passphrase = str(
            request.args.get("passphrase", "")
            or request.headers.get("X-Profile-Passphrase", "")
            or "",
        ).strip()
        if not passphrase:
            return jsonify(error="Passphrase is required for export."), 400
        channel_identities = request.args.getlist("channel_identity")
        try:
            blob = commercial_profiles.dump_profile_blob(
                core.DB_PATH,
                channel_identities=channel_identities,
                passphrase=passphrase,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        response = Response(blob, mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = (
            'attachment; filename="commercial-profiles.pickle"'
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/api/commercial-break/profiles/import")
    def api_commercial_profile_import():
        form_passphrase = str(request.form.get("passphrase", "") or "").strip()
        query_passphrase = str(request.args.get("passphrase", "") or "").strip()
        json_passphrase = None
        header_passphrase = str(
            request.headers.get("X-Profile-Passphrase", "") or ""
        ).strip()
        try:
            data = request.get_json(force=True, silent=True) or {}
            json_passphrase = str(data.get("passphrase", "") or "").strip()
        except Exception:
            data = {}
        passphrase = form_passphrase or query_passphrase or header_passphrase or json_passphrase
        if not passphrase:
            return jsonify(error="Passphrase is required for import."), 400

        uploaded = request.files.get("file")
        raw_payload = uploaded.read() if uploaded else request.get_data(cache=False)
        if not raw_payload:
            return jsonify(error="No profile blob uploaded."), 400
        form_overwrite = str(request.form.get("overwrite", "")).strip().lower() in {"1", "true", "on", "yes"}
        overwrite = bool(data.get("overwrite", False)) or form_overwrite

        try:
            result = commercial_profiles.load_profile_blob(
                core.DB_PATH,
                raw_payload,
                passphrase=passphrase,
                overwrite=overwrite,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(
            ok=True,
            imported=result["inserted"],
            skipped=result["skipped"],
            invalid=result["invalid"],
        )
