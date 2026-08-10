from __future__ import annotations

import struct
import unittest

from tools.hdhr_discovery_host import (
    DEFAULT_DEVICE_ID,
    DEVICE_ID_WILDCARD,
    DEVICE_TYPE_TUNER,
    TAG_BASE_URL,
    TAG_DEVICE_ID,
    TAG_DEVICE_TYPE,
    TAG_TUNER_COUNT,
    TYPE_DISCOVER_REQ,
    TYPE_DISCOVER_RPY,
    _device_id_is_valid,
    _open_frame,
    _parse_tlvs,
    _reply,
    _request_matches,
    _seal_frame,
    _tlv,
)


DEVICE_ID = int(DEFAULT_DEVICE_ID, 16)


class HdHomeRunHostDiscoveryTests(unittest.TestCase):
    def _request(self, device_id: int = DEVICE_ID_WILDCARD) -> bytes:
        payload = b"".join(
            (
                _tlv(TAG_DEVICE_TYPE, struct.pack(">I", DEVICE_TYPE_TUNER)),
                _tlv(TAG_DEVICE_ID, struct.pack(">I", device_id)),
            )
        )
        return _seal_frame(TYPE_DISCOVER_REQ, payload)

    def test_default_device_id_has_valid_silicondust_checksum(self):
        self.assertTrue(_device_id_is_valid(DEVICE_ID))
        self.assertFalse(_device_id_is_valid(0x1234ABCD))

    def test_wildcard_discovery_request_matches(self):
        self.assertTrue(_request_matches(self._request(), DEVICE_ID))

    def test_reply_contains_expected_identity_and_base_url(self):
        packet = _reply("http://10.0.0.22:10000", DEVICE_ID, 2)
        frame_type, payload = _open_frame(packet)
        self.assertEqual(frame_type, TYPE_DISCOVER_RPY)
        values = {tag: value for tag, value in _parse_tlvs(payload)}
        self.assertEqual(struct.unpack(">I", values[TAG_DEVICE_TYPE])[0], DEVICE_TYPE_TUNER)
        self.assertEqual(struct.unpack(">I", values[TAG_DEVICE_ID])[0], DEVICE_ID)
        self.assertEqual(values[TAG_TUNER_COUNT], b"\x02")
        self.assertEqual(values[TAG_BASE_URL], b"http://10.0.0.22:10000")


if __name__ == "__main__":
    unittest.main()
