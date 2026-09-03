from __future__ import annotations

from flask import jsonify, request, send_file

import core
import dvr
import sports
from media import browser
from .http import no_cache


def _validated_channel(data: dict) -> dict:
    play_url = str(data.get("play_url", "") or "").strip()
    channel = next(
        (
            item for item in core.curated_channels_for_guide()
            if str(item.get("play_url", "") or "").strip() == play_url
        ),
        None,
    )
    if channel is None:
        raise ValueError("That curated channel is no longer available.")
    tvg_id = str(data.get("tvg_id", "") or "").strip()
    if not tvg_id or tvg_id != str(channel.get("tvg_id", "") or "").strip():
        raise ValueError("The program no longer matches the selected channel.")
    return channel


def _schedule_payload(data: dict, channel: dict, *, rule_id: int | None = None) -> dict:
    return dvr.schedule_recording(
        core.DB_PATH,
        rule_id=rule_id,
        play_url=str(channel.get("play_url") or ""),
        tvg_id=str(channel.get("tvg_id") or ""),
        channel_name=str(channel.get("name") or ""),
        title=data.get("title", ""),
        subtitle=data.get("subtitle", ""),
        description=data.get("description", ""),
        start_at=data.get("start"),
        stop_at=data.get("stop"),
    )


def _sync_series() -> int:
    timezone_name = str(sports.get_settings(core.DB_PATH).get("timezone") or "America/New_York")
    return dvr.sync_series_rules(
        core.DB_PATH,
        channels=core.curated_channels_for_guide(),
        epg_path=core.COMBINED_EPG_PATH,
        timezone_name=timezone_name,
    )


def register_dvr_routes(app):
    @app.get("/api/dvr")
    def api_dvr_state():
        return no_cache(jsonify(dvr.state(core.DB_PATH)))

    @app.patch("/api/dvr/settings")
    def api_dvr_settings():
        data = request.get_json(force=True, silent=True) or {}
        try:
            dvr.save_settings(data)
        except (TypeError, ValueError) as exc:
            return no_cache(jsonify(error=str(exc), **dvr.state(core.DB_PATH))), 400
        return no_cache(jsonify(dvr.state(core.DB_PATH)))

    @app.post("/api/dvr/storage/validate")
    def api_dvr_validate_storage():
        data = request.get_json(force=True, silent=True) or {}
        result = dvr.validate_host_path(str(data.get("host_path", "") or ""))
        return no_cache(jsonify(result)), (200 if result.get("ok") else 400)

    @app.post("/api/dvr/maintenance")
    def api_dvr_start_maintenance():
        started = dvr.start_manual_maintenance(core.DB_PATH)
        return no_cache(jsonify(ok=True, started=started, dvr=dvr.state(core.DB_PATH))), (202 if started else 200)

    @app.post("/api/dvr/recordings")
    def api_dvr_schedule_recording():
        data = request.get_json(force=True, silent=True) or {}
        try:
            dvr.require_ready()
            channel = _validated_channel(data)
            item = _schedule_payload(data, channel)
            dvr.tick(core.DB_PATH)
        except (TypeError, ValueError) as exc:
            return no_cache(jsonify(error=str(exc))), 400
        return no_cache(jsonify(ok=True, recording=item, dvr=dvr.state(core.DB_PATH))), 201

    @app.post("/api/dvr/series")
    def api_dvr_schedule_series():
        data = request.get_json(force=True, silent=True) or {}
        try:
            dvr.require_ready()
            channel = _validated_channel(data)
            rule = dvr.create_series_rule(
                core.DB_PATH,
                title=str(data.get("title", "") or ""),
                tvg_id=str(channel.get("tvg_id") or ""),
                channel_name=str(channel.get("name") or ""),
                start_at=data.get("start"),
            )
            item = _schedule_payload(data, channel, rule_id=int(rule["id"]))
            _sync_series()
            dvr.tick(core.DB_PATH)
        except (TypeError, ValueError) as exc:
            return no_cache(jsonify(error=str(exc))), 400
        return no_cache(jsonify(ok=True, rule=rule, recording=item, dvr=dvr.state(core.DB_PATH))), 201

    @app.post("/api/dvr/recordings/<int:recording_id>/cancel")
    def api_dvr_cancel_recording(recording_id: int):
        cancelled = dvr.cancel_recording(core.DB_PATH, recording_id)
        return no_cache(jsonify(ok=True, cancelled=cancelled, dvr=dvr.state(core.DB_PATH)))

    @app.delete("/api/dvr/recordings/<int:recording_id>")
    def api_dvr_delete_recording(recording_id: int):
        try:
            deleted = dvr.delete_recording(core.DB_PATH, recording_id)
        except ValueError as exc:
            return no_cache(jsonify(error=str(exc))), 409
        if not deleted:
            return no_cache(jsonify(error="Recording not found.")), 404
        return no_cache(jsonify(ok=True, deleted=True, dvr=dvr.state(core.DB_PATH)))

    @app.delete("/api/dvr/series/<int:rule_id>")
    def api_dvr_delete_series(rule_id: int):
        removed = dvr.remove_series_rule(core.DB_PATH, rule_id)
        if not removed:
            return no_cache(jsonify(error="Series rule not found.")), 404
        return no_cache(jsonify(ok=True, removed=True, dvr=dvr.state(core.DB_PATH)))

    @app.get("/api/dvr/recordings/<int:recording_id>/file")
    def api_dvr_recording_file(recording_id: int):
        path = dvr.recording_file(core.DB_PATH, recording_id)
        if path is None:
            return no_cache(jsonify(error="Completed recording file not found.")), 404
        response = send_file(path, conditional=True, download_name=path.name)
        response.headers["Cache-Control"] = "private, no-cache"
        response.headers["Accept-Ranges"] = "bytes"
        return response

    @app.get("/api/dvr/recordings/<int:recording_id>/play")
    def api_dvr_play_recording(recording_id: int):
        path = dvr.recording_file(core.DB_PATH, recording_id)
        if path is None:
            return no_cache(jsonify(error="Completed recording file not found.")), 404
        if not dvr.begin_playback(recording_id):
            return no_cache(jsonify(error="This recording is being converted. Try playback again when the nightly conversion finishes.")), 409
        response = browser.response_for(
            str(path),
            on_stop=lambda: dvr.end_playback(recording_id),
        )
        if response.status_code >= 400:
            dvr.end_playback(recording_id)
        return response
