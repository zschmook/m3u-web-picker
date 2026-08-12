from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from settings import SETTINGS


HDHR_CONFIG_PATH = Path(SETTINGS.data_dir) / "hdhr.json"
_LOCK = threading.RLock()


def is_enabled() -> bool:
    """Return the persisted HDHomeRun support state.

    The experiments branch already advertised the HDHomeRun facade before this
    toggle existed, so missing state intentionally preserves that behavior and
    defaults to enabled. Once the user changes the switch, the explicit value is
    persisted across rebuilds.
    """
    with _LOCK:
        if not HDHR_CONFIG_PATH.exists():
            return True
        try:
            payload = json.loads(HDHR_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return True
        if not isinstance(payload, dict):
            return True
        return bool(payload.get("enabled", True))


def set_enabled(enabled: bool) -> bool:
    value = bool(enabled)
    with _LOCK:
        HDHR_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = HDHR_CONFIG_PATH.with_name(f".{HDHR_CONFIG_PATH.name}.tmp")
        temporary.write_text(
            json.dumps({"enabled": value}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, HDHR_CONFIG_PATH)
        try:
            HDHR_CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
    return value
