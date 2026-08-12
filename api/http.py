from __future__ import annotations

from flask import jsonify


NO_CACHE = "no-cache, no-store, must-revalidate"


def json_error(message: object, status: int):
    return jsonify(error=str(message)), status


def no_cache(response):
    response.headers["Cache-Control"] = NO_CACHE
    return response


def no_cache_strict(response):
    response.headers["Cache-Control"] = NO_CACHE
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
