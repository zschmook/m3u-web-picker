from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def normalize_host(value: str) -> str:
    host = str(value or "").strip()
    if not host:
        raise ValueError("Enter the Roku TV IP address.")
    if "://" in host or "/" in host or "?" in host or "#" in host or ":" in host:
        raise ValueError("Enter only the Roku TV IP address or hostname, without http:// or a port.")
    if not _HOST_RE.fullmatch(host):
        raise ValueError("Roku TV address contains invalid characters.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version != 4:
        raise ValueError("This Roku experiment currently expects an IPv4 LAN address.")
    if not (address.is_private or address.is_link_local):
        raise ValueError("Roku TV must use a private/local IPv4 address.")
    return host


def _request(host: str, path: str, *, method: str = "GET", timeout: float = 4.0) -> bytes:
    normalized = normalize_host(host)
    url = f"http://{normalized}:8060{path}"
    data = b"" if method.upper() == "POST" else None
    request = urllib.request.Request(url, data=data, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Roku returned HTTP {exc.code} for {path}.") from exc
    except urllib.error.URLError as exc:
        detail = getattr(exc, "reason", exc)
        raise RuntimeError(f"Could not reach Roku at {normalized}:8060 ({detail}).") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Timed out reaching Roku at {normalized}:8060.") from exc


def _parse_device_info(payload: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("Roku answered, but device-info XML could not be parsed.") from exc
    if root.tag != "device-info":
        raise RuntimeError("Port 8060 answered, but it was not a Roku ECP device.")

    def text(name: str) -> str:
        node = root.find(name)
        return (node.text or "").strip() if node is not None else ""

    model = text("model-name")
    serial = text("serial-number")
    device_id = text("device-id")
    if not (model or serial or device_id):
        raise RuntimeError("Port 8060 answered, but Roku device identity was missing.")

    friendly = text("user-device-name") or text("friendly-device-name") or model or "Roku"
    return {
        "name": friendly,
        "model": model,
        "model_number": text("model-number"),
        "serial_number": serial,
        "software_version": text("software-version"),
    }


def device_info(host: str, *, timeout: float = 3.0) -> dict[str, str]:
    payload = _request(host, "/query/device-info", timeout=timeout)
    return _parse_device_info(payload)


def _roku_port_open(host: str, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, 8060), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def discover_devices(
    lan_host: str,
    *,
    connect_timeout: float = 0.15,
    request_timeout: float = 0.8,
    max_workers: int = 64,
) -> list[dict[str, str]]:
    """Discover and verify Roku ECP devices on the LAN host's /24 subnet."""
    try:
        address = ipaddress.ip_address(str(lan_host or "").strip())
    except ValueError as exc:
        raise ValueError("Automatic Roku discovery needs the local LAN IPv4 address.") from exc
    if address.version != 4 or not str(address).startswith("10."):
        raise ValueError("Automatic Roku discovery currently expects a 10.x.x.x LAN address.")

    network = ipaddress.ip_network(f"{address}/24", strict=False)
    hosts = [str(candidate) for candidate in network.hosts()]

    def probe(host: str) -> dict[str, str] | None:
        if not _roku_port_open(host, timeout=connect_timeout):
            return None
        try:
            info = device_info(host, timeout=request_timeout)
        except (ValueError, RuntimeError):
            return None
        return {"host": host, **info}

    devices: list[dict[str, str]] = []
    workers = max(1, min(int(max_workers), len(hosts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(probe, hosts):
            if result:
                devices.append(result)

    devices.sort(key=lambda item: ipaddress.ip_address(item["host"]))
    return devices


def launch_dev(host: str, media_url: str) -> None:
    value = str(media_url or "").strip()
    if not value.startswith(("http://", "https://")):
        raise ValueError("Roku media URL must be HTTP or HTTPS.")
    query = urllib.parse.urlencode({"contentId": value, "mediaType": "tvSpecial"})
    _request(host, f"/launch/dev?{query}", method="POST", timeout=5.0)


def send_home(host: str) -> None:
    _request(host, "/keypress/Home", method="POST", timeout=3.0)
