from flask import jsonify, request

import media_pipeline
from .http import no_cache


def register_media_pipeline_routes(app):
    @app.get("/api/media-pipeline")
    def api_media_pipeline():
        return no_cache(jsonify(media_pipeline.status()))

    @app.post("/api/media-pipeline/test")
    def api_media_pipeline_test():
        return no_cache(jsonify(media_pipeline.status(run_test=True)))

    @app.patch("/api/media-pipeline")
    def api_update_media_pipeline():
        data = request.get_json(force=True, silent=True) or {}
        previous = media_pipeline.settings()
        try:
            saved = media_pipeline.save(data)
        except (TypeError, ValueError) as exc:
            return no_cache(jsonify(error=str(exc), **media_pipeline.status())), 400
        filtering_update = {}
        if bool(previous.get("commercial_detection_enabled")) != bool(
            saved.get("commercial_detection_enabled")
        ):
            from media import mpegts

            filtering_update = mpegts.apply_automatic_filtering_setting(
                bool(saved.get("commercial_detection_enabled"))
            )
        return no_cache(jsonify(
            **media_pipeline.status(),
            streams_recycled=0,
            filtering_update=filtering_update,
        ))
