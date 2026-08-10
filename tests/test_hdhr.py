from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from api.hdhr import (
    HDHR_DEVICE_AUTH,
    HDHR_DEVICE_ID,
    HDHR_FIRMWARE_NAME,
    HDHR_TUNER_COUNT,
    register_hdhr_routes,
)


SAMPLE_CHANNELS = [
    {
        "number": 7,
        "name": "NBC 10",
        "play_url": "/guide/play/manual/manual-token",
    },
    {
        "number": 1000,
        "name": "Phillies vs Nationals",
        "play_url": "/guide/play/sports/1000",
    },
]


class HdHomeRunFacadeTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        register_hdhr_routes(self.app)
        self.client = self.app.test_client()

    def test_discover_advertises_manual_http_facade(self):
        response = self.client.get(
            "/discover.json",
            base_url="http://10.0.0.22:10000",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["DeviceID"], HDHR_DEVICE_ID)
        self.assertEqual(payload["DeviceAuth"], HDHR_DEVICE_AUTH)
        self.assertEqual(payload["FirmwareName"], HDHR_FIRMWARE_NAME)
        self.assertEqual(payload["TunerCount"], HDHR_TUNER_COUNT)
        self.assertEqual(payload["BaseURL"], "http://10.0.0.22:10000")
        self.assertEqual(
            payload["LineupURL"],
            "http://10.0.0.22:10000/lineup.json",
        )

    def test_discover_matches_hdhomerun_cross_origin_behavior(self):
        response = self.client.get(
            "/discover.json",
            base_url="http://10.0.0.22:10000",
            headers={"Origin": "https://app.hdhomerun.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("GET", response.headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("POST", response.headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("Range", response.headers.get("Access-Control-Allow-Headers", ""))

    def test_plex_root_probe_gets_device_xml_without_claiming_root_for_browsers(self):
        plex = self.client.get(
            "/",
            base_url="http://10.0.0.22:10000",
            headers={"User-Agent": "PlexMediaServer/1.0"},
        )
        self.assertEqual(plex.status_code, 200)
        self.assertIn("application/xml", plex.content_type)
        self.assertIn(HDHR_DEVICE_ID, plex.get_data(as_text=True))

        browser = self.client.get(
            "/",
            base_url="http://10.0.0.22:10000",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        self.assertEqual(browser.status_code, 404)

    @patch("api.hdhr.core.curated_channels_for_guide", return_value=SAMPLE_CHANNELS)
    def test_lineup_uses_exact_curated_channel_numbers(self, _curated):
        response = self.client.get(
            "/lineup.json",
            base_url="http://10.0.0.22:10000",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload,
            [
                {
                    "GuideNumber": "7",
                    "GuideName": "NBC 10",
                    "URL": "http://10.0.0.22:10000/auto/v7",
                },
                {
                    "GuideNumber": "1000",
                    "GuideName": "Phillies vs Nationals",
                    "URL": "http://10.0.0.22:10000/auto/v1000",
                },
            ],
        )

    def test_lineup_status_advertises_plex_scan_handshake(self):
        response = self.client.get("/lineup_status.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ScanInProgress": 0,
                "ScanPossible": 1,
                "Source": "Cable",
                "SourceList": ["Cable"],
            },
        )

    def test_lineup_scan_start_and_abort_are_safe_noops(self):
        start = self.client.post("/lineup.post?scan=start")
        self.assertEqual(start.status_code, 200)
        abort = self.client.post("/lineup.post?scan=abort")
        self.assertEqual(abort.status_code, 200)
        invalid = self.client.post("/lineup.post?scan=explode")
        self.assertEqual(invalid.status_code, 400)

    def test_device_xml_has_hdhomerun_identity(self):
        response = self.client.get(
            "/device.xml",
            base_url="http://10.0.0.22:10000",
        )
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("Silicondust", text)
        self.assertIn(HDHR_DEVICE_ID, text)
        self.assertIn("http://10.0.0.22:10000/", text)

    @patch("api.hdhr.core.manual_stream_target", return_value="http://provider/manual")
    @patch("api.hdhr.mpegts.response_for")
    @patch("api.hdhr.core.curated_channels_for_guide", return_value=SAMPLE_CHANNELS)
    def test_manual_stream_resolves_server_side(
        self,
        _curated,
        response_for,
        manual_stream_target,
    ):
        from flask import Response

        response_for.return_value = Response(b"ts", content_type="video/mp2t")
        response = self.client.get("/hdhr/stream/7")
        self.assertEqual(response.status_code, 200)
        manual_stream_target.assert_called_once_with("manual-token")
        response_for.assert_called_once_with("http://provider/manual")

    @patch("api.hdhr.core.manual_stream_target", return_value="http://provider/manual")
    @patch("api.hdhr.mpegts.response_for")
    @patch("api.hdhr.core.curated_channels_for_guide", return_value=SAMPLE_CHANNELS)
    def test_native_auto_stream_alias_resolves_server_side(
        self,
        _curated,
        response_for,
        manual_stream_target,
    ):
        from flask import Response

        response_for.return_value = Response(b"ts", content_type="video/mp2t")
        response = self.client.get("/auto/v7?duration=7200")
        self.assertEqual(response.status_code, 200)
        manual_stream_target.assert_called_once_with("manual-token")
        response_for.assert_called_once_with("http://provider/manual")

    @patch("api.hdhr.sports.generated_stream_target", return_value="http://provider/sports")
    @patch("api.hdhr.mpegts.response_for")
    @patch("api.hdhr.core.curated_channels_for_guide", return_value=SAMPLE_CHANNELS)
    def test_sports_stream_resolves_server_side(
        self,
        _curated,
        response_for,
        generated_stream_target,
    ):
        from flask import Response

        response_for.return_value = Response(b"ts", content_type="video/mp2t")
        response = self.client.get("/hdhr/stream/1000")
        self.assertEqual(response.status_code, 200)
        generated_stream_target.assert_called_once()
        self.assertEqual(generated_stream_target.call_args.args[1], 1000)
        response_for.assert_called_once_with("http://provider/sports")


if __name__ == "__main__":
    unittest.main()
