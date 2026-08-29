from __future__ import annotations

import sqlite3
from contextlib import closing

from flask import jsonify, request

import channel_learning_rotation
import commercial_profiles
import commercial_signatures
import core
from .http import no_cache


def _database_stats() -> dict:
    commercial_profiles.ensure_schema(core.DB_PATH)
    with closing(sqlite3.connect(core.DB_PATH, timeout=30)) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT channel_identity),
                SUM(CASE WHEN label = 'commercial' THEN 1 ELSE 0 END),
                COUNT(*)
            FROM commercial_channel_observations
            """
        ).fetchone()
        episode_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM commercial_channel_episodes"
            ).fetchone()[0]
            or 0
        )
    library = commercial_signatures.library_stats(core.DB_PATH)
    return {
        "channels_with_data": int(row[0] or 0),
        "commercial_samples": int(row[1] or 0),
        "total_samples": int(row[2] or 0),
        "probable_commercials": int(library.get("classified") or 0),
        "commercial_occurrences": int(library.get("occurrences") or 0),
        "commercial_episodes": episode_count,
    }


def _payload() -> dict:
    return {
        **channel_learning_rotation.rotation.status(),
        "database": _database_stats(),
    }


def register_channel_learning_rotation_routes(app):
    @app.get("/api/channel-learning-rotation")
    def api_channel_learning_rotation_status():
        return no_cache(jsonify(_payload()))

    @app.post("/api/channel-learning-rotation")
    def api_channel_learning_rotation_start():
        try:
            body = request.get_json(silent=True) or {}
            minutes = int(body.get(
                "channel_minutes",
                channel_learning_rotation.rotation.channel_seconds // 60,
            ))
            channel_learning_rotation.rotation.start(
                core.curated_channels_for_guide(),
                db_path=core.DB_PATH,
                archive_root=core.DATA_DIR / "channel-learning-runs",
                channel_seconds=minutes * 60,
            )
        except (TypeError, ValueError) as exc:
            return no_cache(jsonify(error=str(exc))), 400
        return no_cache(jsonify(_payload()))

    @app.delete("/api/channel-learning-rotation")
    def api_channel_learning_rotation_stop():
        channel_learning_rotation.rotation.stop()
        return no_cache(jsonify(_payload()))
