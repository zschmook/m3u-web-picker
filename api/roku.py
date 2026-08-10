from __future__ import annotations

import ipaddress
import re
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


def device_info(host: str) -> dict[str, str]:
    payload = _request(host, "/query/device-info", timeout=3.0)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("Roku answered, but device-info XML could not be parsed.") from exc

    def text(name: str) -> str:
        node = root.find(name)
        return (node.text or "").strip() if node is not None else ""

    friendly = text("user-device-name") or text("friendly-device-name") or text("model-name") or "Roku"
    return {
        "name": friendly,
        "model": text("model-name"),
        "model_number": text("model-number"),
        "serial_number": text("serial-number"),
        "software_version": text("software-version"),
    }


def launch_dev(host: str, media_url: str) -> None:
    value = str(media_url or "").strip()
    if not value.startswith(("http://", "https://")):
        raise ValueError("Roku media URL must be HTTP or HTTPS.")
    query = urllib.parse.urlencode({"contentId": value, "mediaType": "tvSpecial"})
    _request(host, f"/launch/dev?{query}", method="POST", timeout=5.0)


def send_home(host: str) -> None:
    _request(host, "/keypress/Home", method="POST", timeout=3.0)
