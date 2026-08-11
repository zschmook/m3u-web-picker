#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import struct
import sys
import threading


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from playback.hdhr_protocol import (  # noqa: E402
    CONTROL_PORT,
    DISCOVER_PORT,
    TAG_GETSET_NAME,
    TAG_GETSET_VALUE,
    TYPE_GETSET_REQ,
    build_discovery_reply,
    device_id_text,
    discovery_request_matches,
    first_text,
    getset_reply_error,
    getset_reply_value,
    open_frame,
    parse_device_id,
    requested_device_types,
)


DEFAULT_DEVICE_ID = "10500009"


def _control_value(name: str, *, model: str, tuner_count: int) -> str | None:
    if name == "/sys/model":
        return "hdhomerun5_atsc"
    if name == "/sys/hwmodel":
        return model
    if name == "/sys/version":
        return "20260810"
    if name == "/sys/copyright":
        return "HDHomeRun protocol compatibility endpoint"
    if name == "/sys/features":
        return "channelmap: us-bcast us-cable"
    if name.startswith("/tuner"):
        path = name.lstrip("/")
        head, _, variable = path.partition("/")
        try:
            tuner = int(head.removeprefix("tuner"))
        except ValueError:
            return None
        if tuner < 0 or tuner >= tuner_count:
            return None
        if variable == "status":
            return "ch=none lock=none ss=0 snq=0 seq=0 bps=0 pps=0"
        if variable in {"channel", "vchannel", "target", "filter", "program", "lockkey"}:
            return "none"
    return None


def _frame_length(buffer: bytes) -> int | None:
    if len(buffer) < 4:
        return None
    payload_length = struct.unpack(">H", buffer[2:4])[0]
    return 4 + payload_length + 4


def serve_udp(*, bind: str, device_id: int, tuner_count: int, base_url: str, device_auth: str) -> None:
    reply = build_discovery_reply(
        device_id=device_id,
        tuner_count=tuner_count,
        base_url=base_url,
        device_auth=device_auth,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind, DISCOVER_PORT))
    print(f"[hdhr] UDP discovery listening on {bind}:{DISCOVER_PORT}", flush=True)

    while True:
        data, remote = sock.recvfrom(2048)
        try:
            frame = open_frame(data)
            if not discovery_request_matches(frame, device_id):
                continue
        except ValueError:
            continue
        requested = ",".join(f"0x{value:08X}" for value in requested_device_types(frame)) or "unspecified"
        print(
            f"[hdhr] discovery from {remote[0]}:{remote[1]} types={requested} -> {base_url}",
            flush=True,
        )
        sock.sendto(reply, remote)


def _handle_control_connection(
    conn: socket.socket,
    remote: tuple[str, int],
    *,
    model: str,
    tuner_count: int,
) -> None:
    buffer = b""
    conn.settimeout(5.0)
    with conn:
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                return
            if not chunk:
                return
            buffer += chunk

            while True:
                length = _frame_length(buffer)
                if length is None or len(buffer) < length:
                    break
                packet, buffer = buffer[:length], buffer[length:]
                try:
                    frame = open_frame(packet)
                except ValueError:
                    return
                if frame.packet_type != TYPE_GETSET_REQ:
                    print(f"[hdhr] unsupported control frame from {remote[0]}", flush=True)
                    conn.sendall(getset_reply_error("", "unsupported request"))
                    continue

                name = first_text(frame, TAG_GETSET_NAME) or ""
                requested_value = first_text(frame, TAG_GETSET_VALUE)
                value = _control_value(name, model=model, tuner_count=tuner_count)
                if value is None:
                    print(f"[hdhr] control {remote[0]} GETSET {name or '<missing>'} -> unknown", flush=True)
                    conn.sendall(getset_reply_error(name, "unknown variable"))
                    continue
                result = requested_value if requested_value is not None else value
                verb = "SET" if requested_value is not None else "GET"
                print(f"[hdhr] control {remote[0]} {verb} {name} -> {result}", flush=True)
                conn.sendall(getset_reply_value(name, result))


def serve_tcp(*, bind: str, model: str, tuner_count: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, CONTROL_PORT))
    sock.listen(16)
    print(f"[hdhr] TCP control listening on {bind}:{CONTROL_PORT}", flush=True)
    while True:
        conn, remote = sock.accept()
        thread = threading.Thread(
            target=_handle_control_connection,
            args=(conn, remote),
            kwargs={"model": model, "tuner_count": tuner_count},
            daemon=True,
        )
        thread.start()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Host-side HDHomeRun discovery/control compatibility daemon."
    )
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "M3U_HDHR_BASE_URL",
            f"http://{os.environ.get('M3U_LAN_HOST', '10.0.0.22')}:{os.environ.get('M3U_EXTERNAL_PORT', '1000')}",
        ),
    )
    parser.add_argument(
        "--device-id",
        default=os.environ.get("M3U_HDHR_DEVICE_ID", DEFAULT_DEVICE_ID),
    )
    parser.add_argument(
        "--device-auth",
        default=os.environ.get("M3U_HDHR_DEVICE_AUTH", ""),
        help="Optional legitimate DeviceAuth. Empty by default; no auth is fabricated.",
    )
    parser.add_argument(
        "--tuners",
        type=int,
        default=int(os.environ.get("M3U_HDHR_TUNER_COUNT", "4")),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("M3U_HDHR_MODEL_NUMBER", "HDHR5-4US"),
    )
    args = parser.parse_args()

    device_id = parse_device_id(args.device_id)
    tuner_count = max(1, min(int(args.tuners), 8))
    base_url = str(args.base_url).rstrip("/")

    print(
        f"[hdhr] pretending very seriously to be {device_id_text(device_id)} "
        f"({tuner_count} tuners) at {base_url}",
        flush=True,
    )
    if not args.device_auth:
        print("[hdhr] DeviceAuth is intentionally empty; local protocol test only.", flush=True)

    tcp = threading.Thread(
        target=serve_tcp,
        kwargs={"bind": args.bind, "model": args.model, "tuner_count": tuner_count},
        daemon=True,
    )
    tcp.start()
    serve_udp(
        bind=args.bind,
        device_id=device_id,
        tuner_count=tuner_count,
        base_url=base_url,
        device_auth=args.device_auth,
    )


if __name__ == "__main__":
    main()
