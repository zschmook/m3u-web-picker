from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("M3U_DISABLE_SCHEDULER", "true")
os.environ.setdefault("M3U_ONBOARDING_ENABLED", "false")

from flask import Flask, jsonify, render_template, request

import core
import app_config
import dvr
import jellyfin_cache
import master_update_worker
import sports
import setup_wizard


REPO_DIR = Path(__file__).resolve().parent.parent
TESTING_PLAYLIST = "https://iptv-org.github.io/iptv/countries/us.m3u"

app = Flask(
    __name__,
    static_folder=str(REPO_DIR / "static"),
    template_folder=str(REPO_DIR / "templates"),
)


@app.after_request
def no_setup_cache(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _provider_configured() -> bool:
    return bool(core.primary_provider_source() or core.source_mode == "file")


def _payload() -> dict:
    state = setup_wizard.load_state()
    return {
        "state": state,
        "provider_configured": _provider_configured(),
        "channel_count": len(core.channels),
        "selected_count": len(core.selected_ids),
        "sports": {
            "settings": sports.get_settings(core.DB_PATH),
            "rules": sports.get_rules(core.DB_PATH),
            "schedule_api": sports.schedule_api_status_payload(core.DB_PATH),
        },
        "test_mode": os.environ.get("M3U_SETUP_TEST_MODE", "").lower() in {"1", "true", "yes"},
    }


def _json() -> dict:
    return request.get_json(force=True, silent=True) or {}


def _apply_full_app_configuration(state: dict) -> None:
    """Commit wizard choices to the isolated app's real runtime settings."""
    app_config.update_section("network", {"external_port": 9998})

    dvr_enabled = bool(state["features"].get("dvr"))
    if dvr_enabled:
        dvr_values = state["dvr"]
        os.environ["M3U_DVR_HOST_DIR"] = str(dvr_values["host_path"])
        dvr.save_settings(
            {
                "enabled": True,
                "host_path": dvr_values["host_path"],
                "plex_path": dvr_values.get("server_path", ""),
                "max_concurrent_recordings": dvr_values["max_concurrent_recordings"],
                "transcode_hevc": True,
                "remove_commercials": dvr_values["remove_commercials"],
                "processing_policy": (
                    "immediate" if dvr_values["process_immediately"] else "scheduled"
                ),
            }
        )
    else:
        os.environ.pop("M3U_DVR_HOST_DIR", None)
        dvr.save_settings({"enabled": False})

    jellyfin_enabled = bool(state["features"].get("jellyfin"))
    if jellyfin_enabled:
        jellyfin_values = state["jellyfin"]
        os.environ["M3U_JELLYFIN_CACHE_HOST_DIR"] = str(jellyfin_values["cache_path"])
        jellyfin_cache.update_settings(
            core.DB_PATH,
            using_jellyfin=True,
            cleanup_enabled=jellyfin_values["cleanup_enabled"],
            acknowledged=jellyfin_values["acknowledged"],
            host_path=jellyfin_values["cache_path"],
        )
    else:
        os.environ.pop("M3U_JELLYFIN_CACHE_HOST_DIR", None)
        jellyfin_cache.update_settings(
            core.DB_PATH,
            using_jellyfin=False,
            cleanup_enabled=False,
            acknowledged=False,
            host_path="",
        )

    if state.get("mode") == "testing":
        sports.update_settings(core.DB_PATH, {"enabled": False})
        sports.update_schedule_api_config(core.DB_PATH, enabled=False, clear_key=True)
    elif not state["features"].get("sports_api"):
        sports.update_schedule_api_config(core.DB_PATH, enabled=False, clear_key=True)


@app.get("/")
def index():
    return render_template("setup_wizard.html")


@app.get("/api/setup/state")
def api_setup_state():
    return jsonify(_payload())


@app.post("/api/setup/choices")
def api_setup_choices():
    data = _json()
    try:
        state = setup_wizard.save_choices(
            data.get("mode", ""),
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(state=state)


@app.post("/api/setup/provider")
def api_setup_provider():
    if _provider_configured():
        return jsonify(error="A provider is already configured in this setup workspace."), 409
    data = _json()
    state = setup_wizard.load_state()
    testing = state.get("mode") == "testing"
    url = TESTING_PLAYLIST if testing else str(data.get("url", "") or "").strip()
    name = "Free U.S. Testing Channels" if testing else str(data.get("name", "") or "Primary").strip()
    username = "" if testing else str(data.get("username", "") or "")
    password = "" if testing else str(data.get("password", "") or "")
    if not url.startswith(("http://", "https://")):
        return jsonify(error="Enter a provider URL beginning with http:// or https://."), 400
    if bool(username) != bool(password):
        return jsonify(error="Enter both the Xtream username and password."), 400
    try:
        source, text, parsed = core.detect_provider_source(
            name, url, username=username, password=password, role="primary"
        )
        core.install_primary_provider(source, text, parsed)
    except ValueError as exc:
        return jsonify(error=core.redact_url_credentials(str(exc))), 400
    except Exception as exc:
        return jsonify(error=core.redact_url_credentials(str(exc))), 502
    saved = setup_wizard.save_state(
        {
            "current_step": "channels",
            "provider": {"configured": True, "name": name},
        }
    )
    return jsonify(state=saved, channel_count=len(core.channels))


@app.get("/api/setup/channels")
def api_setup_channels():
    query = str(request.args.get("q", "") or "").strip().casefold()
    group = str(request.args.get("group", "") or "").strip()
    hide_sd = str(request.args.get("hide_sd", "") or "").lower() in {"1", "true", "yes"}
    channels = core.combined_channels_for_api()
    if hide_sd:
        channels = [
            item for item in channels
            if str(item.get("group", "") or "").strip().upper() != "LOW BANDWIDTH"
        ]
    groups = sorted({str(item.get("group", "") or "") for item in channels if item.get("group")})
    if group:
        channels = [item for item in channels if str(item.get("group", "") or "") == group]
    if query:
        channels = [
            item for item in channels
            if query in f"{item.get('name', '')} {item.get('group', '')} {item.get('tvg_id', '')}".casefold()
        ]
    return jsonify(
        channels=channels[:500],
        total=len(channels),
        groups=groups,
        selected_ids=core.selected_ids_payload(),
    )


@app.post("/api/setup/channels")
def api_save_setup_channels():
    data = _json()
    values = data.get("ids") if isinstance(data.get("ids"), list) else []
    hide_sd = bool(data.get("hide_sd"))
    valid = {
        int(channel["id"])
        for channel in core.channels
        if not hide_sd
        or str(channel.get("group", "") or "").strip().upper() != "LOW BANDWIDTH"
    }
    core.selected_ids = {
        int(value) for value in values
        if str(value).isdigit() and int(value) in valid
    }
    if not core.selected_ids:
        return jsonify(error="Choose at least one channel."), 400
    sports.update_settings(core.DB_PATH, {"exclude_sd": hide_sd})
    core.write_current_playlist()
    core.save_config()
    state = setup_wizard.load_state()
    next_step = "build" if state.get("mode") == "testing" else "sports"
    state = setup_wizard.save_state(
        {
            "current_step": next_step,
            "channels": {
                "saved": True,
                "selected_count": len(core.selected_ids),
                "hide_sd": hide_sd,
            },
        }
    )
    return jsonify(state=state, selected_count=len(core.selected_ids))


@app.post("/api/setup/dvr")
def api_setup_dvr():
    state = setup_wizard.load_state()
    if state.get("mode") != "provider":
        return jsonify(error="DVR is unavailable in Just Testing mode."), 409
    data = _json()
    enabled = bool(data.get("enabled"))
    if not enabled:
        media_server = state.get("media_server") or {"type": "none"}
        if media_server.get("type") == "plex":
            media_server = {"type": "none"}
        saved = setup_wizard.save_state(
            {
                "features": {**state["features"], "dvr": False},
                "dvr": {**state["dvr"], "server_path": ""},
                "media_server": media_server,
                "current_step": "media",
            }
        )
        return jsonify(state=saved)
    try:
        host_path = setup_wizard.normalize_host_path(data.get("host_path", ""), label="DVR")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    saved = setup_wizard.save_state(
        {
            "features": {**state["features"], "dvr": True},
            "current_step": "media",
            "dvr": {
                **state["dvr"],
                "host_path": host_path,
                "server_path": "",
                "process_immediately": bool(data.get("process_immediately", True)),
                "remove_commercials": bool(data.get("remove_commercials", True)),
                "max_concurrent_recordings": max(1, min(8, int(data.get("max_concurrent_recordings", 2)))),
            },
        }
    )
    return jsonify(state=saved)


@app.post("/api/setup/jellyfin")
def api_setup_jellyfin():
    state = setup_wizard.load_state()
    if state.get("mode") != "provider" or not state["features"].get("jellyfin"):
        return jsonify(error="Jellyfin was not selected in the Features step."), 409
    data = _json()
    cleanup = bool(data.get("cleanup_enabled"))
    acknowledged = bool(data.get("acknowledged"))
    if cleanup and not acknowledged:
        return jsonify(error="Acknowledge the cache cleanup warning before enabling cleanup."), 400
    try:
        cache_path = setup_wizard.normalize_host_path(data.get("cache_path", ""), label="Jellyfin cache")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    saved = setup_wizard.save_state(
        {"jellyfin": {
            "cache_path": cache_path,
            "cleanup_enabled": cleanup,
            "acknowledged": acknowledged,
        }}
    )
    return jsonify(state=saved)


@app.get("/api/setup/sports/catalog")
def api_setup_sports_catalog():
    query = str(request.args.get("q", "") or "")
    items = sports.catalog_payload(core.DB_PATH, query=query)
    return jsonify(items=items[:300], total=len(items))


@app.post("/api/setup/sports")
def api_setup_sports():
    state = setup_wizard.load_state()
    if state.get("mode") != "provider":
        return jsonify(error="Sports Automation is unavailable in Just Testing mode."), 409
    data = _json()
    enabled = bool(data.get("enabled"))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if enabled and not items:
        return jsonify(error="Choose at least one team or league, or disable Sports Automation."), 400
    try:
        current = sports.get_rules(core.DB_PATH)
        selected = {(str(item.get("scope_type")), str(item.get("scope_id"))) for item in items}
        for rule in current:
            if (str(rule["scope_type"]), str(rule["scope_id"])) not in selected:
                sports.delete_rule(core.DB_PATH, int(rule["id"]))
        rules = sports.add_rules(core.DB_PATH, items) if enabled else sports.get_rules(core.DB_PATH)
        sports.update_settings(core.DB_PATH, {"enabled": enabled})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    saved = setup_wizard.save_state(
        {
            "current_step": "api",
            "sports": {"enabled": enabled, "selection_count": len(rules) if enabled else 0},
        }
    )
    return jsonify(state=saved, rules=rules)


@app.post("/api/setup/sports-api")
def api_setup_sports_api():
    state = setup_wizard.load_state()
    if state.get("mode") != "provider":
        return jsonify(error="Sports API is unavailable in Just Testing mode."), 409
    data = _json()
    enabled = bool(data.get("enabled"))
    if not enabled:
        sports.update_schedule_api_config(core.DB_PATH, enabled=False, clear_key=True)
        saved = setup_wizard.save_state(
            {
                "features": {**state["features"], "sports_api": False},
                "sports_api": {"configured": False},
                "current_step": "dvr",
            }
        )
        return jsonify(state=saved, configured=False)
    api_key = str(data.get("api_key", "") or "").strip()
    if not api_key:
        return jsonify(error="Enter the API-SPORTS key."), 400
    try:
        sports.update_schedule_api_config(core.DB_PATH, enabled=True, api_key=api_key)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    saved = setup_wizard.save_state(
        {
            "features": {**state["features"], "sports_api": True},
            "sports_api": {"configured": True},
            "current_step": "dvr",
        }
    )
    return jsonify(state=saved, configured=True)


@app.post("/api/setup/media-server")
def api_setup_media_server():
    state = setup_wizard.load_state()
    if state.get("mode") != "provider":
        return jsonify(error="Media-server integration is unavailable in Just Testing mode."), 409
    data = _json()
    server_type = str(data.get("type", "none") or "none").strip().lower()
    if server_type not in {"none", "jellyfin", "plex"}:
        return jsonify(error="Choose No media server, Jellyfin, or Plex."), 400

    dvr_values = {**state["dvr"], "server_path": ""}
    jellyfin_values = {
        **state["jellyfin"],
        "cleanup_enabled": False,
        "acknowledged": False,
    }
    features = {**state["features"], "jellyfin": server_type == "jellyfin"}

    if server_type == "plex":
        if not state["features"].get("dvr"):
            return jsonify(error="Enable DVR before selecting Plex recording export."), 400
        try:
            dvr_values["server_path"] = setup_wizard.normalize_host_path(
                data.get("plex_path", ""), label="Plex library"
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    elif server_type == "jellyfin":
        cleanup = bool(data.get("cleanup_enabled"))
        acknowledged = bool(data.get("acknowledged"))
        if cleanup and not acknowledged:
            return jsonify(error="Acknowledge the cache cleanup warning before enabling cleanup."), 400
        try:
            cache_path = setup_wizard.normalize_host_path(
                data.get("cache_path", ""), label="Jellyfin cache"
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        jellyfin_values = {
            "cache_path": cache_path,
            "cleanup_enabled": cleanup,
            "acknowledged": acknowledged,
        }

    saved = setup_wizard.save_state(
        {
            "features": features,
            "dvr": dvr_values,
            "jellyfin": jellyfin_values,
            "media_server": {"type": server_type},
            "current_step": "build",
        }
    )
    return jsonify(state=saved)


@app.post("/api/setup/build")
def api_setup_build():
    state = setup_wizard.load_state()
    if not state["channels"].get("saved"):
        return jsonify(error="Save at least one channel before building the configuration."), 409
    try:
        preview = setup_wizard.build_preview(state)
        _apply_full_app_configuration(state)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    update = master_update_worker.payload()
    if update.get("running"):
        return jsonify(
            error="An application update is already running. Let it finish, then try Build again.",
            master_update=update,
        ), 409
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    state = setup_wizard.save_state(
        {
            "completed": False,
            "current_step": "build",
            "initial_update": {
                "status": "starting",
                "started_at": started_at,
                "completed_at": None,
                "error": "",
            },
        }
    )
    try:
        started, update = master_update_worker.start(trigger="setup")
    except Exception as exc:
        message = core.redact_url_credentials(str(exc))
        state = setup_wizard.save_state(
            {"initial_update": {"status": "failed", "error": message}}
        )
        return jsonify(error=message, state=state), 500
    if not started:
        state = setup_wizard.save_state(
            {
                "initial_update": {
                    "status": "failed",
                    "error": "The initial guide update could not be started.",
                }
            }
        )
        return jsonify(error=state["initial_update"]["error"], state=state), 409
    return jsonify(
        state=state,
        preview=preview,
        test_mode=_payload()["test_mode"],
        master_update=update,
        launch_url="/",
    )


@app.get("/api/setup/build-status")
def api_setup_build_status():
    state = setup_wizard.load_state()
    update = master_update_worker.payload()
    initial = state.get("initial_update") or {}
    if initial.get("status") in {"starting", "running"}:
        if update.get("running"):
            if initial.get("status") != "running":
                state = setup_wizard.save_state(
                    {"initial_update": {"status": "running"}}
                )
        else:
            completed_trigger = (
                update.get("last_completed_trigger") or update.get("last_trigger")
            )
            completed_at = update.get("last_completed_at") or update.get("last_update")
            started_at = initial.get("started_at")
            completed_after_start = False
            try:
                completed_after_start = bool(
                    completed_at
                    and started_at
                    and datetime.fromisoformat(str(completed_at))
                    >= datetime.fromisoformat(str(started_at))
                )
            except (TypeError, ValueError):
                completed_after_start = False

            if completed_trigger == "setup" and completed_after_start:
                error = str(update.get("last_error") or "").strip()
                if error:
                    state = setup_wizard.save_state(
                        {
                            "completed": False,
                            "current_step": "build",
                            "initial_update": {
                                "status": "failed",
                                "completed_at": completed_at,
                                "error": error,
                            },
                        }
                    )
                else:
                    state = setup_wizard.save_state(
                        {
                            "completed": True,
                            "current_step": "complete",
                            "initial_update": {
                                "status": "complete",
                                "completed_at": completed_at,
                                "error": "",
                            },
                        }
                    )
            else:
                state = setup_wizard.save_state(
                    {
                        "completed": False,
                        "current_step": "build",
                        "initial_update": {
                            "status": "failed",
                            "completed_at": completed_at,
                            "error": "The initial guide update was interrupted. Run Build again to retry it.",
                        },
                    }
                )
    return jsonify(state=state, master_update=update, launch_url="/")


@app.post("/api/setup/reset")
def api_setup_reset():
    """Reset only the isolated setup workspace after an explicit UI action."""
    update = master_update_worker.payload()
    if update.get("running"):
        return jsonify(
            error="The first guide update is still running. Wait for it to finish before starting over.",
            master_update=update,
        ), 409
    if _provider_configured():
        core.remove_primary_source()
    core.selected_ids.clear()
    core.save_config()
    core.write_current_playlist()
    for rule in sports.get_rules(core.DB_PATH):
        sports.delete_rule(core.DB_PATH, int(rule["id"]))
    sports.update_settings(core.DB_PATH, {"enabled": False})
    sports.update_schedule_api_config(core.DB_PATH, enabled=False, clear_key=True)
    setup_wizard.state_path().unlink(missing_ok=True)
    for name in (".env.preview", "compose.setup.generated.yml", "setup-manifest.json"):
        (setup_wizard.output_dir() / name).unlink(missing_ok=True)
    return jsonify(reset=True, **_payload())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9998, debug=True)
