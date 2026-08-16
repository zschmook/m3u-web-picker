from __future__ import annotations

import os

from flask import Response, jsonify

import core
import sports
from media import mpegts
from .http import no_cache


_TRUTHY = {"1", "true", "yes", "on"}


def debug_tools_enabled() -> bool:
    return str(os.environ.get("M3U_DEBUG_TOOLS", "") or "").strip().lower() in _TRUTHY


def _debug_not_found() -> Response:
    return no_cache(Response("Not found.\n", status=404, content_type="text/plain; charset=utf-8"))


def register_guide_debug_routes(app):
    @app.get("/api/guide/debug/status")
    def api_guide_debug_status():
        return no_cache(jsonify(enabled=debug_tools_enabled()))

    @app.get("/guide/debug/ts/manual/<token>")
    def guide_debug_ts_manual(token: str):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = core.manual_stream_target(token)
        if not target:
            return no_cache(Response("Curated stream not found.\n", status=404, content_type="text/plain; charset=utf-8"))
        return mpegts.response_for(target)

    @app.get("/guide/debug/ts/sports/<int:assigned_number>")
    def guide_debug_ts_sports(assigned_number: int):
        if not debug_tools_enabled():
            return _debug_not_found()
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return no_cache(Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8"))
        return mpegts.response_for(target)
