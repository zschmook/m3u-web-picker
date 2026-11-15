from flask import jsonify, request
import movie_lighting
import sports_lighting
from .http import no_cache


def register_movie_lighting_routes(app):
    @app.get('/api/lighting/teams')
    def lighting_teams():
        return no_cache(jsonify(teams=sports_lighting.teams()))

    @app.get('/api/lighting/rooms/<room_id>/teams')
    def lighting_room_teams(room_id):
        return no_cache(jsonify(teams=sports_lighting.preferences(room_id)))

    @app.put('/api/lighting/rooms/<room_id>/teams')
    def save_lighting_room_teams(room_id):
        try:
            if not any(room['id'] == room_id for room in movie_lighting.status()['rooms']):
                raise ValueError('Room no longer exists.')
            return no_cache(jsonify(teams=sports_lighting.save(room_id, request.get_json(silent=True))))
        except ValueError as error:
            return no_cache(jsonify(error=str(error))), 400
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
        settings = movie_lighting.for_roku(request.args.get("roku_host", request.remote_addr))
        play_url = request.args.get('play_url', '')
        programme = None
        is_sports = request.args.get('is_sports') == 'true'
        if play_url.startswith('/guide/play/manual/') and 'api_guide_channels' in app.view_functions:
            from .roku_app import is_sports_channel
            channels = app.view_functions['api_guide_channels']().get_json()['channels']
            channel = next((value for value in channels if value.get('play_url') == play_url), None)
            is_sports = bool(channel and is_sports_channel(channel))
            programme = channel.get('now') if channel else None
        result = sports_lighting.apply(settings, play_url, is_sports, programme)
        return no_cache(jsonify({**result, 'sports_active': is_sports}))

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
