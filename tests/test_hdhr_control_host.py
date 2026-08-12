from __future__ import annotations

import unittest

from tools.hdhr_control_host import (
    DEFAULT_HWMODEL,
    DEFAULT_MODEL,
    DEFAULT_VERSION,
    TAG_ERROR_MESSAGE,
    TAG_GETSET_NAME,
    TAG_GETSET_VALUE,
    TYPE_GETSET_REQ,
    TYPE_GETSET_RPY,
    ControlState,
    _control_reply,
    _control_request,
)
from tools.hdhr_discovery_host import _open_frame, _parse_tlvs, _seal_frame, _tlv


class HdHomeRunControlHostTests(unittest.TestCase):
    def _get_request(self, name: str) -> bytes:
        payload = _tlv(TAG_GETSET_NAME, name.encode("utf-8") + b"\x00")
        return _seal_frame(TYPE_GETSET_REQ, payload)

    def _set_request(self, name: str, value: str) -> bytes:
        payload = b"".join(
            (
                _tlv(TAG_GETSET_NAME, name.encode("utf-8") + b"\x00"),
                _tlv(TAG_GETSET_VALUE, value.encode("utf-8") + b"\x00"),
            )
        )
        return _seal_frame(TYPE_GETSET_REQ, payload)

    def test_get_request_parses_null_terminated_name(self):
        name, value, lockkey = _control_request(self._get_request("/sys/model"))
        self.assertEqual(name, "/sys/model")
        self.assertIsNone(value)
        self.assertIsNone(lockkey)

    def test_set_request_parses_value(self):
        name, value, _ = _control_request(
            self._set_request("/tuner0/channel", "none")
        )
        self.assertEqual(name, "/tuner0/channel")
        self.assertEqual(value, "none")

    def test_success_reply_is_native_getset_reply(self):
        packet = _control_reply("/sys/model", value=DEFAULT_MODEL)
        frame_type, payload = _open_frame(packet)
        self.assertEqual(frame_type, TYPE_GETSET_RPY)
        values = {tag: raw for tag, raw in _parse_tlvs(payload)}
        self.assertEqual(values[TAG_GETSET_NAME], b"/sys/model\x00")
        self.assertEqual(values[TAG_GETSET_VALUE], DEFAULT_MODEL.encode() + b"\x00")

    def test_error_reply_uses_error_message_tag(self):
        packet = _control_reply("/no/such/variable", error="unknown getset variable")
        frame_type, payload = _open_frame(packet)
        self.assertEqual(frame_type, TYPE_GETSET_RPY)
        values = {tag: raw for tag, raw in _parse_tlvs(payload)}
        self.assertEqual(
            values[TAG_ERROR_MESSAGE],
            b"unknown getset variable\x00",
        )

    def test_system_identity_matches_http_personality(self):
        state = ControlState(2)
        self.assertEqual(state.get("/sys/model"), DEFAULT_MODEL)
        self.assertEqual(state.get("/sys/hwmodel"), DEFAULT_HWMODEL)
        self.assertEqual(state.get("/sys/version"), DEFAULT_VERSION)

    def test_idle_tuner_status_is_available(self):
        state = ControlState(2)
        self.assertEqual(
            state.get("/tuner0/status"),
            "ch=none lock=none ss=0 snq=0 seq=0 bps=0 pps=0",
        )
        self.assertEqual(state.get("/tuner1/channel"), "none")
        self.assertIsNone(state.get("/tuner2/status"))

    def test_writable_tuner_values_round_trip_in_memory(self):
        state = ControlState(2)
        self.assertEqual(state.set("/tuner0/channelmap", "us-cable"), "us-cable")
        self.assertEqual(state.get("/tuner0/channelmap"), "us-cable")

    def test_unknown_variable_is_not_fabricated(self):
        state = ControlState(2)
        self.assertIsNone(state.get("/sys/made-up"))
        self.assertIsNone(state.set("/sys/made-up", "wat"))


if __name__ == "__main__":
    unittest.main()
