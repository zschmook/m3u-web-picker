from __future__ import annotations

import re
from xml.etree import ElementTree

from flask import Response, jsonify, request

import core
import hdhr_config
import sports
from media import mpegts
from .http import no_cache


# Must satisfy SiliconDust's HDHomeRun device-ID checksum rule.
HDHR_DEVICE_ID = "1234ABC2"
HDHR_DEVICE_AUTH = "m3u-web-picker"
HDHR_FRIENDLY_NAME = "M3U Web Picker"
HDHR_MODEL = "HDTC-2US"
HDHR_TUNER_COUNT = 2
# Temporary interoperability marker so Jellyfin can distinguish channels
# imported through the HDHomeRun facade from the same channels imported via M3U.
# This affects only HDHomeRun GuideName values; source channel names are untouched.
HDHR_GUIDE_NAME_SUFFIX = " [HDHR]"
# Telly uses the HDHomeRun EXTEND ATSC personality specifically for Plex
# compatibility. Keep the same firmware family while retaining our own ID.
HDHR_FIRMWARE_NAME = "hdhomeruntc_atsc"
HDHR_FIRMWARE_VERSION = "20150826"

_HDHR_HTTP_PREFIXES = (
    "/discover.json",
    "/lineup_status.json",
    "/lineup.json",
    "/lineup.post",
    "/device.xml",
    "/capability",
    "/hdhr/stream/",
    "/auto/v",
)


def _base_url() -> str:
    return request.host_url.rstrip("/")


def _is_plex_request() -> bool:
    user_agent = str(request.headers.get("User-Agent", "") or "").lower()
    if "plex" in user_agent:
        return True
    return any(str(name).lower().startswith("x-plex-") for name in request.headers.keys())


def _hdhr_guide_name(name: str) -> str:
    value = str(name or "").strip()
    if not value or value.endswith(HDHR_GUIDE_NAME_SUFFIX):
        return value
    return f"{value}{HDHR_GUIDE_NAME_SUFFIX}"


def _support_payload() -> dict:
    base = _base_url()
    return {
        "enabled": hdhr_config.is_enabled(),
        "device_id": HDHR_DEVICE_ID,
        "friendly_name": HDHR_FRIENDLY_NAME,
        "model": HDHR_MODEL,
        "tuner_count": HDHR_TUNER_COUNT,
        "base_url": base,
        "discover_url": f"{base}/discover.json",
        "lineup_url": f"{base}/lineup.json",
        "guide_name_suffix": HDHR_GUIDE_NAME_SUFFIX.strip(),
    }


def _resolve_play_url(play_url: str) -> str:
    value = str(play_url or "").split("?", 1)[0].strip()
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        return core.manual_stream_target(manual.group(1))
    generated = re.fullmatch(r"/guide/play/sports/(\d+)", value)
    if generated:
        return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


def _lineup_rows() -> list[dict]:
    base = _base_url()
    output = []
    for channel in core.curated_channels_for_guide():
        number = str(channel.get("number", "") or "").strip()
        name = str(channel.get("name", "") or "").strip()
        if not number or not name:
            continue
        output.append(
            {
                "GuideNumber": number,
                "GuideName": _hdhr_guide_name(name),
                # Native HDHomeRun HTTP live-TV URL shape. Plex-compatible
                # emulators use this form and may append their own query string.
                "URL": f"{base}/auto/v{number}",
            }
        )
    return output


def _device_xml() -> bytes:
    root = ElementTree.Element("root", {"xmlns": "urn:schemas-upnp-org:device-1-0"})
    spec = ElementTree.SubElement(root, "specVersion")
    ElementTree.SubElement(spec, "major").text = "1"
    ElementTree.SubElement(spec, "minor").text = "0"
    ElementTree.SubElement(root, "URLBase").text = _base_url() + "/"

    device = ElementTree.SubElement(root, "device")
    ElementTree.SubElement(device, "deviceType").text = "urn:schemas-upnp-org:device:MediaServer:1"
    ElementTree.SubElement(device, "friendlyName").text = HDHR_FRIENDLY_NAME
    ElementTree.SubElement(device, "manufacturer").text = "Silicondust"
    ElementTree.SubElement(device, "modelName").text = HDHR_MODEL
    ElementTree.SubElement(device, "modelNumber").text = HDHR_MODEL
    ElementTree.SubElement(device, "serialNumber").text = HDHR_DEVICE_ID
    ElementTree.SubElement(device, "UDN").text = f"uuid:{HDHR_DEVICE_ID}"
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _device_xml_response() -> Response:
    response = Response(_device_xml(), content_type="application/xml; charset=utf-8")
    return no_cache(response)


