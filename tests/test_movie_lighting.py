import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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

    def test_discovery_adds_lights_and_preserves_offline_bulbs_and_room_preferences(self):
        original = self.configure()
        desk = {"ip": "10.0.0.8", "name": "Desk", "model": "KL110", "device_id": "desk-id"}
        with patch.object(movie_lighting, "load_settings", return_value=SimpleNamespace(lan_host="10.0.0.100")), \
                patch.object(movie_lighting, "_read_light", side_effect=lambda ip, **kwargs: desk if ip == desk["ip"] else None) as probe:
            response = self.client.post("/api/movie-lighting/lights/discover")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["responding"], 1)
        self.assertEqual(response.json["subnet"], "10.0.0.0/24")
        self.assertEqual({light["device_id"] for light in response.json["lights"]}, {"shelf-id", "desk-id"})
        self.assertEqual(movie_lighting.status(), original)
        self.assertLessEqual(probe.call_count, 254)
        self.assertTrue(all(call.args[0].startswith("10.0.0.") for call in probe.call_args_list))

    def test_discovery_updates_the_same_light_by_identity_without_reassigning_the_room(self):
        original = self.configure()
        observed = {**original["target"], "ip": "192.168.50.14", "name": "Renamed Shelf"}
        with patch.object(movie_lighting, "load_settings", return_value=SimpleNamespace(lan_host="192.168.50.10")), \
                patch.object(movie_lighting, "_read_light", side_effect=lambda ip, **kwargs: observed if ip == observed["ip"] else None):
            response = self.client.post("/api/movie-lighting/lights/discover")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["lights"], [observed])
        self.assertEqual(movie_lighting.status(), original)

    def test_discovery_requires_a_private_lan_address_before_sending_any_queries(self):
        self.configure()
        original = app_config.load()
        for address in ("", "8.8.8.8", "127.0.0.1", "hostname", "::1"):
            with self.subTest(address=address), \
                    patch.object(movie_lighting, "load_settings", return_value=SimpleNamespace(lan_host=address)), \
                    patch.object(movie_lighting, "_read_light") as probe:
                response = self.client.post("/api/movie-lighting/lights/discover")
                self.assertEqual(response.status_code, 400)
                probe.assert_not_called()
                self.assertEqual(app_config.load(), original)

    def test_device_probe_sends_only_an_identity_query_and_rejects_non_lights(self):
        def encrypt(payload):
            key = 171
            encoded = bytearray()
            for value in json.dumps(payload).encode():
                key ^= value
                encoded.append(key)
            return bytes(encoded)

        product = dict(alias="Shelf", model="KL110", deviceId="shelf-id", light_state={"on_off": 1})
        for info, expected in ((product, True), ({k: v for k, v in product.items() if k != "light_state"}, False)):
            with self.subTest(light=expected):
                client = MagicMock()
                client.__enter__.return_value = client
                client.recvfrom.return_value = (encrypt({"system": {"get_sysinfo": info}}), ("10.0.0.7", 9999))
                with patch.object(movie_lighting.socket, "socket", return_value=client):
                    result = movie_lighting._read_light("10.0.0.7")
                self.assertEqual(result is not None, expected)
                packet, address = client.sendto.call_args.args
                self.assertEqual(address, ("10.0.0.7", 9999))
                decoded = bytes(value ^ (packet[index - 1] if index else 171) for index, value in enumerate(packet))
                self.assertEqual(json.loads(decoded), {"system": {"get_sysinfo": {}}})
                client.sendto.assert_called_once()

    def test_legacy_pair_is_a_named_room_without_writing_on_read(self):
        light = {"ip": "10.0.0.7", "name": "Shelf", "model": "KL110", "device_id": "shelf"}
        app_config.update_section("movie_lighting", {"enabled": True, "target": light,
            "roku_host": "10.0.0.2", "brightness": 17, "revision": 8})
        original = self.path.read_bytes()
        room = self.client.get("/api/movie-lighting").json["rooms"][0]
        self.assertEqual((room["id"], room["name"], room["brightness"]), ("legacy", "Room 1", 17))
        self.assertEqual(room["targets"], [light])
        self.assertEqual(self.path.read_bytes(), original)

    def test_named_rooms_have_independent_groups_and_settings(self):
        self.configure()
        lights = movie_lighting.lights() + [{"ip": "10.0.0.8", "name": "Desk", "model": "KL125", "device_id": "desk"},
            {"ip": "10.0.0.9", "name": "Bedside", "model": "KL110", "device_id": "bedside"}]
        movie_lighting.remember_lights(lights)
        first = movie_lighting.status()["rooms"][0]
        response = self.client.patch(f"/api/movie-lighting/rooms/{first['id']}", json={"name": "Living room", "targets": lights[:2]})
        self.assertEqual(response.status_code, 200)
        saved_first = response.json
        response = self.client.post("/api/movie-lighting/rooms", json={"name": "Bedroom", "enabled": True,
            "roku_host": "10.0.0.3", "targets": [lights[2]], "brightness": 25, "paused_brightness": 70})
        self.assertEqual(response.status_code, 201)
        second = response.json
        self.assertEqual(movie_lighting.for_roku("10.0.0.2")["targets"], lights[:2])
        self.assertEqual(movie_lighting.for_roku("10.0.0.3")["brightness"], 25)
        self.client.patch(f"/api/movie-lighting/rooms/{second['id']}", json={"brightness": 30})
        self.assertEqual(movie_lighting.status()["rooms"][0], saved_first)
        self.assertNotIn("targets", movie_lighting.for_roku("10.0.0.99"))

    def test_duplicate_room_assignments_and_invalid_groups_do_not_write(self):
        self.configure()
        original = app_config.load()
        light = movie_lighting.lights()[0]
        for values in ({"name": "Other", "roku_host": "10.0.0.2"}, {"name": "Other", "targets": [light]},
                {"name": "room 1"}, {"name": " "}, {"name": "Other", "targets": [light, light]},
                {"name": "Other", "targets": "lights"}, {"name": "Other", "enabled": True},
                {"name": "Other", "targets": [{**light, "ip": "10.0.0.19"}]},
                {"name": "Other", "id": "overwrite"}):
            with self.subTest(values=values):
                response = self.client.post("/api/movie-lighting/rooms", json=values)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(app_config.load(), original)

    def test_deleting_last_room_does_not_resurrect_old_preferences(self):
        light = {"ip": "10.0.0.7", "name": "Shelf", "model": "KL110", "device_id": "shelf"}
        app_config.update_section("movie_lighting", {"enabled": True, "target": light, "roku_host": "10.0.0.2"})
        response = self.client.delete("/api/movie-lighting/rooms/legacy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["rooms"], [])
        self.assertFalse(movie_lighting.for_roku("10.0.0.2")["enabled"])
        self.assertNotIn("target", movie_lighting.for_roku("10.0.0.2"))
        self.assertEqual(self.client.patch("/api/movie-lighting/rooms/legacy", json={"brightness": 20}).status_code, 400)

    def test_group_receiver_resolves_discovered_light_by_identity(self):
        self.configure()
        original = movie_lighting.status()
        moved = {**movie_lighting.lights()[0], "ip": "10.0.0.17", "name": "Renamed Shelf"}
        movie_lighting.remember_lights([moved])
        self.assertEqual(movie_lighting.for_roku("10.0.0.2")["targets"], [moved])
        self.assertEqual(movie_lighting.status(), original)


if __name__ == "__main__":
    unittest.main()
