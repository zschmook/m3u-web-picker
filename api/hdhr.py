from __future__ import annotations

import re
from xml.etree import ElementTree

from flask import Response, jsonify, request

import core
import sports
from media import mpegts
from .http import no_cache


HDHR_DEVICE_ID = "1234ABCD"
HDHR_FRIENDLY_NAME = "M3U Web Picker"
HDHR_MODEL = "HDTC-2US"
HDHR_TUNER_COUNT = 2
HDHR_FIRMWARE_VERSION = "20260810"


def _base_url() -> str:
    return request.host_url.rstrip("/")


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
                "GuideName": name,
                "URL": f"{base}/hdhr/stream/{number}",
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


def register_hdhr_routes(app):
    @app.get("/discover.json")
    def hdhr_discover():
        base = _base_url()
        response = jsonify(
            FriendlyName=HDHR_FRIENDLY_NAME,
            Manufacturer="Silicondust",
            ModelNumber=HDHR_MODEL,
            FirmwareName="hdhomerun",
            FirmwareVersion=HDHR_FIRMWARE_VERSION,
            DeviceID=HDHR_DEVICE_ID,
            DeviceAuth="m3u-web-picker",
            BaseURL=base,
            LineupURL=f"{base}/lineup.json",
            TunerCount=HDHR_TUNER_COUNT,
        )
        return no_cache(response)

    @app.get("/lineup_status.json")
    def hdhr_lineup_status():
        response = jsonify(
            ScanInProgress=0,
            ScanPossible=0,
            Source="Cable",
            SourceList=["Cable"],
        )
        return no_cache(response)

    @app.get("/lineup.json")
    def hdhr_lineup():
        return no_cache(jsonify(_lineup_rows()))

    @app.get("/device.xml")
    @app.get("/capability")
    def hdhr_device():
        response = Response(_device_xml(), content_type="application/xml; charset=utf-8")
        return no_cache(response)

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
