from flask import jsonify, request

import core


def register_group_routes(app):
    @app.get("/api/groups")
    def api_groups():
        return jsonify(groups=core.list_custom_groups())

    @app.post("/api/groups")
    def api_create_group():
        data = request.get_json(force=True, silent=True) or {}
        try:
            group = core.create_custom_group(str(data.get("name", "")))
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(group=group), 201

    @app.get("/api/groups/<slug>/channels")
    def api_group_channels(slug: str):
        return jsonify(channel_keys=core.group_member_keys(slug))

    @app.post("/api/groups/<slug>/channels")
    def api_add_group_channels(slug: str):
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(key).strip() for key in data.get("channel_keys", []) if str(key).strip()]
        try:
            added = core.add_channels_to_group(slug, keys)
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(added=added)

    @app.delete("/api/groups/<slug>/channels")
    def api_remove_group_channels(slug: str):
        data = request.get_json(force=True, silent=True) or {}
        keys = [str(key).strip() for key in data.get("channel_keys", []) if str(key).strip()]
        try:
            removed = core.remove_channels_from_group(slug, keys)
        except Exception as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(removed=removed)
