from __future__ import annotations

from flask import jsonify

from settings import load_settings


def image_cache_count() -> int:
    """Count cached image payloads, excluding JSON/signature metadata."""
    root = load_settings().data_dir / "logo_cache"
    if not root.exists():
        return 0

    count = 0
    try:
        count += sum(1 for path in root.glob("*.bin") if path.is_file())
    except OSError:
        pass

    event_dir = root / "events"
    try:
        count += sum(1 for path in event_dir.glob("*.png") if path.is_file())
    except OSError:
        pass
    return count


def register_logo_cache_status_routes(app):
    @app.get("/api/logo-cache/status")
    def api_logo_cache_status():
        return jsonify({"images": image_cache_count()})
