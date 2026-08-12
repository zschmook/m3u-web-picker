from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import threading
import zlib

from settings import load_settings
from .hdhr import HDHR_DEVICE_ID, HDHR_TUNER_COUNT


log = logging.getLogger(__name__)

HDHR_DISCOVERY_PORT = 65001
HDHR_MAX_PACKET_SIZE = 1460

HDHR_TYPE_DISCOVER_REQ = 0x0002
HDHR_TYPE_DISCOVER_RPY = 0x0003

HDHR_TAG_DEVICE_TYPE = 0x01
HDHR_TAG_DEVICE_ID = 0x02
HDHR_TAG_TUNER_COUNT = 0x10
HDHR_TAG_BASE_URL = 0x2A
HDHR_TAG_MULTI_TYPE = 0x2D

HDHR_DEVICE_TYPE_TUNER = 0x00000001
HDHR_DEVICE_TYPE_WILDCARD = 0xFFFFFFFF
HDHR_DEVICE_ID_WILDCARD = 0xFFFFFFFF


class HdhrPacketError(ValueError):
    pass


def _read_var_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise HdhrPacketError("Missing TLV length.")
    first = data[offset]
    offset += 1
    if not (first & 0x80):
        return first, offset
    if offset >= len(data):
        raise HdhrPacketError("Truncated two-byte TLV length.")
    length = (first & 0x7F) | (data[offset] << 7)
    return length, offset + 1


def _write_var_length(length: int) -> bytes:
    if length < 0 or length > 0x3FFF:
        raise ValueError("TLV value is too large.")
    if length <= 127:
        return bytes((length,))
    return bytes(((length & 0x7F) | 0x80, length >> 7))


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes((tag,)) + _write_var_length(len(value)) + value


def _parse_tlvs(payload: bytes) -> list[tuple[int, bytes]]:
    output: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        tag = payload[offset]
        offset += 1
        length, offset = _read_var_length(payload, offset)
        end = offset + length
        if end > len(payload):
            raise HdhrPacketError("Truncated TLV value.")
        output.append((tag, payload[offset:end]))
        offset = end
    return output


def _open_frame(data: bytes) -> tuple[int, bytes]:
    if len(data) < 8 or len(data) > HDHR_MAX_PACKET_SIZE:
        raise HdhrPacketError("Invalid HDHomeRun packet size.")
    frame_type, payload_length = struct.unpack_from(">HH", data, 0)
    frame_end = 4 + payload_length
    if frame_end + 4 != len(data):
        raise HdhrPacketError("HDHomeRun packet length does not match datagram length.")
    expected_crc = struct.unpack_from("<I", data, frame_end)[0]
    actual_crc = zlib.crc32(data[:frame_end]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise HdhrPacketError("HDHomeRun packet CRC mismatch.")
    return frame_type, data[4:frame_end]


def _seal_frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > HDHR_MAX_PACKET_SIZE - 8:
        raise ValueError("HDHomeRun payload is too large.")
    frame = struct.pack(">HH", frame_type, len(payload)) + payload
    crc = zlib.crc32(frame) & 0xFFFFFFFF
    return frame + struct.pack("<I", crc)


def _request_matches(data: bytes, device_id: int) -> bool:
    try:
        frame_type, payload = _open_frame(data)
        if frame_type != HDHR_TYPE_DISCOVER_REQ:
            return False
        tlvs = _parse_tlvs(payload)
    except HdhrPacketError:
        return False

    device_types: set[int] = set()
    requested_ids: list[int] = []
    for tag, value in tlvs:
        if tag == HDHR_TAG_DEVICE_TYPE and len(value) == 4:
            device_types.add(struct.unpack(">I", value)[0])
        elif tag == HDHR_TAG_MULTI_TYPE and len(value) % 4 == 0:
            for offset in range(0, len(value), 4):
                device_types.add(struct.unpack_from(">I", value, offset)[0])
        elif tag == HDHR_TAG_DEVICE_ID and len(value) == 4:
            requested_ids.append(struct.unpack(">I", value)[0])

    if not device_types:
        return False
    if HDHR_DEVICE_TYPE_TUNER not in device_types and HDHR_DEVICE_TYPE_WILDCARD not in device_types:
        return False
    if requested_ids and not any(value in {device_id, HDHR_DEVICE_ID_WILDCARD} for value in requested_ids):
        return False
    return True


def _discovery_reply(base_url: str, device_id: int) -> bytes:
    payload = b"".join(
        (
            _tlv(HDHR_TAG_DEVICE_TYPE, struct.pack(">I", HDHR_DEVICE_TYPE_TUNER)),
            _tlv(HDHR_TAG_DEVICE_ID, struct.pack(">I", device_id)),
            _tlv(HDHR_TAG_TUNER_COUNT, bytes((HDHR_TUNER_COUNT,))),
            _tlv(HDHR_TAG_BASE_URL, base_url.encode("utf-8")),
        )
    )
    return _seal_frame(HDHR_TYPE_DISCOVER_RPY, payload)


def _is_local_ipv4(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and (address.is_private or address.is_link_local or address.is_loopback)


class HdhrDiscoveryResponder:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        settings = load_settings()
        lan_host = str(settings.lan_host or "").strip()
        if not lan_host:
            log.warning("HDHomeRun discovery disabled: M3U_LAN_HOST is not configured.")
            return False
        try:
            address = ipaddress.ip_address(lan_host)
        except ValueError:
            log.warning("HDHomeRun discovery disabled: M3U_LAN_HOST is not an IPv4 address.")
            return False
        if address.version != 4 or not (address.is_private or address.is_link_local):
            log.warning("HDHomeRun discovery disabled: M3U_LAN_HOST must be a local IPv4 address.")
            return False

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hdhr-discovery",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def _run(self) -> None:
        settings = load_settings()
        base_url = f"http://{settings.lan_host}:{settings.external_port}"
        try:
            device_id = int(HDHR_DEVICE_ID, 16)
        except ValueError:
            log.error("HDHomeRun discovery disabled: DeviceID %r is not hexadecimal.", HDHR_DEVICE_ID)
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._socket = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", HDHR_DISCOVERY_PORT))
            sock.settimeout(0.5)
            reply = _discovery_reply(base_url, device_id)
            log.info("HDHomeRun discovery listening on UDP %s as %s.", HDHR_DISCOVERY_PORT, base_url)

            while not self._stop.is_set():
                try:
                    data, remote = sock.recvfrom(HDHR_MAX_PACKET_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise

                remote_host = str(remote[0] or "")
                if not _is_local_ipv4(remote_host):
                    continue
                if not _request_matches(data, device_id):
                    continue
                try:
                    sock.sendto(reply, remote)
                except OSError:
                    if self._stop.is_set():
                        break
                    log.exception("Could not answer HDHomeRun discovery request from %s.", remote_host)
        except OSError as exc:
            log.warning("HDHomeRun discovery responder could not bind/listen on UDP %s: %s", HDHR_DISCOVERY_PORT, exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._socket = None


_RESPONDER = HdhrDiscoveryResponder()


def start_hdhr_discovery() -> bool:
    return _RESPONDER.start()


def stop_hdhr_discovery() -> None:
    _RESPONDER.stop()
