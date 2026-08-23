from flask import jsonify, request

import network_config
from .http import no_cache


def register_network_config_routes(app):
    @app.get("/api/network-config")
    def api_network_config():
        return no_cache(jsonify(network_config.status()))

    @app.patch("/api/network-config")
    def api_update_network_config():
        data = request.get_json(force=True, silent=True) or {}
        try:
            return no_cache(jsonify(network_config.save(data)))
        except ValueError as exc:
            return no_cache(jsonify(error=str(exc), **network_config.status())), 400
