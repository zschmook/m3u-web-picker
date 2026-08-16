from __future__ import annotations

import os

from flask import Response, jsonify, request

import core
import sports
from media import mpegts
from .http import no_cache


_TRUTHY = {"1", "true", "yes", "on"}


def debug_tools_enabled() -> bool:
    return str(os.environ.get("M3U_DEBUG_TOOLS", "") or "").strip().lower() in _TRUTHY


def _debug_not_found() -> Response:
    return no_cache(
        Response(
            "Not found.\n",
            status=404,
            content_type="text/plain; charset=utf-8",
        )
    )


def _guide_channel_name(play_url: str, fallback: str) -> str:
    try:
        for channel in core.curated_channels_for_guide():
            if str(channel.get("play_url", "") or "") == play_url:
                return str(channel.get("name", "") or fallback).strip() or fallback
    except Exception:
        pass
    return fallback


def _single_channel_m3u(name: str, ts_path: str) -> Response:
    safe_name = " ".join(str(name or "M3U Web Picker").splitlines()).strip()
    origin = request.host_url.rstrip("/")
    body = f"#EXTM3U\n#EXTINF:-1,{safe_name}\n{origin}{ts_path}\n"
    response = Response(body, content_type="audio/x-mpegurl; charset=utf-8")
    response.headers["Content-Disposition"] = 'inline; filename="channel.m3u"'
    return no_cache(response)


def register_guide_debug_routes(app):
    @app.get("/api/guide/debug/status")
    def api_guide_debug_status():
        return no_cache(jsonify(enabled=debug_tools_enabled(), m3u_per_channel=True))

    @app.get("/guide/debug/ts/manual/<token>")
    def guide_debug_ts_manual(token: str):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = core.manual_stream_target(token)
        if not target:
            return no_cache(
                Response(
                    "Curated stream not found.\n",
                    status=404,
                    content_type="text/plain; charset=utf-8",
                )
            )
        return mpegts.response_for(target)

    @app.get("/guide/debug/ts/sports/<int:assigned_number>")
    def guide_debug_ts_sports(assigned_number: int):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return no_cache(
                Response(
                    "Sports stream not found.\n",
                    status=404,
                    content_type="text/plain; charset=utf-8",
                )
            )
        return mpegts.response_for(target)

    @app.get("/guide/debug/m3u/manual/<token>.m3u")
    def guide_debug_m3u_manual(token: str):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = core.manual_stream_target(token)
        if not target:
            return no_cache(
                Response(
                    "Curated stream not found.\n",
                    status=404,
                    content_type="text/plain; charset=utf-8",
                )
            )
        play_url = f"/guide/play/manual/{token}"
        name = _guide_channel_name(play_url, "Manual channel")
        return _single_channel_m3u(name, f"/guide/debug/ts/manual/{token}")

    @app.get("/guide/debug/m3u/sports/<int:assigned_number>.m3u")
    def guide_debug_m3u_sports(assigned_number: int):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return no_cache(
                Response(
                    "Sports stream not found.\n",
                    status=404,
                    content_type="text/plain; charset=utf-8",
                )
            )
        play_url = f"/guide/play/sports/{assigned_number}"
        name = _guide_channel_name(play_url, f"Sports channel {assigned_number}")
        return _single_channel_m3u(
            name,
            f"/guide/debug/ts/sports/{assigned_number}",
        )
