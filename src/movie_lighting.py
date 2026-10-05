"""Saved movie-lighting preferences; saving settings never changes a bulb."""
from __future__ import annotations

import ipaddress
import json
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import app_config
from settings import load_settings


DEFAULTS = {
    "enabled": False, "brightness": 10, "paused_brightness": 100,
    "transition_ms": 2000, "target": {"ip": "", "name": "", "model": ""},
    "roku_host": "", "roku_name": "", "revision": 0,
}
_LIGHT_LOCK = threading.RLock()


def status() -> dict:
    stored = app_config.section("movie_lighting")
    rooms = _rooms(stored)
    first = rooms[0] if rooms else DEFAULTS
    return {**DEFAULTS, **{key: first[key] for key in DEFAULTS if key in first},
            "target": (first.get("targets") or [DEFAULTS["target"]])[0],
            "revision": stored.get("revision", 0), "rooms": rooms}


def _rooms(stored):
    if "rooms" in stored:
        return stored["rooms"]
    # Reading old settings is non-mutating. The first room save persists migration.
    if stored.get("roku_host") or stored.get("target", {}).get("device_id"):
        old = {**DEFAULTS, **stored}
        return [{key: old[key] for key in DEFAULTS if key != "target"} | {
            "id": "legacy", "name": "Room 1",
            "targets": [old["target"]] if old["target"].get("device_id") else [],
        }]
    return []


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
    with _LIGHT_LOCK:
        app_config.update_section("movie_light_devices", {"lights": identified})
    return identified


def _read_light(ip, *, timeout=2) -> dict | None:
    """Read bulb identity and metadata; never send controls."""
    ip = _local_address(ip)
    encrypted = bytearray()
    key = 171
    for byte in b'{"system":{"get_sysinfo":{}}}':
        key ^= byte
        encrypted.append(key)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(timeout)
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


def _remember_observed_lights(observed) -> dict:
    with _LIGHT_LOCK:
        refreshed = {value["device_id"]: value for value in lights()}
        for value in observed:
            if value:
                refreshed[value["device_id"]] = value
        # Keep unavailable bulbs listed. Room assignments are saved separately.
        saved = remember_lights(list(refreshed.values()))
    return {"lights": saved, "responding": sum(value is not None for value in observed)}


