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
    "MX: 1\r\n"
    "ST: roku:ecp\r\n"
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


def _parse_ssdp_headers(payload: bytes) -> dict[str, str]:
    text = payload.decode("iso-8859-1", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def _ssdp_location_host(payload: bytes) -> str:
    headers = _parse_ssdp_headers(payload)
    if headers.get("st", "").lower() != "roku:ecp":
        return ""
    location = headers.get("location", "")
    if not location:
        return ""
    parsed = urllib.parse.urlparse(location)
    host = str(parsed.hostname or "").strip()
    if not host:
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if address.version != 4 or not (address.is_private or address.is_link_local):
        return ""
    return host


def _discover_ssdp_hosts(*, timeout: float = 1.25, attempts: int = 2) -> set[str]:
    """Return all Roku ECP hosts that answer the standard SSDP M-SEARCH."""
    hosts: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(("", 0))
        sock.settimeout(0.15)
        deadline = time.monotonic() + max(0.1, float(timeout))
        sends_left = max(1, int(attempts))
        next_send = 0.0

        while time.monotonic() < deadline:
            now = time.monotonic()
            if sends_left and now >= next_send:
                try:
                    sock.sendto(_SSDP_REQUEST, _SSDP_TARGET)
                except OSError:
                    return hosts
                sends_left -= 1
                next_send = now + 0.35

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.15, remaining))
            try:
                payload, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            host = _ssdp_location_host(payload)
            if host:
                hosts.add(host)
    finally:
        sock.close()
    return hosts


def discover_devices(
    lan_host: str,
    *,
    connect_timeout: float = 0.15,
    request_timeout: float = 0.8,
    max_workers: int = 64,
) -> list[dict[str, str]]:
    """Discover every Roku ECP device on the local LAN.

    Standard Roku SSDP discovery is attempted first. A parallel /24 ECP probe then
    fills in devices SSDP may miss because of multicast, router, or Docker quirks.
    """
    try:
        address = ipaddress.ip_address(str(lan_host or "").strip())
    except ValueError as exc:
        raise ValueError("Automatic Roku discovery needs the local LAN IPv4 address.") from exc
    if address.version != 4 or not (address.is_private or address.is_link_local):
        raise ValueError("Automatic Roku discovery needs a private/local LAN IPv4 address.")

    network = ipaddress.ip_network(f"{address}/24", strict=False)
    devices_by_host: dict[str, dict[str, str]] = {}

    def verify(host: str) -> dict[str, str] | None:
        try:
            candidate = ipaddress.ip_address(host)
        except ValueError:
            return None
        if candidate not in network:
            return None
        try:
            info = device_info(host, timeout=request_timeout)
        except (ValueError, RuntimeError):
            return None
        return {"host": host, **info}

    # Roku's documented discovery path. Multiple Rokus produce multiple replies.
    for host in sorted(_discover_ssdp_hosts(), key=ipaddress.ip_address):
        result = verify(host)
        if result:
            devices_by_host[host] = result

    # Keep the proven subnet scan as a safety net. This also catches extra Rokus
    # if an SSDP exchange returns only a subset of the devices on the LAN.
    hosts = [
        str(candidate)
        for candidate in network.hosts()
        if str(candidate) not in devices_by_host
    ]

    def probe(host: str) -> dict[str, str] | None:
        if not _roku_port_open(host, timeout=connect_timeout):
            return None
        return verify(host)

    workers = max(1, min(int(max_workers), len(hosts))) if hosts else 1
    if hosts:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(probe, hosts):
                if result:
                    devices_by_host[result["host"]] = result

    devices = list(devices_by_host.values())
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