def register_hdhr_routes(app):
    @app.before_request
    def hdhr_http_trace_and_plex_root_probe():
        hdhr_endpoint = request.path.startswith(_HDHR_HTTP_PREFIXES)
        plex_root_probe = (
            request.path == "/"
            and request.method in {"GET", "HEAD"}
            and _is_plex_request()
        )
        if (hdhr_endpoint or plex_root_probe) and not hdhr_config.is_enabled():
            return no_cache(Response(status=404))
        if hdhr_endpoint or plex_root_probe:
            print(
                "HDHomeRun HTTP "
                f"{request.method} {request.path} "
                f"from {request.remote_addr or '?'} "
                f"host={request.host or '?'} "
                f"ua={request.headers.get('User-Agent', '')!r}",
                flush=True,
            )
        # Plex-compatible tuner emulators such as Telly serve their UPnP
        # capability XML at the base address. Do that only for Plex so the
        # normal M3U Web Picker browser UI can continue owning '/'.
        if plex_root_probe:
            return _device_xml_response()
        return None

    @app.after_request
    def hdhr_http_headers(response):
        if request.path.startswith(_HDHR_HTTP_PREFIXES):
            # Real HDHomeRun HTTP endpoints allow cross-origin access. Modern
            # Chromium/WebView also performs local/private-network checks when
            # a web origin fetches an RFC1918 address, so explicitly allow the
            # private-network preflight as well.
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Range, Accept, Origin"
            )
            response.headers["Access-Control-Expose-Headers"] = (
                "Content-Length, Content-Range, Accept-Ranges"
            )
            response.headers["Access-Control-Allow-Private-Network"] = "true"
            response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers["Timing-Allow-Origin"] = "*"
            response.headers["Vary"] = "Origin, Access-Control-Request-Private-Network"
        return response

    @app.get("/api/hdhr/status")
    def hdhr_support_status():
        return no_cache(jsonify(_support_payload()))

    @app.patch("/api/hdhr/settings")
    def hdhr_support_settings():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get("enabled")) is not bool:
            return jsonify(error="enabled must be true or false"), 400

        enabled = hdhr_config.set_enabled(payload["enabled"])
        # The in-container responder is useful on host-networked Linux and for
        # tests. Docker Desktop may still require tools/hdhr_discovery_host.py;
        # that host helper polls /api/hdhr/status and follows this same switch.
        from .hdhr_discovery import start_hdhr_discovery, stop_hdhr_discovery

        if enabled:
            start_hdhr_discovery()
        else:
            stop_hdhr_discovery()
        return no_cache(jsonify(_support_payload()))

    @app.get("/discover.json")
    def hdhr_discover():
        base = _base_url()
        response = jsonify(
            FriendlyName=HDHR_FRIENDLY_NAME,
            Manufacturer="Silicondust",
            ModelNumber=HDHR_MODEL,
            FirmwareName=HDHR_FIRMWARE_NAME,
            FirmwareVersion=HDHR_FIRMWARE_VERSION,
            DeviceID=HDHR_DEVICE_ID,
            DeviceAuth=HDHR_DEVICE_AUTH,
            BaseURL=base,
            LineupURL=f"{base}/lineup.json",
            TunerCount=HDHR_TUNER_COUNT,
        )
        return no_cache(response)

    @app.get("/lineup_status.json")
    def hdhr_lineup_status():
        # Plex-capable HDHomeRun emulators advertise a scan operation even
        # though the IPTV lineup is already known. The matching lineup.post
        # route below acknowledges start/abort as no-ops.
        response = jsonify(
            ScanInProgress=0,
            ScanPossible=1,
            Source="Cable",
            SourceList=["Cable"],
        )
        return no_cache(response)

    @app.post("/lineup.post")
    def hdhr_lineup_scan():
        action = str(request.args.get("scan", "") or "").strip().lower()
        if action in {"start", "abort"}:
            return no_cache(Response(status=200))
        return no_cache(
            Response(
                "Invalid scan command.\n",
                status=400,
                content_type="text/plain; charset=utf-8",
            )
        )

    @app.get("/lineup.json")
    def hdhr_lineup():
        return no_cache(jsonify(_lineup_rows()))

    @app.get("/device.xml")
    @app.get("/capability")
    def hdhr_device():
        return _device_xml_response()

    @app.route("/hdhr/stream/<guide_number>", methods=["GET", "HEAD"])
    @app.route("/auto/v<guide_number>", methods=["GET", "HEAD"])
    def hdhr_stream(guide_number: str):
        channel = next(
            (
                item
                for item in core.curated_channels_for_guide()
                if str(item.get("number", "") or "").strip() == str(guide_number).strip()
            ),
            None,
        )
        if channel is None:
            return Response("Channel not found.\n", status=404, content_type="text/plain; charset=utf-8")
        target = _resolve_play_url(str(channel.get("play_url", "") or ""))
        if not target:
            return Response("Channel stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        if request.method == "HEAD":
            response = Response(status=200, content_type="video/mp2t")
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response
        return mpegts.response_for(target)
