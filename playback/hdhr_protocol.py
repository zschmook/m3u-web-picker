from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


DISCOVER_PORT = 65001
CONTROL_PORT = 65001

TYPE_DISCOVER_REQ = 0x0002
TYPE_DISCOVER_RPY = 0x0003
TYPE_GETSET_REQ = 0x0004
TYPE_GETSET_RPY = 0x0005

TAG_DEVICE_TYPE = 0x01
TAG_DEVICE_ID = 0x02
TAG_GETSET_NAME = 0x03
TAG_GETSET_VALUE = 0x04
TAG_ERROR_MESSAGE = 0x05
TAG_TUNER_COUNT = 0x10
TAG_LINEUP_URL = 0x27
TAG_BASE_URL = 0x2A
TAG_DEVICE_AUTH_STR = 0x2B

DEVICE_TYPE_WILDCARD = 0xFFFFFFFF
DEVICE_TYPE_TUNER = 0x00000001
DEVICE_ID_WILDCARD = 0xFFFFFFFF

_DEVICE_ID_LOOKUP = (0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB, 0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0)


@dataclass(frozen=True)
class Frame:
    packet_type: int
    payload: bytes
    tlvs: tuple[tuple[int, bytes], ...]


def validate_device_id(device_id: int) -> bool:
    value = int(device_id) & 0xFFFFFFFF
    checksum = 0
    for index, shift in enumerate(range(28, -1, -4)):
        nibble = (value >> shift) & 0x0F
        checksum ^= _DEVICE_ID_LOOKUP[nibble] if index % 2 == 0 else nibble
    return checksum == 0


def parse_device_id(value: str | int) -> int:
    if isinstance(value, int):
        device_id = value
    else:
        text = str(value or "").strip().upper()
        if text.startswith("0X"):
            text = text[2:]
        if len(text) != 8 or any(ch not in "0123456789ABCDEF" for ch in text):
            raise ValueError("HDHomeRun device ID must be exactly 8 hexadecimal digits.")
        device_id = int(text, 16)
    if device_id in {0, DEVICE_ID_WILDCARD} or not validate_device_id(device_id):
        raise ValueError("HDHomeRun device ID fails the SiliconDust self-check.")
    return device_id


def device_id_text(value: str | int) -> str:
    return f"{parse_device_id(value):08X}"


def encode_var_length(length: int) -> bytes:
    length = int(length)
    if length < 0 or length > 0x7FFF:
        raise ValueError("TLV is too large.")
    if length <= 127:
        return bytes((length,))
    return bytes(((length & 0x7F) | 0x80, (length >> 7) & 0xFF))


def decode_var_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("Missing TLV length.")
    first = data[offset]
    offset += 1
    if not (first & 0x80):
        return first, offset
    if offset >= len(data):
        raise ValueError("Missing second TLV length byte.")
    return (first & 0x7F) | (data[offset] << 7), offset + 1


def tlv(tag: int, value: bytes) -> bytes:
    payload = bytes(value)
    return bytes((int(tag) & 0xFF,)) + encode_var_length(len(payload)) + payload


def tlv_u32(tag: int, value: int) -> bytes:
    return tlv(tag, struct.pack(">I", int(value) & 0xFFFFFFFF))


def tlv_u8(tag: int, value: int) -> bytes:
    return tlv(tag, bytes((int(value) & 0xFF,)))


def tlv_text(tag: int, value: str, *, nul: bool = False) -> bytes:
    payload = str(value).encode("utf-8")
    if nul:
        payload += b"\x00"
    return tlv(tag, payload)


def parse_tlvs(payload: bytes) -> tuple[tuple[int, bytes], ...]:
    output: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        tag = payload[offset]
        offset += 1
        length, offset = decode_var_length(payload, offset)
        end = offset + length
        if end > len(payload):
            raise ValueError("TLV extends beyond packet payload.")
        output.append((tag, payload[offset:end]))
        offset = end
    return tuple(output)


def seal_frame(packet_type: int, payload: bytes) -> bytes:
    body = struct.pack(">HH", int(packet_type) & 0xFFFF, len(payload)) + bytes(payload)
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack("<I", crc)


def open_frame(data: bytes) -> Frame:
    packet = bytes(data)
    if len(packet) < 8:
        raise ValueError("Packet is too short.")
    packet_type, payload_length = struct.unpack(">HH", packet[:4])
    expected = 4 + payload_length + 4
    if len(packet) != expected:
        raise ValueError("Packet length does not match frame header.")
    body = packet[: 4 + payload_length]
    expected_crc = struct.unpack("<I", packet[-4:])[0]
    if (zlib.crc32(body) & 0xFFFFFFFF) != expected_crc:
        raise ValueError("Packet CRC is invalid.")
    payload = packet[4:-4]
    return Frame(packet_type=packet_type, payload=payload, tlvs=parse_tlvs(payload))


def first_u32(frame: Frame, tag: int) -> int | None:
    for item_tag, value in frame.tlvs:
        if item_tag == tag and len(value) == 4:
            return struct.unpack(">I", value)[0]
    return None


def first_text(frame: Frame, tag: int) -> str | None:
    for item_tag, value in frame.tlvs:
        if item_tag == tag:
            return value.rstrip(b"\x00").decode("utf-8", errors="replace")
    return None


def build_discovery_reply(*, device_id: int, tuner_count: int, base_url: str, device_auth: str = "") -> bytes:
    device_id = parse_device_id(device_id)
    base = str(base_url or "").rstrip("/")
    payload = b"".join((
        tlv_u32(TAG_DEVICE_TYPE, DEVICE_TYPE_TUNER),
        tlv_u32(TAG_DEVICE_ID, device_id),
        tlv_u8(TAG_TUNER_COUNT, max(1, min(int(tuner_count), 255))),
        tlv_text(TAG_BASE_URL, base),
        tlv_text(TAG_LINEUP_URL, f"{base}/lineup.json"),
        tlv_text(TAG_DEVICE_AUTH_STR, device_auth) if device_auth else b"",
    ))
    return seal_frame(TYPE_DISCOVER_RPY, payload)


def discovery_request_matches(frame: Frame, device_id: int) -> bool:
    if frame.packet_type != TYPE_DISCOVER_REQ:
        return False
    requested_type = first_u32(frame, TAG_DEVICE_TYPE)
    if requested_type not in {None, DEVICE_TYPE_WILDCARD, DEVICE_TYPE_TUNER}:
        return False
    requested_id = first_u32(frame, TAG_DEVICE_ID)
    return requested_id in {None, DEVICE_ID_WILDCARD, parse_device_id(device_id)}


def getset_reply_value(name: str, value: str) -> bytes:
    payload = tlv_text(TAG_GETSET_NAME, name, nul=True) + tlv_text(TAG_GETSET_VALUE, value, nul=True)
    return seal_frame(TYPE_GETSET_RPY, payload)


def getset_reply_error(name: str, message: str) -> bytes:
    payload = tlv_text(TAG_GETSET_NAME, name, nul=True) + tlv_text(TAG_ERROR_MESSAGE, message, nul=True)
    return seal_frame(TYPE_GETSET_RPY, payload)
