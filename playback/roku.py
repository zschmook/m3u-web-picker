from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_SSDP_TARGET = ("239.255.255.250", 1900)
_SSDP_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: roku:ecp\r\n"
    "MX: 1\r\n"
    "\r\n"
).encode("ascii")


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
    stable_id = device_id or serial
    return {
        "name": friendly,
        "model": model,
        "model_number": text("model-number"),
        "serial_number": serial,
        "device_id": device_id,
        "device_key": stable_id,
        "software_version": text("software-version"),
    }


def device_info(host: str, *, timeout: float = 3.0) -> dict[str, str]:
    payload = _request(host, "/query/device-info", timeout=timeout)
    info = _parse_device_info(payload)
    if not info.get("device_key"):
        info["device_key"] = normalize_host(host)
    return info


def _parse_ssdp_response(payload: bytes) -> dict[str, str]:
    text = payload.decode("iso-8859-1", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or "200" not in lines[0]:
        return {}
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    if headers.get("st", "").lower() != "roku:ecp":
        return {}
    return headers


def _host_from_location(location: str) -> str:
    parsed = urllib.parse.urlparse(str(location or ""))
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        raise ValueError("Roku SSDP response had an invalid ECP location.")
    if parsed.port not in {None, 8060}:
        raise ValueError("Roku SSDP response used an unexpected ECP port.")
    return normalize_host(parsed.hostname)


def _discover_ssdp(lan_host: str, *, timeout: float = 1.25) -> list[dict[str, str]]:
    local = ipaddress.ip_address(str(lan_host or "").strip())
    if local.version != 4 or not (local.is_private or local.is_link_local):
        raise ValueError("Automatic Roku discovery needs a private/local LAN IPv4 address.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(str(local)))
        sock.settimeout(0.2)
        sock.bind((str(local), 0))
        sock.sendto(_SSDP_REQUEST, _SSDP_TARGET)

        deadline = time.monotonic() + max(0.2, float(timeout))
        candidates: dict[str, dict[str, str]] = {}
        while time.monotonic() < deadline:
            try:
                payload, _remote = sock.recvfrom(8192)
            except socket.timeout:
                continue
            headers = _parse_ssdp_response(payload)
            location = headers.get("location", "")
            if not location:
                continue
            try:
                host = _host_from_location(location)
            except ValueError:
                continue
            try:
                info = device_info(host, timeout=0.8)
            except (ValueError, RuntimeError):
                continue
            usn = headers.get("usn", "")
            key = str(info.get("device_key") or usn or host)
            info["device_key"] = key
            candidates[key] = {
                "host": host,
                "location": location,
                "usn": usn,
                "discovery": "ssdp",
                **info,
            }
        return list(candidates.values())
    finally:
        sock.close()


def _roku_port_open(host: str, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, 8060), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _discover_scan(
    lan_host: str,
    *,
    connect_timeout: float = 0.12,
    request_timeout: float = 0.7,
    max_workers: int = 64,
) -> list[dict[str, str]]:
    address = ipaddress.ip_address(str(lan_host or "").strip())
    if address.version != 4 or not (address.is_private or address.is_link_local):
        raise ValueError("Automatic Roku discovery needs a private/local LAN IPv4 address.")

    network = ipaddress.ip_network(f"{address}/24", strict=False)
    hosts = [str(candidate) for candidate in network.hosts()]

    def probe(host: str) -> dict[str, str] | None:
        if not _roku_port_open(host, timeout=connect_timeout):
            return None
        try:
            info = device_info(host, timeout=request_timeout)
        except (ValueError, RuntimeError):
            return None
        return {"host": host, "discovery": "scan", **info}

    devices: list[dict[str, str]] = []
    workers = max(1, min(int(max_workers), len(hosts)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for result in executor.map(probe, hosts):
            if result:
                devices.append(result)
    return devices


def discover_devices(lan_host: str) -> list[dict[str, str]]:
    """Discover Roku ECP devices with SSDP, with a bounded /24 scan fallback."""
    try:
        devices = _discover_ssdp(lan_host)
    except (OSError, ValueError):
        devices = []
    if not devices:
        devices = _discover_scan(lan_host)

    unique: dict[str, dict[str, str]] = {}
    for device in devices:
        key = str(device.get("device_key") or device.get("host") or "")
        if key:
            unique[key] = device
    output = list(unique.values())
    output.sort(key=lambda item: (
        str(item.get("name", "")).casefold(),
        ipaddress.ip_address(str(item["host"])),
    ))
    return output


def launch_dev(host: str, media_url: str) -> None:
    value = str(media_url or "").strip()
    if not value.startswith(("http://", "https://")):
        raise ValueError("Roku media URL must be HTTP or HTTPS.")
    query = urllib.parse.urlencode({"contentID": value, "MediaType": "tvSpecial"})
    _request(host, f"/launch/dev?{query}", method="POST", timeout=5.0)


def send_home(host: str) -> None:
    _request(host, "/keypress/Home", method="POST", timeout=3.0)
