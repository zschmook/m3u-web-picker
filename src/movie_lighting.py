"""Saved movie-lighting preferences; saving settings never changes a bulb."""
from __future__ import annotations

import ipaddress
import json
import socket
from concurrent.futures import ThreadPoolExecutor
import app_config


DEFAULTS = {
    "enabled": False, "brightness": 10, "paused_brightness": 100,
    "transition_ms": 2000, "target": {"ip": "", "name": "", "model": ""},
    "roku_host": "", "roku_name": "", "revision": 0,
}


def status() -> dict:
    return {**DEFAULTS, **app_config.section("movie_lighting")}


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a whole number from {minimum} to {maximum}.")
    return value


def _local_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        raise ValueError("Choose a device with a valid private network address.") from None
    if not any(address in ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Choose a device on your private network.")
    return str(address)


def _light(value):
    if not isinstance(value, dict) or set(value) != {"ip", "name", "model", "device_id"}:
        raise ValueError("Choose an identified light from the list.")
    if any(not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 100
           for field in ("name", "model", "device_id")):
        raise ValueError("The light needs a name, model, and device identity.")
    return {"ip": _local_address(value["ip"]), **{field: value[field].strip()
            for field in ("name", "model", "device_id")}}


def lights() -> list[dict]:
    return app_config.section("movie_light_devices").get("lights", [])


def remember_lights(values) -> list[dict]:
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError("Too many lights.")
    identified = [_light(value) for value in values]
    if len({value["device_id"] for value in identified}) != len(identified):
        raise ValueError("Each light must have a different device identity.")
    identified.sort(key=lambda value: (value["name"].casefold(), value["device_id"]))
    app_config.update_section("movie_light_devices", {"lights": identified})
    return identified


def _read_light(ip) -> dict | None:
    """Read identity only from a previously identified bulb; never send controls."""
    ip = _local_address(ip)
    encrypted = bytearray()
    key = 171
    for byte in b'{"system":{"get_sysinfo":{}}}':
        key ^= byte
        encrypted.append(key)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(encrypted, (ip, 9999))
            data, sender = client.recvfrom(8192)
        if sender != (ip, 9999):
            return None
        decoded = bytearray()
        key = 171
        for byte in data:
            decoded.append(key ^ byte)
            key = byte
        product = json.loads(decoded)["system"]["get_sysinfo"]
        if not isinstance(product.get("light_state"), dict):
            return None
        return _light({"ip": ip, "name": product.get("alias"), "model": product.get("model"),
                       "device_id": product.get("deviceId")})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def refresh_lights() -> dict:
    existing = lights()
    addresses = list(dict.fromkeys(value["ip"] for value in existing))
    with ThreadPoolExecutor(max_workers=4) as pool:
        observed = list(pool.map(_read_light, addresses))
    refreshed = {value["device_id"]: value for value in existing}
    for value in observed:
        if value:
            refreshed[value["device_id"]] = value
    # Keep unavailable bulbs listed. Never replace the selected movie light.
    saved = remember_lights(list(refreshed.values()))
    return {"lights": saved, "responding": sum(value is not None for value in observed)}


def save(values) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Lighting settings must be an object.")
    allowed = set(DEFAULTS) - {"revision"}
    if set(values) - allowed:
        raise ValueError("Unknown movie-lighting setting.")
    current = status()
    changes = {}
    for field, title, minimum, maximum in (
        ("brightness", "Playing brightness", 1, 100),
        ("paused_brightness", "Paused brightness", 1, 100),
        ("transition_ms", "Fade time in milliseconds", 0, 5000),
    ):
        if field in values:
            changes[field] = _integer(values[field], title, minimum, maximum)
    if "enabled" in values:
        if not isinstance(values["enabled"], bool):
            raise ValueError("Movie lighting must be enabled or disabled.")
        changes["enabled"] = values["enabled"]
    if "target" in values:
        target = _light(values["target"])
        if target not in lights():
            raise ValueError("Choose a light from the identified devices.")
        changes["target"] = target
    if "roku_host" in values:
        changes["roku_host"] = _local_address(values["roku_host"])
    if "roku_name" in values:
        if not isinstance(values["roku_name"], str):
            raise ValueError("Roku name must be text.")
        changes["roku_name"] = values["roku_name"].strip()[:100]
    merged = {**current, **changes}
    if merged["enabled"] and (not merged["roku_host"] or not all(merged["target"].get(field) for field in ("ip", "name", "model"))):
        raise ValueError("Select a Roku and light before enabling movie lighting.")
    changes["revision"] = int(current.get("revision", 0)) + 1
    app_config.update_section("movie_lighting", changes)
    return status()


def for_roku(address: str) -> dict:
    settings = status()
    if address != settings["roku_host"]:
        # Other receivers never inherit this room's light-control settings.
        return {"enabled": False, "revision": settings["revision"]}
    return settings
