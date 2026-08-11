from __future__ import annotations

import unittest

from playback import roku


class RokuDiscoveryTests(unittest.TestCase):
    def test_ssdp_response_parses_roku_ecp_identity(self):
        payload = (
            "HTTP/1.1 200 OK\r\n"
            "ST: roku:ecp\r\n"
            "USN: uuid:roku:ecp:1234567890\r\n"
            "LOCATION: http://10.0.0.55:8060/\r\n"
            "\r\n"
        ).encode("ascii")
        headers = roku._parse_ssdp_response(payload)
        self.assertEqual(headers["st"], "roku:ecp")
        self.assertEqual(headers["usn"], "uuid:roku:ecp:1234567890")
        self.assertEqual(roku._host_from_location(headers["location"]), "10.0.0.55")

    def test_ssdp_response_rejects_non_roku_target(self):
        payload = (
            "HTTP/1.1 200 OK\r\n"
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "LOCATION: http://10.0.0.56:8060/\r\n"
            "\r\n"
        ).encode("ascii")
        self.assertEqual(roku._parse_ssdp_response(payload), {})

    def test_device_info_uses_device_id_as_stable_key(self):
        payload = b"""<device-info>
            <user-device-name>Bedroom Roku</user-device-name>
            <model-name>Roku Ultra</model-name>
            <model-number>4802X</model-number>
            <serial-number>SERIAL123</serial-number>
            <device-id>DEVICE456</device-id>
            <software-version>15.0.0</software-version>
        </device-info>"""
        info = roku._parse_device_info(payload)
        self.assertEqual(info["name"], "Bedroom Roku")
        self.assertEqual(info["device_key"], "DEVICE456")
        self.assertEqual(info["serial_number"], "SERIAL123")

    def test_location_rejects_unexpected_ecp_port(self):
        with self.assertRaises(ValueError):
            roku._host_from_location("http://10.0.0.55:9999/")


if __name__ == "__main__":
    unittest.main()
