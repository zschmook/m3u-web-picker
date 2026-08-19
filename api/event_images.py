from __future__ import annotations

from flask import Response

import event_logos


def register_event_image_routes(app):
    @app.get("/api/event-logo/<digest>.png")
    def api_event_logo(digest: str):
        rendered = event_logos.render_event_logo(digest)
        if not rendered:
            return Response(
                "Event logo not found.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        payload, cache_state = rendered
        response = Response(payload, content_type="image/png")
        # A fallback composite can improve as soon as a previously unavailable
        # upstream team logo becomes reachable. Never let a guide/browser pin
        # the fallback image under the stable event URL for hours.
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-M3U-Event-Logo-Cache"] = cache_state
        return response
