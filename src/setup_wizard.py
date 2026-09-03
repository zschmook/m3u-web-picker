from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from settings import SETTINGS


STATE_VERSION = 1
DEFAULT_STATE: dict[str, Any] = {
    "version": STATE_VERSION,
    "mode": "testing",
    "features": {"dvr": False, "jellyfin": False, "sports_api": False},
    "current_step": "choices",
    "provider": {"configured": False, "name": ""},
    "channels": {"saved": False, "selected_count": 0, "hide_sd": False},
    "dvr": {
        "host_path": "C:/DVR",
        "server_path": "",
        "process_immediately": True,
        "remove_commercials": True,
        "max_concurrent_recordings": 2,
    },
    "jellyfin": {"cache_path": "", "cleanup_enabled": False, "acknowledged": False},
    "media_server": {"type": "none"},
    "sports": {"enabled": False, "selection_count": 0},
    "sports_api": {"configured": False},
    "initial_update": {
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "error": "",
    },
    "completed": False,
}

_LOCK = threading.RLock()


def state_path() -> Path:
    return Path(
        os.environ.get("M3U_SETUP_STATE_PATH", str(SETTINGS.data_dir / "setup-state.json"))
    ).expanduser()


def output_dir() -> Path:
    return Path(
        os.environ.get("M3U_SETUP_OUTPUT_DIR", str(SETTINGS.data_dir / "setup-output"))
    ).expanduser()


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif key in result:
            result[key] = value
    return result


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or state_path()
    with _LOCK:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        state = _merge(DEFAULT_STATE, payload)
        if state["mode"] == "testing":
            state["features"] = {"dvr": False, "jellyfin": False, "sports_api": False}
        return state


def save_state(changes: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or state_path()
    with _LOCK:
        state = _merge(load_state(target), changes)
        state["version"] = STATE_VERSION
        if state["mode"] == "testing":
            state["features"] = {"dvr": False, "jellyfin": False, "sports_api": False}
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(target)
        return state


def save_choices(
    mode: str,
    features: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    selected_mode = str(mode or "").strip().lower()
    if selected_mode not in {"testing", "provider"}:
        raise ValueError("Choose Just Testing or Use My Provider.")
    current = load_state(path)
    clean_features = current["features"] if selected_mode == "provider" else {
        "dvr": False,
        "jellyfin": False,
        "sports_api": False,
    }
    return save_state(
        {
            "mode": selected_mode,
            "features": clean_features,
            "media_server": (
                current["media_server"] if selected_mode == "provider" else {"type": "none"}
            ),
            "sports": (
                current["sports"]
                if selected_mode == "provider"
                else {"enabled": False, "selection_count": 0}
            ),
            "current_step": "channels" if selected_mode == "testing" else "provider",
            "initial_update": {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "error": "",
            },
            "completed": False,
        },
        path,
    )


def normalize_host_path(value: str, *, label: str) -> str:
    entered = re.sub(r"/{2,}", "/", str(value or "").strip().replace("\\", "/"))
    if entered == "/" or re.fullmatch(r"[A-Za-z]:/?", entered):
        raise ValueError(f"Choose a dedicated {label} folder, not an entire drive.")
    if entered != "/":
        entered = entered.rstrip("/")
    absolute = entered.startswith("/") or bool(re.match(r"^[A-Za-z]:/", entered))
    if not entered or not absolute:
        raise ValueError(f"Use an absolute {label} path.")
    return entered


def _env_value(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def build_preview(state: dict[str, Any], destination: Path | None = None) -> dict[str, Any]:
    target = destination or output_dir()
    target.mkdir(parents=True, exist_ok=True)
    features = state.get("features") or {}

    env_lines = [
        "M3U_HOST_PORT=9999",
        "M3U_ONBOARDING_ENABLED=false",
    ]
    mounts: list[tuple[str, str]] = []
    if features.get("dvr"):
        dvr_path = normalize_host_path(state["dvr"].get("host_path", ""), label="DVR")
        env_lines.append(f"M3U_DVR_DIR={_env_value(dvr_path)}")
        mounts.append((dvr_path, "/recordings"))
    if features.get("jellyfin"):
        cache_path = normalize_host_path(
            state["jellyfin"].get("cache_path", ""), label="Jellyfin cache"
        )
        env_lines.append(f"M3U_JELLYFIN_CACHE_DIR={_env_value(cache_path)}")
        mounts.append((cache_path, "/jellyfin-cache"))

    compose_lines = ["services:", "  m3u-picker:"]
    if mounts:
        compose_lines.append("    volumes:")
        for source, target_path in mounts:
            compose_lines.extend(
                [
                    "      - type: bind",
                    f"        source: {_env_value(source)}",
                    f"        target: {target_path}",
                ]
            )
    else:
        compose_lines.append("    # No optional host folders selected.")

    env_text = "\n".join(env_lines) + "\n"
    compose_text = "\n".join(compose_lines) + "\n"
    manifest = {
        "version": STATE_VERSION,
        "mode": state.get("mode"),
        "features": features,
        "selected_channels": int((state.get("channels") or {}).get("selected_count") or 0),
        "ready_for_handoff": True,
    }
    (target / ".env.preview").write_text(env_text, encoding="utf-8")
    (target / "compose.setup.generated.yml").write_text(compose_text, encoding="utf-8")
    (target / "setup-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {"env": env_text, "compose": compose_text, "manifest": manifest}
