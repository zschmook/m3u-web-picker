import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
import app_config
import movie_lighting
from api.movie_lighting import register_movie_lighting_routes


class MovieLightingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.patch = patch.object(app_config, "CONFIG_PATH", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)
        app = Flask(__name__)
        register_movie_lighting_routes(app)
        self.client = app.test_client()

    def configure(self):
        target = {"ip": "10.0.0.7", "name": "Shelf Light", "model": "KL110(US)", "device_id": "shelf-id"}
        movie_lighting.remember_lights([target])
        return movie_lighting.save({"enabled": True, "brightness": 10, "paused_brightness": 100,
            "target": target,
            "roku_host": "10.0.0.2", "roku_name": "Main TV Roku"})

    def test_saved_levels_reach_only_the_selected_roku_without_repackaging(self):
        self.configure()
        first = self.client.get("/api/roku/movie-lighting?roku_host=10.0.0.2").json
        self.assertEqual((first["brightness"], first["paused_brightness"]), (10, 100))
        response = self.client.patch("/api/movie-lighting", json={"brightness": 15, "paused_brightness": 80})
        self.assertEqual(response.status_code, 200)
        second = self.client.get("/api/roku/movie-lighting?roku_host=10.0.0.2").json
        self.assertEqual((second["brightness"], second["paused_brightness"]), (15, 80))
        self.assertGreater(second["revision"], first["revision"])
        other = self.client.get("/api/roku/movie-lighting?roku_host=10.0.0.29").json
        self.assertFalse(other["enabled"])
        self.assertNotIn("target", other)

    def test_invalid_updates_preserve_preferences_and_other_sections(self):
        app_config.update_section("network", {"external_port": 9998})
        self.configure()
        original = app_config.load()
        for values in ({"brightness": 0}, {"paused_brightness": 101}, {"brightness": True},
                {"brightness": 10.5}, {"transition_ms": 6000}, {"enabled": "true"}, [], None,
                {"roku_host": "8.8.8.8"}, {"target": {"ip": "127.0.0.1", "name": "Other", "model": "KL110"}}):
            with self.subTest(values=values):
                response = self.client.patch("/api/movie-lighting", json=values)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(app_config.load(), original)
        self.client.patch("/api/movie-lighting", json={"brightness": 20})
        self.assertEqual(app_config.section("network"), {"external_port": 9998})

    def test_disabled_preferences_remain_saved_without_enabling_other_rooms(self):
        self.configure()
        self.client.patch("/api/movie-lighting", json={"enabled": False})
        self.assertFalse(self.client.get("/api/roku/movie-lighting?roku_host=10.0.0.2").json["enabled"])
        self.assertEqual(movie_lighting.status()["brightness"], 10)

    def test_only_an_identified_bulb_can_be_assigned(self):
        self.configure()
        original = movie_lighting.status()
        response = self.client.patch("/api/movie-lighting", json={"target": {
            "ip": "10.0.0.7", "name": "Shelf Light", "model": "KL110(US)", "device_id": "different-bulb"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(movie_lighting.status(), original)

    def test_refreshing_names_does_not_assign_another_bulb_or_change_levels(self):
        self.configure()
        original = movie_lighting.status()
        changed = {"ip": "10.0.0.7", "name": "Bathroom Light", "model": "KL110(US)", "device_id": "bathroom-id"}
        with patch.object(movie_lighting, "_read_light", return_value=changed):
            response = self.client.post("/api/movie-lighting/lights/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["responding"], 1)
        self.assertEqual(movie_lighting.status(), original)
        self.assertEqual(len(response.json["lights"]), 2)

    def test_room_assignment_works_on_other_private_networks(self):
        light = {"ip": "192.168.50.14", "name": "Theater Lamp", "model": "KL110(US)", "device_id": "theater-id"}
        movie_lighting.remember_lights([light])
        settings = movie_lighting.save({"enabled": True, "target": light, "roku_host": "192.168.50.20", "roku_name": "Theater Roku"})
        self.assertEqual(movie_lighting.for_roku("192.168.50.20")["target"], light)
        self.assertFalse(movie_lighting.for_roku("192.168.50.21")["enabled"])
        self.assertEqual(settings["brightness"], 10)


if __name__ == "__main__":
    unittest.main()
