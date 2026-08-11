from __future__ import annotations

import html
from urllib.parse import quote

from flask import Response, jsonify, request, stream_with_context

import core
from media import mpegts
from playback import hdhomerun
from playback.targets import lineup_channel, resolve_play_target
from settings import load_settings
from .http import no_cache


def _base_url() -> str:
    settings = load_settings()
    if settings.lan_host:
        return f"http://{settings.lan_host}:{settings.external_port}"
    return request.url_root.rstrip("/")


def _lineup_rows() -> list[dict]:
    base = _base_url().rstrip("/")
    rows: list[dict] = []
    for channel in core.curated_channels_for_guide():
        number = str(channel.get("number", "") or "").strip()
        name = str(channel.get("name", "") or "").strip() or f"Channel {number}"
        if not number:
            continue
        rows.append({
            "GuideNumber": number,
            "GuideName": name,
            "URL": f"{base}/auto/v{quote(number, safe='.')}",
        })
    return rows


def _hdhr_error(message: str, status: int, code: int) -> Response:
    response = Response(f"{message}\n", status=status, content_type="text/plain; charset=utf-8")
    response.headers["X-HDHomeRun-Error"] = f"{int(code)} {message}"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _duration_arg() -> int | None:
    raw = str(request.args.get("duration", "") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(1, min(value, 24 * 60 * 60))


def _stream_channel(guide_number: str, tuner_index: int | None = None) -> Response:
    channel = lineup_channel(guide_number)
    if channel is None:
        return _hdhr_error("Unknown Channel", 404, 801)

    target = resolve_play_target(str(channel.get("play_url", "") or ""))
    if not target:
        return _hdhr_error("Unknown Channel", 404, 801)

    lease = hdhomerun.TUNERS.acquire(tuner_index)
    if lease is None:
        if tuner_index is not None:
            return _hdhr_error("Tuner In Use", 503, 804)
        return _hdhr_error("All Tuners In Use", 503, 805)

    duration = _duration_arg()

    def generate():
        try:
            yield from mpegts.stream(target, duration=duration)
        finally:
            hdhomerun.TUNERS.release(lease)

    response = Response(
        stream_with_context(generate()),
        content_type="video/mp2t",
        direct_passthrough=True,
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _device_xml() -> str:
    device = hdhomerun.device_metadata(_base_url())
    base = str(device["BaseURL"])
    friendly = html.escape(str(device["FriendlyName"]))
    model = html.escape(str(device["ModelNumber"]))
    device_id = html.escape(str(device["DeviceID"]))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>{html.escape(base)}/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{friendly}</friendlyName>
    <manufacturer>Silicondust</manufacturer>
    <manufacturerURL>https://www.silicondust.com/</manufacturerURL>
    <modelDescription>HDHomeRun</modelDescription>
    <modelName>{model}</modelName>
    <modelNumber>{model}</modelNumber>
    <serialNumber>{device_id}</serialNumber>
    <UDN>uuid:{device_id}</UDN>
  </device>
</root>
"""


def register_hdhomerun_routes(app):
    @app.get("/discover.json")
    def hdhr_discover_json():
        return no_cache(jsonify(hdhomerun.device_metadata(_base_url())))

    @app.get("/lineup_status.json")
    def hdhr_lineup_status():
        return no_cache(jsonify(
            ScanInProgress=0,
            ScanPossible=0,
            Source="Antenna",
            SourceList=["Antenna"],
        ))

    @app.get("/device.xml")
    @app.get("/capability")
    def hdhr_device_xml():
        response = Response(_device_xml(), content_type="application/xml; charset=utf-8")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/lineup.json")
    def hdhr_lineup_json():
        return no_cache(jsonify(_lineup_rows()))

    @app.get("/lineup.m3u")
    def hdhr_lineup_m3u():
        lines = ["#EXTM3U"]
        for row in _lineup_rows():
            lines.append(f'#EXTINF:-1 tvg-chno="{row["GuideNumber"]}",{row["GuideName"]}')
            lines.append(row["URL"])
        response = Response("\n".join(lines) + "\n", content_type="audio/x-mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/lineup.xml")
    def hdhr_lineup_xml():
        body = ["<?xml version=\"1.0\" encoding=\"utf-8\"?>", "<Lineup>"]
        for row in _lineup_rows():
            body.extend((
                "  <Program>",
                f"    <GuideNumber>{html.escape(row['GuideNumber'])}</GuideNumber>",
                f"    <GuideName>{html.escape(row['GuideName'])}</GuideName>",
                f"    <URL>{html.escape(row['URL'])}</URL>",
                "  </Program>",
            ))
        body.append("</Lineup>")
        response = Response("\n".join(body) + "\n", content_type="application/xml")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/auto/v<guide_number>")
    def hdhr_auto_stream(guide_number: str):
        return _stream_channel(guide_number)

    @app.get("/tuner<int:tuner_index>/v<guide_number>")
    def hdhr_tuner_stream(tuner_index: int, guide_number: str):
        return _stream_channel(guide_number, tuner_index=tuner_index)

    @app.get("/api/hdhomerun/status")
    def hdhr_status():
        return no_cache(jsonify(
            ok=True,
            device=hdhomerun.device_metadata(_base_url()),
            lineup_count=len(_lineup_rows()),
            tuners=hdhomerun.TUNERS.status(),
            discovery_port=65001,
            discovery_daemon="host-side tools/hdhr_discovery_host.py",
        ))
