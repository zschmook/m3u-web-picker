from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["M3U_DISABLE_SCHEDULER"] = "true"

try:
    from app import app  # noqa: E402
except ModuleNotFoundError as exc:  # Lightweight source-test environments may omit Flask.
    if exc.name != "flask":
        raise
    app = None


@unittest.skipIf(app is None, "Flask is installed inside the Docker image")
class HdHomeRunApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.environment = patch.dict(
            "os.environ",
            {
                "M3U_LAN_HOST": "10.0.0.22",
                "M3U_EXTERNAL_PORT": "1000",
                "M3U_HDHR_DEVICE_ID": "10500009",
                "M3U_HDHR_TUNER_COUNT": "4",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_discover_json_exposes_local_http_identity(self):
        response = self.client.get("/discover.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["DeviceID"], "10500009")
        self.assertEqual(payload["BaseURL"], "http://10.0.0.22:1000")
        self.assertEqual(payload["LineupURL"], "http://10.0.0.22:1000/lineup.json")
        self.assertEqual(payload["TunerCount"], 4)
        self.assertNotIn("DeviceAuth", payload)

    def test_lineup_uses_local_auto_urls_and_hides_provider_target(self):
        provider_url = "http://provider.test/user/pass/live.ts"
        curated = [{
            "number": 7,
            "name": "Test Channel",
            "play_url": "/guide/play/manual/opaque-token",
        }]
        with patch("core.curated_channels_for_guide", return_value=curated):
            response = self.client.get("/lineup.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload, [{
            "GuideNumber": "7",
            "GuideName": "Test Channel",
            "URL": "http://10.0.0.22:1000/auto/v7",
        }])
        self.assertNotIn(provider_url, response.get_data(as_text=True))

    def test_lineup_status_disables_physical_scan(self):
        response = self.client.get("/lineup_status.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["ScanInProgress"], 0)
        self.assertEqual(payload["ScanPossible"], 0)
        self.assertEqual(payload["SourceList"], ["Antenna"])

    def test_device_xml_has_matching_identity(self):
        response = self.client.get("/device.xml")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("HDHomeRun CONNECT", text)
        self.assertIn("10500009", text)
        self.assertIn("http://10.0.0.22:1000/", text)

    def test_unknown_tuner_channel_returns_hdhr_error(self):
        with patch("core.curated_channels_for_guide", return_value=[]):
            response = self.client.get("/auto/v9999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("X-HDHomeRun-Error"), "801 Unknown Channel")

    def test_head_probe_does_not_acquire_tuner(self):
        curated = [{
            "number": 7,
            "name": "Test Channel",
            "play_url": "/guide/play/manual/opaque-token",
        }]
        with patch("core.curated_channels_for_guide", return_value=curated), \
             patch("playback.targets.resolve_play_target", return_value="http://provider.test/live.ts"), \
             patch("api.hdhomerun.resolve_play_target", return_value="http://provider.test/live.ts"), \
             patch("api.hdhomerun.hdhomerun.TUNERS.acquire") as acquire:
            response = self.client.head("/auto/v7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Content-Type"), "video/mp2t")
        acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
