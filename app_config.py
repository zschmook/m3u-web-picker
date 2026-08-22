from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from settings import SETTINGS


CONFIG_PATH = Path(SETTINGS.data_dir) / "config.json"
_LOCK = threading.RLock()


def load(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path or CONFIG_PATH)
    with _LOCK:
        if not target.exists():
            return {}
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {}
    return payload if isinstance(payload, dict) else {}


def _write(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    try:
        target.chmod(0o600)
    except OSError:
        # Windows-backed Docker volumes may not implement POSIX modes.
        pass


def update(values: Mapping[str, Any], *, path: Path | str | None = None) -> dict[str, Any]:
    """Atomically merge top-level values without deleting other settings sections."""
    target = Path(path or CONFIG_PATH)
    with _LOCK:
        payload = load(target)
        payload.update(dict(values))
        _write(target, payload)
        return dict(payload)


def section(name: str, *, path: Path | str | None = None) -> dict[str, Any]:
    value = load(path).get(str(name), {})
    return dict(value) if isinstance(value, dict) else {}


def update_section(
    name: str,
    values: Mapping[str, Any],
    *,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically merge one named settings section and preserve the document."""
    target = Path(path or CONFIG_PATH)
    with _LOCK:
        payload = load(target)
        current = payload.get(str(name), {})
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(dict(values))
        payload[str(name)] = merged
        _write(target, payload)
        return dict(merged)
