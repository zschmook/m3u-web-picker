from __future__ import annotations

from flask import jsonify

import core
import hdhr_config
import roku_devices
import sports
from media import hls
from .hdhr import HDHR_TUNER_COUNT


def _stage(name: str, status: str, detail: str = "", *, kind: str = "system") -> dict:
    return {
        "name": str(name),
        "status": str(status),
        "detail": str(detail or ""),
        "kind": str(kind),
    }


def _active_hls_sessions() -> int:
    # The relay registry is process-local by design. Read it under the module's
    # lock and count only live ffmpeg processes; do not mutate session state.
    try:
        with hls._LOCK:  # noqa: SLF001 - same-application status introspection
            sessions = list(hls._SESSIONS.values())  # noqa: SLF001
        return sum(1 for session in sessions if session.process.poll() is None)
    except Exception:
        return 0


def _update_health() -> dict:
    stages: list[dict] = []
    providers = core.provider_sources_payload()
    primary = next((item for item in providers if item.get("role") == "primary"), None)

    if primary:
        provider_error = str(primary.get("last_error") or "").strip()
        provider_status = str(primary.get("account_status") or "").strip()
        if provider_error:
            stages.append(_stage("Primary Provider", "error", provider_error, kind="provider"))
        else:
            detail = f"{int(primary.get('channel_count') or 0):,} channels"
            if provider_status:
                detail += f" · {provider_status}"
            stages.append(_stage("Primary Provider", "success", detail, kind="provider"))
    else:
        stages.append(_stage("Primary Provider", "setup", "No primary provider configured.", kind="provider"))

    for source in providers:
        if source.get("role") != "fallback":
            continue
        error = str(source.get("last_error") or "").strip()
        warning = str(source.get("warning") or "").strip()
        name = str(source.get("name") or "Fallback Provider")
        if error:
            stages.append(_stage(name, "error", error, kind="provider"))
        elif warning:
            stages.append(_stage(name, "warning", warning, kind="provider"))
        elif not source.get("deferred"):
            stages.append(_stage(name, "success", f"{int(source.get('channel_count') or 0):,} channels", kind="provider"))

    for source in core.epg_sources_payload():
        error = str(source.get("last_error") or "").strip()
        name = str(source.get("name") or "EPG Source")
        stages.append(_stage(name, "error" if error else "success", error or "Guide source refreshed.", kind="epg"))

    public_epg = core.public_epg_payload()
    for country in public_epg.get("countries") or []:
        if not country.get("enabled"):
            continue
        error = str(country.get("last_error") or "").strip()
        if error:
            status = "error"
            detail = error
        elif country.get("cached"):
            status = "success"
            detail = f"{int(country.get('filtered_channels') or 0):,} filtered channels"
        else:
            status = "setup"
            detail = "Enabled; awaiting first successful refresh."
        stages.append(_stage(f"Public EPG — {country.get('name') or country.get('code')}", status, detail, kind="epg"))

    sports_status = core.enrich_sports_status(sports.status_payload(core.DB_PATH))
    settings = sports_status.get("settings") or {}
    if settings.get("enabled"):
        last_scan = sports_status.get("last_scan") or {}
        scan_status = str(last_scan.get("status") or "").lower()
        if scan_status == "failed":
            stages.append(_stage("Sports Automation", "error", str(last_scan.get("message") or "Sports update failed."), kind="sports"))
        elif scan_status == "cancelled":
            stages.append(_stage("Sports Automation", "warning", str(last_scan.get("message") or "Sports update was cancelled."), kind="sports"))
        elif scan_status:
            detail = f"{int(last_scan.get('channel_count') or 0):,} channels"
            stages.append(_stage("Sports Automation", "success", detail, kind="sports"))
        else:
            stages.append(_stage("Sports Automation", "setup", "Enabled; no completed scan yet.", kind="sports"))

        schedule_api = sports_status.get("schedule_api") or {}
        for item in schedule_api.get("apis") or []:
            status_code = str(item.get("status_code") or "")
            if status_code not in {"error", "stale", "partial"}:
                continue
            detail = str(item.get("last_error") or item.get("reference_error") or item.get("status_label") or "Schedule API issue.")
            stages.append(_stage(f"Schedule API — {item.get('scope') or item.get('id') or 'Dataset'}", "error" if status_code == "error" else "warning", detail, kind="sports"))
    else:
        stages.append(_stage("Sports Automation", "disabled", "Disabled.", kind="sports"))

    if primary:
        if core.PLAYLIST_PATH.exists():
            stages.append(_stage("M3U Publish", "success", "Curated playlist is available.", kind="output"))
        else:
            stages.append(_stage("M3U Publish", "error", "Curated playlist output is missing.", kind="output"))

        if core.COMBINED_EPG_PATH.exists():
            stages.append(_stage("Combined EPG", "success", "Combined XMLTV output is available.", kind="output"))
        else:
            stages.append(_stage("Combined EPG", "error", "Combined XMLTV output is missing.", kind="output"))

    errors = [item for item in stages if item["status"] == "error"]
    warnings = [item for item in stages if item["status"] == "warning"]
    master = core.master_update_payload()

    if master.get("running"):
        status = "running"
        label = "Update in progress"
    elif errors:
        status = "failed"
        label = "Update completed with errors"
    elif warnings:
        status = "warning"
        label = "Updated with warnings"
    elif not primary:
        status = "setup"
        label = "Setup needed"
    else:
        status = "success"
        label = "All systems updated"

    return {
        "status": status,
        "label": label,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "stages": stages,
    }


def ui_status_payload() -> dict:
    providers = core.provider_sources_payload()
    primary = next((item for item in providers if item.get("role") == "primary"), None)
    generated_count = len(sports.generated_rows(core.DB_PATH))
    saved_roku = roku_devices.list_saved(core.DB_PATH)
    provider_label = "Not configured"
    provider_state = "setup"
    if primary:
        if primary.get("last_error"):
            provider_label = "Error"
            provider_state = "error"
        else:
            provider_label = str(primary.get("account_status") or "Active")
            provider_state = "success"

    return {
        "provider": {
            "label": provider_label,
            "status": provider_state,
        },
        "counts": {
            "all_channels": len(core.channels),
            "indexed_channels": len(core.selected_ids),
            "sports_channels": generated_count,
        },
        "devices": {
            "hdhr": {
                "enabled": hdhr_config.is_enabled(),
                "tuners": HDHR_TUNER_COUNT,
            },
            "roku_saved": len(saved_roku),
            "roku_devices": saved_roku,
            "active_streams": _active_hls_sessions(),
        },
        "master_update": core.master_update_payload(),
        "update": _update_health(),
        "outputs": {
            "m3u": "/playlist/channels.m3u",
            "epg": "/epg/epg.xml",
        },
    }


def register_ui_status_routes(app):
    @app.get("/api/ui/status")
    def api_ui_status():
        return jsonify(ui_status_payload())
