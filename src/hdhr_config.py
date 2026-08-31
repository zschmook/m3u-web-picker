from __future__ import annotations

import threading
from pathlib import Path

import app_config
from settings import SETTINGS


HDHR_CONFIG_PATH = app_config.CONFIG_PATH
LEGACY_HDHR_CONFIG_PATH = Path(SETTINGS.data_dir) / "hdhr.json"
_LOCK = threading.RLock()


def is_enabled() -> bool:
    """Return the persisted HDHomeRun support state.

    HDHomeRun support existed before this toggle was persisted, so missing state
    intentionally preserves that behavior and defaults to enabled. Once the user
    changes the switch, the explicit value is persisted across rebuilds.
    """
    with _LOCK:
        settings = app_config.section("hdhr", path=HDHR_CONFIG_PATH)
        if "enabled" in settings:
            return bool(settings["enabled"])

        # One-time compatibility migration from the original dedicated file.
        if HDHR_CONFIG_PATH == app_config.CONFIG_PATH and LEGACY_HDHR_CONFIG_PATH.exists():
            legacy = app_config.load(LEGACY_HDHR_CONFIG_PATH)
            if "enabled" in legacy:
                return set_enabled(bool(legacy["enabled"]))
        return True


def set_enabled(enabled: bool) -> bool:
    value = bool(enabled)
    with _LOCK:
        app_config.update_section("hdhr", {"enabled": value}, path=HDHR_CONFIG_PATH)
    return value