def refresh_lights() -> dict:
    addresses = list(dict.fromkeys(value["ip"] for value in lights()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        observed = list(pool.map(_read_light, addresses))
    return _remember_observed_lights(observed)


def discover_lights() -> dict:
    """Find compatible Kasa bulbs on the configured LAN using identity queries."""
    try:
        address = _local_address(load_settings().lan_host)
    except ValueError:
        raise ValueError("Light discovery needs the server's LAN address (M3U_LAN_HOST).") from None
    network = ipaddress.IPv4Network(f"{address}/24", strict=False)
    addresses = {str(host) for host in network.hosts() if str(host) != address}
    addresses.update(value["ip"] for value in lights())
    # Unicast reaches LAN devices from Docker even when broadcast is unavailable.
    with ThreadPoolExecutor(max_workers=64) as pool:
        observed = list(pool.map(lambda ip: _read_light(ip, timeout=.75), sorted(addresses)))
    result = _remember_observed_lights(observed)
    result["subnet"] = str(network)
    return result


def _validate_room(values, current):
    if not isinstance(values, dict):
        raise ValueError("Lighting settings must be an object.")
    allowed = (set(DEFAULTS) - {"revision", "target"}) | {"name", "targets"}
    if set(values) - allowed:
        raise ValueError("Unknown movie-lighting setting.")
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
    if "name" in values:
        if not isinstance(values["name"], str) or not 1 <= len(values["name"].strip()) <= 80:
            raise ValueError("Room name must be from 1 to 80 characters.")
        changes["name"] = values["name"].strip()
    if "targets" in values:
        if not isinstance(values["targets"], list) or len(values["targets"]) > 32:
            raise ValueError("Choose up to 32 lights for this room.")
        targets = [_light(value) for value in values["targets"]]
        if len({value["device_id"] for value in targets}) != len(targets):
            raise ValueError("Each light can only be selected once.")
        if any(target not in lights() for target in targets):
            raise ValueError("Choose lights from the identified devices.")
        changes["targets"] = targets
    if "roku_host" in values:
        changes["roku_host"] = "" if values["roku_host"] == "" else _local_address(values["roku_host"])
    if "roku_name" in values:
        if not isinstance(values["roku_name"], str):
            raise ValueError("Roku name must be text.")
        changes["roku_name"] = values["roku_name"].strip()[:100]
    merged = {**current, **changes}
    if merged["enabled"] and (not merged["roku_host"] or not merged["targets"]):
        raise ValueError("Select a Roku and at least one light before enabling this room.")
    return merged


def save_room(values, room_id=None) -> dict:
    with _LIGHT_LOCK:
        stored = app_config.section("movie_lighting")
        rooms = _rooms(stored)
        current = next((room for room in rooms if room["id"] == room_id), None)
        if room_id and current is None:
            raise ValueError("Room no longer exists. Reload the settings.")
        if current is None:
            if len(rooms) >= 32:
                raise ValueError("You can save up to 32 rooms.")
            current = {key: value for key, value in DEFAULTS.items() if key != "target"} | {
                "id": uuid.uuid4().hex, "name": f"Room {len(rooms) + 1}", "targets": []}
        merged = _validate_room(values, current)
        for room in rooms:
            if room["id"] == current["id"]:
                continue
            if room["name"].casefold() == merged["name"].casefold():
                raise ValueError("Choose a different room name.")
            if merged["roku_host"] and room["roku_host"] == merged["roku_host"]:
                raise ValueError(f"That Roku is already assigned to {room['name']}.")
            used = {target["device_id"] for target in room["targets"]}
            if any(target["device_id"] in used for target in merged["targets"]):
                raise ValueError(f"A selected light is already assigned to {room['name']}.")
        revision = int(stored.get("revision", 0)) + 1
        merged["revision"] = revision
        rooms = [merged if room["id"] == merged["id"] else room for room in rooms]
        if room_id is None:
            rooms.append(merged)
        app_config.update_section("movie_lighting", {"rooms": rooms, "revision": revision})
        return merged


def delete_room(room_id) -> dict:
    with _LIGHT_LOCK:
        stored = app_config.section("movie_lighting")
        rooms = _rooms(stored)
        remaining = [room for room in rooms if room["id"] != room_id]
        if len(remaining) == len(rooms):
            raise ValueError("Room no longer exists. Reload the settings.")
        app_config.update_section("movie_lighting", {"rooms": remaining,
            "revision": int(stored.get("revision", 0)) + 1})
        return status()


def save(values) -> dict:
    """Keep the earlier single-room API usable without dropping other rooms."""
    if not isinstance(values, dict):
        raise ValueError("Lighting settings must be an object.")
    if set(values) - (set(DEFAULTS) - {"revision"}):
        raise ValueError("Unknown movie-lighting setting.")
    values = dict(values)
    if "target" in values:
        values["targets"] = [values.pop("target")]
    with _LIGHT_LOCK:
        rooms = status()["rooms"]
        save_room(values, rooms[0]["id"] if rooms else None)
        return status()


def for_roku(address: str) -> dict:
    settings = status()
    room = next((room for room in settings["rooms"] if room["roku_host"] == address), None)
    if room is None:
        return {"enabled": False, "revision": settings["revision"]}
    # Resolve lights by identity after discovery updates their names or addresses.
    known = {light["device_id"]: light for light in lights()}
    targets = [known.get(light["device_id"], light) for light in room["targets"]]
    # Older Roku app versions keep working with the first light until upgraded.
    return {**room, "targets": targets, "target": (targets or [DEFAULTS["target"]])[0]}
