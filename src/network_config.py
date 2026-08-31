from __future__ import annotations

import os

import app_config
from settings import load_settings


def status() -> dict:
    current = load_settings()
    return {
        "lan_host": current.lan_host,
        "external_port": current.external_port,
        "environment_port": int(os.environ.get("M3U_EXTERNAL_PORT", "9999")),
    }


def save(values: dict) -> dict:
    try:
        external_port = int(values.get("external_port"))
    except (TypeError, ValueError):
        raise ValueError("Public URL port must be a number from 1 to 65535.")
    if not 1 <= external_port <= 65535:
        raise ValueError("Public URL port must be a number from 1 to 65535.")
    app_config.update_section("network", {"external_port": external_port})
    return status()
