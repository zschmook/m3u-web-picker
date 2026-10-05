from flask import jsonify, request
import movie_lighting
from .http import no_cache


def register_movie_lighting_routes(app):
    @app.get("/api/movie-lighting/lights")
    def movie_lighting_lights():
        return no_cache(jsonify(lights=movie_lighting.lights()))

    @app.post("/api/movie-lighting/lights/refresh")
    def refresh_movie_lighting_lights():
        return no_cache(jsonify(movie_lighting.refresh_lights()))

    @app.post("/api/movie-lighting/lights/discover")
    def discover_movie_lighting_lights():
        try:
            return no_cache(jsonify(movie_lighting.discover_lights()))
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400

    @app.get("/api/movie-lighting")
    def movie_lighting_settings():
        return no_cache(jsonify(movie_lighting.status()))

    @app.patch("/api/movie-lighting")
    def save_movie_lighting_settings():
        try:
            return no_cache(jsonify(movie_lighting.save(request.get_json(silent=True))))
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400

    @app.get("/api/roku/movie-lighting")
    def roku_movie_lighting_settings():
        return no_cache(jsonify(movie_lighting.for_roku(request.args.get("roku_host", request.remote_addr))))

    @app.post("/api/movie-lighting/rooms")
    def add_movie_lighting_room():
        try:
            return no_cache(jsonify(movie_lighting.save_room(request.get_json(silent=True)))), 201
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400

    @app.patch("/api/movie-lighting/rooms/<room_id>")
    def save_movie_lighting_room(room_id):
        try:
            return no_cache(jsonify(movie_lighting.save_room(request.get_json(silent=True), room_id)))
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400

    @app.delete("/api/movie-lighting/rooms/<room_id>")
    def delete_movie_lighting_room(room_id):
        try:
            return no_cache(jsonify(movie_lighting.delete_room(room_id)))
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400
