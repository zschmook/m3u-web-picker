from __future__ import annotations

import unittest

from playback.hdhr_protocol import (
    DEVICE_ID_WILDCARD,
    DEVICE_TYPE_TUNER,
    DEVICE_TYPE_WILDCARD,
    TAG_BASE_URL,
    TAG_DEVICE_ID,
    TAG_DEVICE_TYPE,
    TAG_LINEUP_URL,
    TAG_TUNER_COUNT,
    TYPE_DISCOVER_REQ,
    TYPE_DISCOVER_RPY,
    build_discovery_reply,
    discovery_request_matches,
    first_text,
    first_u32,
    open_frame,
    parse_device_id,
    seal_frame,
    tlv_u32,
    validate_device_id,
)


class HdHomeRunProtocolTests(unittest.TestCase):
    def test_compatibility_device_id_passes_silicondust_self_check(self):
        self.assertTrue(validate_device_id(0x10500009))
        self.assertFalse(validate_device_id(0x10500008))
        self.assertEqual(parse_device_id("10500009"), 0x10500009)
        with self.assertRaises(ValueError):
            parse_device_id("10500008")

    def test_frame_round_trip_and_crc_rejection(self):
        packet = seal_frame(TYPE_DISCOVER_REQ, tlv_u32(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER))
        frame = open_frame(packet)
        self.assertEqual(frame.packet_type, TYPE_DISCOVER_REQ)
        self.assertEqual(first_u32(frame, TAG_DEVICE_TYPE), DEVICE_TYPE_TUNER)

        broken = bytearray(packet)
        broken[5] ^= 0x01
        with self.assertRaises(ValueError):
            open_frame(bytes(broken))

    def test_discovery_request_accepts_wildcards_and_exact_id(self):
        device_id = 0x10500009
        wildcard = seal_frame(
            TYPE_DISCOVER_REQ,
            tlv_u32(TAG_DEVICE_TYPE, DEVICE_TYPE_WILDCARD)
            + tlv_u32(TAG_DEVICE_ID, DEVICE_ID_WILDCARD),
        )
        exact = seal_frame(
            TYPE_DISCOVER_REQ,
            tlv_u32(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER)
            + tlv_u32(TAG_DEVICE_ID, device_id),
        )
        wrong = seal_frame(
            TYPE_DISCOVER_REQ,
            tlv_u32(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER)
            + tlv_u32(TAG_DEVICE_ID, 0x10400000),
        )
        self.assertTrue(discovery_request_matches(open_frame(wildcard), device_id))
        self.assertTrue(discovery_request_matches(open_frame(exact), device_id))
        self.assertFalse(discovery_request_matches(open_frame(wrong), device_id))

    def test_discovery_reply_advertises_http_surface(self):
        packet = build_discovery_reply(
            device_id=0x10500009,
            tuner_count=4,
            base_url="http://10.0.0.22:1000",
        )
        frame = open_frame(packet)
        self.assertEqual(frame.packet_type, TYPE_DISCOVER_RPY)
        self.assertEqual(first_u32(frame, TAG_DEVICE_TYPE), DEVICE_TYPE_TUNER)
        self.assertEqual(first_u32(frame, TAG_DEVICE_ID), 0x10500009)
        self.assertEqual(first_text(frame, TAG_BASE_URL), "http://10.0.0.22:1000")
        self.assertEqual(first_text(frame, TAG_LINEUP_URL), "http://10.0.0.22:1000/lineup.json")
        tuner_values = [value for tag, value in frame.tlvs if tag == TAG_TUNER_COUNT]
        self.assertEqual(tuner_values, [b"\x04"])


if __name__ == "__main__":
    unittest.main()
