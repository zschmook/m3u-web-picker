from __future__ import annotations

import struct
import unittest

from tools.hdhr_discovery_host import (
    DEFAULT_DEVICE_AUTH,
    DEFAULT_DEVICE_ID,
    DEVICE_ID_WILDCARD,
    DEVICE_TYPE_TUNER,
    SSDP_MEDIA_SERVER,
    SSDP_ROOT_DEVICE,
    TAG_BASE_URL,
    TAG_DEVICE_AUTH_STR,
    TAG_DEVICE_ID,
    TAG_DEVICE_TYPE,
    TAG_LINEUP_URL,
    TAG_TUNER_COUNT,
    TYPE_DISCOVER_REQ,
    TYPE_DISCOVER_RPY,
    _device_id_is_valid,
    _open_frame,
    _parse_tlvs,
    _reply,
    _request_matches,
    _seal_frame,
    _ssdp_notify,
    _ssdp_response,
    _ssdp_search_target,
    _tlv,
)


DEVICE_ID = int(DEFAULT_DEVICE_ID, 16)
BASE_URL = "http://10.0.0.22:10000"


class HdHomeRunHostDiscoveryTests(unittest.TestCase):
    def _request(self, device_id: int = DEVICE_ID_WILDCARD) -> bytes:
        payload = b"".join(
            (
                _tlv(TAG_DEVICE_TYPE, struct.pack(">I", DEVICE_TYPE_TUNER)),
                _tlv(TAG_DEVICE_ID, struct.pack(">I", device_id)),
            )
        )
        return _seal_frame(TYPE_DISCOVER_REQ, payload)

    def _ssdp_search(self, st: str = SSDP_ROOT_DEVICE) -> bytes:
        return (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            f"ST: {st}\r\n"
            "\r\n"
        ).encode("ascii")

    def test_default_device_id_has_valid_silicondust_checksum(self):
        self.assertTrue(_device_id_is_valid(DEVICE_ID))
        self.assertFalse(_device_id_is_valid(0x1234ABCD))

    def test_wildcard_discovery_request_matches(self):
        self.assertTrue(_request_matches(self._request(), DEVICE_ID))

    def test_reply_contains_expected_identity_urls_and_auth(self):
        packet = _reply(BASE_URL, DEVICE_ID, 2)
        frame_type, payload = _open_frame(packet)
        self.assertEqual(frame_type, TYPE_DISCOVER_RPY)
        values = {tag: value for tag, value in _parse_tlvs(payload)}
        self.assertEqual(
            struct.unpack(">I", values[TAG_DEVICE_TYPE])[0], DEVICE_TYPE_TUNER
        )
        self.assertEqual(struct.unpack(">I", values[TAG_DEVICE_ID])[0], DEVICE_ID)
        self.assertEqual(values[TAG_TUNER_COUNT], b"\x02")
        self.assertEqual(values[TAG_BASE_URL], BASE_URL.encode("ascii"))
        self.assertEqual(
            values[TAG_LINEUP_URL], f"{BASE_URL}/lineup.json".encode("ascii")
        )
        self.assertEqual(values[TAG_DEVICE_AUTH_STR], DEFAULT_DEVICE_AUTH.encode("ascii"))

    def test_ssdp_root_search_is_recognized(self):
        self.assertEqual(
            _ssdp_search_target(self._ssdp_search()),
            SSDP_ROOT_DEVICE,
        )

    def test_ssdp_all_search_is_recognized(self):
        self.assertEqual(
            _ssdp_search_target(self._ssdp_search("ssdp:all")),
            "ssdp:all",
        )

    def test_ssdp_unrelated_search_is_ignored(self):
        self.assertEqual(
            _ssdp_search_target(
                self._ssdp_search("urn:schemas-upnp-org:service:ContentDirectory:1")
            ),
            "",
        )

    def test_ssdp_response_points_plex_at_device_xml(self):
        response = _ssdp_response(BASE_URL, DEVICE_ID, SSDP_ROOT_DEVICE).decode("ascii")
        self.assertTrue(response.startswith("HTTP/1.1 200 OK\r\n"))
        self.assertIn(f"LOCATION: {BASE_URL}/device.xml\r\n", response)
        self.assertIn(f"ST: {SSDP_ROOT_DEVICE}\r\n", response)
        self.assertIn(
            f"USN: uuid:{DEVICE_ID:08X}::{SSDP_ROOT_DEVICE}\r\n",
            response,
        )

    def test_ssdp_media_server_search_preserves_device_type(self):
        response = _ssdp_response(BASE_URL, DEVICE_ID, SSDP_MEDIA_SERVER).decode("ascii")
        self.assertIn(f"ST: {SSDP_MEDIA_SERVER}\r\n", response)
        self.assertIn(
            f"USN: uuid:{DEVICE_ID:08X}::{SSDP_MEDIA_SERVER}\r\n",
            response,
        )

    def test_ssdp_alive_advertisement_points_at_device_xml(self):
        packet = _ssdp_notify(BASE_URL, DEVICE_ID).decode("ascii")
        self.assertTrue(packet.startswith("NOTIFY * HTTP/1.1\r\n"))
        self.assertIn("NTS: ssdp:alive\r\n", packet)
        self.assertIn(f"LOCATION: {BASE_URL}/device.xml\r\n", packet)
        self.assertIn(f"NT: {SSDP_ROOT_DEVICE}\r\n", packet)


if __name__ == "__main__":
    unittest.main()
