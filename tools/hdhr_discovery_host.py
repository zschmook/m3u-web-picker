#!/usr/bin/env python3
"""Native macOS HDHomeRun discovery shim for Docker Desktop experiments.

Docker Desktop can expose UDP ports yet fail to pass LAN broadcast or multicast
service-discovery packets into the container. This stdlib-only helper owns the
HDHomeRun discovery socket on UDP 65001 and the UPnP/SSDP discovery socket on
UDP 1900 on the Mac host, while advertising the normal Docker-hosted HTTP
facade.

The actual tuner facade, lineup, guide, and media streams remain in Docker.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import select
import socket
import struct
import subprocess
import sys
import time
import zlib


DISCOVERY_PORT = 65001
SSDP_PORT = 1900
SSDP_MULTICAST_HOST = "239.255.255.250"
SSDP_MAX_AGE = 1800
SSDP_ALIVE_INTERVAL = 300.0
MAX_PACKET_SIZE = 1460

TYPE_DISCOVER_REQ = 0x0002
TYPE_DISCOVER_RPY = 0x0003

TAG_DEVICE_TYPE = 0x01
TAG_DEVICE_ID = 0x02
TAG_TUNER_COUNT = 0x10
TAG_LINEUP_URL = 0x27
TAG_BASE_URL = 0x2A
TAG_DEVICE_AUTH_STR = 0x2B
TAG_MULTI_TYPE = 0x2D

DEVICE_TYPE_TUNER = 0x00000001
DEVICE_TYPE_WILDCARD = 0xFFFFFFFF
DEVICE_ID_WILDCARD = 0xFFFFFFFF

DEFAULT_DEVICE_ID = "1234ABC2"
DEFAULT_DEVICE_AUTH = "m3u-web-picker"
DEFAULT_TUNER_COUNT = 2
DEFAULT_EXTERNAL_PORT = 10000

SSDP_ROOT_DEVICE = "upnp:rootdevice"
SSDP_MEDIA_SERVER = "urn:schemas-upnp-org:device:MediaServer:1"


class PacketError(ValueError):
    pass


def _read_var_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise PacketError("missing TLV length")
    first = data[offset]
    offset += 1
    if not (first & 0x80):
        return first, offset
    if offset >= len(data):
        raise PacketError("truncated TLV length")
    return (first & 0x7F) | (data[offset] << 7), offset + 1


def _write_var_length(length: int) -> bytes:
    if length < 0 or length > 0x3FFF:
        raise ValueError("TLV value too large")
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
            raise PacketError("truncated TLV value")
        output.append((tag, payload[offset:end]))
        offset = end
    return output


def _open_frame(data: bytes) -> tuple[int, bytes]:
    if len(data) < 8 or len(data) > MAX_PACKET_SIZE:
        raise PacketError("invalid packet size")
    frame_type, payload_length = struct.unpack_from(">HH", data, 0)
    frame_end = 4 + payload_length
    if frame_end + 4 != len(data):
        raise PacketError("packet length mismatch")
    expected_crc = struct.unpack_from("<I", data, frame_end)[0]
    actual_crc = zlib.crc32(data[:frame_end]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise PacketError("CRC mismatch")
    return frame_type, data[4:frame_end]


def _seal_frame(frame_type: int, payload: bytes) -> bytes:
    frame = struct.pack(">HH", frame_type, len(payload)) + payload
    crc = zlib.crc32(frame) & 0xFFFFFFFF
    return frame + struct.pack("<I", crc)


def _device_id_is_valid(device_id: int) -> bool:
    # SiliconDust's published device-ID checksum algorithm.
    lookup = (
        0xA,
        0x5,
        0xF,
        0x6,
        0x7,
        0xC,
        0x1,
        0xB,
        0x9,
        0x2,
        0x8,
        0xD,
        0x4,
        0x3,
        0xE,
        0x0,
    )
    checksum = 0
    checksum ^= lookup[(device_id >> 28) & 0x0F]
    checksum ^= (device_id >> 24) & 0x0F
    checksum ^= lookup[(device_id >> 20) & 0x0F]
    checksum ^= (device_id >> 16) & 0x0F
    checksum ^= lookup[(device_id >> 12) & 0x0F]
    checksum ^= (device_id >> 8) & 0x0F
    checksum ^= lookup[(device_id >> 4) & 0x0F]
    checksum ^= device_id & 0x0F
    return checksum == 0


def _request_matches(data: bytes, device_id: int) -> bool:
    try:
        frame_type, payload = _open_frame(data)
        if frame_type != TYPE_DISCOVER_REQ:
            return False
        tlvs = _parse_tlvs(payload)
    except PacketError:
        return False

    device_types: set[int] = set()
    requested_ids: list[int] = []
    for tag, value in tlvs:
        if tag == TAG_DEVICE_TYPE and len(value) == 4:
            device_types.add(struct.unpack(">I", value)[0])
        elif tag == TAG_MULTI_TYPE and len(value) % 4 == 0:
            for offset in range(0, len(value), 4):
                device_types.add(struct.unpack_from(">I", value, offset)[0])
        elif tag == TAG_DEVICE_ID and len(value) == 4:
            requested_ids.append(struct.unpack(">I", value)[0])

    if not device_types:
        return False
    if DEVICE_TYPE_TUNER not in device_types and DEVICE_TYPE_WILDCARD not in device_types:
        return False
    if requested_ids and not any(
        value in {device_id, DEVICE_ID_WILDCARD} for value in requested_ids
    ):
        return False
    return True


def _reply(
    base_url: str,
    device_id: int,
    tuner_count: int,
    device_auth: str = DEFAULT_DEVICE_AUTH,
) -> bytes:
    lineup_url = f"{base_url}/lineup.json"
    payload = b"".join(
        (
            _tlv(TAG_DEVICE_TYPE, struct.pack(">I", DEVICE_TYPE_TUNER)),
            _tlv(TAG_DEVICE_ID, struct.pack(">I", device_id)),
            _tlv(TAG_TUNER_COUNT, bytes((tuner_count,))),
            _tlv(TAG_BASE_URL, base_url.encode("utf-8")),
            _tlv(TAG_LINEUP_URL, lineup_url.encode("utf-8")),
            _tlv(TAG_DEVICE_AUTH_STR, device_auth.encode("utf-8")),
        )
    )
    return _seal_frame(TYPE_DISCOVER_RPY, payload)


def _ssdp_headers(data: bytes) -> tuple[str, dict[str, str]]:
    try:
        text = data.decode("iso-8859-1", errors="strict")
    except UnicodeDecodeError:
        return "", {}
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines:
        return "", {}
    request_line = lines[0].strip()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip() or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return request_line, headers


def _ssdp_search_target(data: bytes) -> str:
    request_line, headers = _ssdp_headers(data)
    if request_line.upper() != "M-SEARCH * HTTP/1.1":
        return ""
    if headers.get("man", "").lower().strip('"') != "ssdp:discover":
        return ""
    st = headers.get("st", "").strip()
    accepted = {
        "ssdp:all",
        SSDP_ROOT_DEVICE,
        SSDP_MEDIA_SERVER,
    }
    if st.lower().startswith("uuid:"):
        return st
    if st.lower() not in {value.lower() for value in accepted}:
        return ""
    return st


def _ssdp_response(base_url: str, device_id: int, requested_st: str) -> bytes:
    # For an ssdp:all search, advertise the root-device identity. That mirrors
    # the simple Plex-compatible advertisement used by Threadfin while still
    # answering clients that explicitly ask for the MediaServer device type.
    if requested_st.lower() == SSDP_MEDIA_SERVER.lower():
        st = SSDP_MEDIA_SERVER
        usn = f"uuid:{device_id:08X}::{SSDP_MEDIA_SERVER}"
    elif requested_st.lower().startswith("uuid:"):
        st = f"uuid:{device_id:08X}"
        usn = st
    else:
        st = SSDP_ROOT_DEVICE
        usn = f"uuid:{device_id:08X}::{SSDP_ROOT_DEVICE}"
    lines = (
        "HTTP/1.1 200 OK",
        f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}",
        "EXT:",
        f"LOCATION: {base_url}/device.xml",
        "SERVER: M3U-Web-Picker/1.0 UPnP/1.0",
        f"ST: {st}",
        f"USN: {usn}",
        "",
        "",
    )
    return "\r\n".join(lines).encode("ascii")


def _ssdp_notify(base_url: str, device_id: int, nts: str = "ssdp:alive") -> bytes:
    lines = (
        "NOTIFY * HTTP/1.1",
        f"HOST: {SSDP_MULTICAST_HOST}:{SSDP_PORT}",
        f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}",
        f"LOCATION: {base_url}/device.xml",
        f"NT: {SSDP_ROOT_DEVICE}",
        f"NTS: {nts}",
        "SERVER: M3U-Web-Picker/1.0 UPnP/1.0",
        f"USN: uuid:{device_id:08X}::{SSDP_ROOT_DEVICE}",
        "",
        "",
    )
    return "\r\n".join(lines).encode("ascii")


def _private_ipv4(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4 or not (address.is_private or address.is_link_local):
        raise ValueError(f"{value!r} is not a private/link-local IPv4 address")
    return str(address)


def _detect_macos_lan_host() -> str:
    try:
        route = subprocess.run(
            ["route", "-n", "get", "default"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        interface = ""
        for line in route.splitlines():
            if line.strip().startswith("interface:"):
                interface = line.split(":", 1)[1].strip()
                break
        if interface:
            result = subprocess.run(
                ["ipconfig", "getifaddr", interface],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if result:
                return _private_ipv4(result)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return _private_ipv4(sock.getsockname()[0])
    finally:
        sock.close()


def _parse_device_id(text: str) -> int:
    value = int(text, 16)
    if value <= 0 or value > 0xFFFFFFFF:
        raise ValueError("device ID must be an 8-digit hexadecimal value")
    if not _device_id_is_valid(value):
        raise ValueError(f"device ID {value:08X} fails the SiliconDust checksum")
    return value


def _configure_ssdp_socket(lan_host: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lan_host))
    sock.bind(("", SSDP_PORT))
    membership = socket.inet_aton(SSDP_MULTICAST_HOST) + socket.inet_aton(lan_host)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Answer HDHomeRun and Plex SSDP discovery on the Mac host"
    )
    parser.add_argument(
        "--lan-host",
        default=os.environ.get("M3U_LAN_HOST", ""),
        help="LAN IPv4 advertised in BaseURL (default: auto-detect)",
    )
    parser.add_argument(
        "--external-port",
        type=int,
        default=int(
            os.environ.get("M3U_EXTERNAL_PORT", str(DEFAULT_EXTERNAL_PORT))
        ),
        help=f"HTTP facade port (default: {DEFAULT_EXTERNAL_PORT})",
    )
    parser.add_argument(
        "--device-id",
        default=os.environ.get("M3U_HDHR_DEVICE_ID", DEFAULT_DEVICE_ID),
        help=f"checksum-valid HDHomeRun device ID (default: {DEFAULT_DEVICE_ID})",
    )
    parser.add_argument(
        "--device-auth",
        default=os.environ.get("M3U_HDHR_DEVICE_AUTH", DEFAULT_DEVICE_AUTH),
        help=f"HDHomeRun DeviceAuth string (default: {DEFAULT_DEVICE_AUTH})",
    )
    parser.add_argument(
        "--tuners",
        type=int,
        default=int(os.environ.get("M3U_HDHR_TUNERS", str(DEFAULT_TUNER_COUNT))),
        help=f"advertised tuner count (default: {DEFAULT_TUNER_COUNT})",
    )
    args = parser.parse_args()

    try:
        lan_host = (
            _private_ipv4(args.lan_host)
            if args.lan_host
            else _detect_macos_lan_host()
        )
        device_id = _parse_device_id(args.device_id)
    except (ValueError, OSError) as exc:
        print(f"HDHomeRun host discovery: {exc}", file=sys.stderr)
        return 2

    if not 1 <= args.external_port <= 65535:
        print("HDHomeRun host discovery: invalid external port", file=sys.stderr)
        return 2
    if not 1 <= args.tuners <= 255:
        print(
            "HDHomeRun host discovery: tuner count must be 1..255",
            file=sys.stderr,
        )
        return 2
    device_auth = str(args.device_auth or "").strip()
    if not device_auth:
        print("HDHomeRun host discovery: device auth must not be empty", file=sys.stderr)
        return 2

    base_url = f"http://{lan_host}:{args.external_port}"
    hdhr_response = _reply(base_url, device_id, args.tuners, device_auth)

    hdhr_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    hdhr_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    hdhr_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        hdhr_sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as exc:
        print(f"Could not bind UDP {DISCOVERY_PORT}: {exc}", file=sys.stderr)
        return 1

    try:
        ssdp_sock = _configure_ssdp_socket(lan_host)
    except OSError as exc:
        hdhr_sock.close()
        print(f"Could not bind/join SSDP UDP {SSDP_PORT}: {exc}", file=sys.stderr)
        return 1

    print(
        f"HDHomeRun host discovery listening on UDP {DISCOVERY_PORT}; "
        f"advertising {base_url} as {device_id:08X} ({args.tuners} tuners)."
    )
    print(
        f"Plex/UPnP SSDP listening on UDP {SSDP_PORT}; "
        f"LOCATION {base_url}/device.xml"
    )
    print(f"Lineup URL: {base_url}/lineup.json")
    print("Ctrl-C to stop.")

    multicast_target = (SSDP_MULTICAST_HOST, SSDP_PORT)
    alive_packet = _ssdp_notify(base_url, device_id)
    byebye_packet = _ssdp_notify(base_url, device_id, "ssdp:byebye")
    try:
        ssdp_sock.sendto(alive_packet, multicast_target)
    except OSError:
        pass
    next_alive = time.monotonic() + SSDP_ALIVE_INTERVAL

    try:
        while True:
            timeout = max(0.0, min(1.0, next_alive - time.monotonic()))
            readable, _, _ = select.select([hdhr_sock, ssdp_sock], [], [], timeout)
            for ready in readable:
                if ready is hdhr_sock:
                    data, remote = hdhr_sock.recvfrom(MAX_PACKET_SIZE)
                    remote_host = str(remote[0] or "")
                    try:
                        address = ipaddress.ip_address(remote_host)
                    except ValueError:
                        continue
                    if address.version != 4 or not (
                        address.is_private or address.is_link_local or address.is_loopback
                    ):
                        continue
                    if not _request_matches(data, device_id):
                        continue
                    hdhr_sock.sendto(hdhr_response, remote)
                    print(f"hdhr answered {remote_host}:{remote[1]}", flush=True)
                    continue

                data, remote = ssdp_sock.recvfrom(65535)
                remote_host = str(remote[0] or "")
                requested_st = _ssdp_search_target(data)
                if not requested_st:
                    continue
                try:
                    address = ipaddress.ip_address(remote_host)
                except ValueError:
                    continue
                if address.version != 4 or not (
                    address.is_private or address.is_link_local or address.is_loopback
                ):
                    continue
                response = _ssdp_response(base_url, device_id, requested_st)
                ssdp_sock.sendto(response, remote)
                print(
                    f"ssdp answered {remote_host}:{remote[1]} st={requested_st}",
                    flush=True,
                )

            if time.monotonic() >= next_alive:
                try:
                    ssdp_sock.sendto(alive_packet, multicast_target)
                except OSError:
                    pass
                next_alive = time.monotonic() + SSDP_ALIVE_INTERVAL
    except KeyboardInterrupt:
        print("\nHDHomeRun host discovery stopped.")
    finally:
        try:
            ssdp_sock.sendto(byebye_packet, multicast_target)
        except OSError:
            pass
        ssdp_sock.close()
        hdhr_sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
