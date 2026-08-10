from __future__ import annotations

import struct
import unittest

from api.hdhr_discovery import (
    HDHR_DEVICE_ID_WILDCARD,
    HDHR_DEVICE_TYPE_TUNER,
    HDHR_TAG_BASE_URL,
    HDHR_TAG_DEVICE_ID,
    HDHR_TAG_DEVICE_TYPE,
    HDHR_TAG_TUNER_COUNT,
    HDHR_TYPE_DISCOVER_REQ,
    HDHR_TYPE_DISCOVER_RPY,
    HdhrPacketError,
    _discovery_reply,
    _open_frame,
    _parse_tlvs,
    _request_matches,
    _seal_frame,
    _tlv,
)


DEVICE_ID = 0x1234ABCD


class HdHomeRunDiscoveryPacketTests(unittest.TestCase):
    def _request(self, device_id: int | None = None) -> bytes:
        payload = _tlv(HDHR_TAG_DEVICE_TYPE, struct.pack(">I", HDHR_DEVICE_TYPE_TUNER))
        if device_id is not None:
            payload += _tlv(HDHR_TAG_DEVICE_ID, struct.pack(">I", device_id))
        return _seal_frame(HDHR_TYPE_DISCOVER_REQ, payload)

    def test_wildcard_request_without_device_id_matches(self):
        self.assertTrue(_request_matches(self._request(), DEVICE_ID))

    def test_explicit_wildcard_device_id_matches(self):
        self.assertTrue(
            _request_matches(self._request(HDHR_DEVICE_ID_WILDCARD), DEVICE_ID)
        )

    def test_other_device_id_is_ignored(self):
        self.assertFalse(_request_matches(self._request(0xDEADBEEF), DEVICE_ID))

    def test_bad_crc_is_rejected(self):
        packet = bytearray(self._request())
        packet[-1] ^= 0xFF
        self.assertFalse(_request_matches(bytes(packet), DEVICE_ID))
        with self.assertRaises(HdhrPacketError):
            _open_frame(bytes(packet))

    def test_reply_advertises_tuner_identity_and_custom_base_url(self):
        packet = _discovery_reply("http://10.0.0.22:10000", DEVICE_ID)
        frame_type, payload = _open_frame(packet)
        self.assertEqual(frame_type, HDHR_TYPE_DISCOVER_RPY)
        values = {tag: value for tag, value in _parse_tlvs(payload)}
        self.assertEqual(
            struct.unpack(">I", values[HDHR_TAG_DEVICE_TYPE])[0],
            HDHR_DEVICE_TYPE_TUNER,
        )
        self.assertEqual(
            struct.unpack(">I", values[HDHR_TAG_DEVICE_ID])[0],
            DEVICE_ID,
        )
        self.assertEqual(values[HDHR_TAG_TUNER_COUNT], b"\x02")
        self.assertEqual(values[HDHR_TAG_BASE_URL], b"http://10.0.0.22:10000")


if __name__ == "__main__":
    unittest.main()
