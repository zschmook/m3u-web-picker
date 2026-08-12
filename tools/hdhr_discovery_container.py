#!/usr/bin/env python3
"""HDHomeRun UDP discovery responder for Docker Desktop host networking.

This is the container-owned counterpart to ``hdhr_discovery_host.py``.  It is
intentionally limited to native HDHomeRun discovery on UDP 65001: Jellyfin uses
that exchange to find the tuner, then follows up over HTTP on the host's bare
port 80.  The main application remains in the normal bridged container.

Docker Desktop host networking is used so this small responder can bind the
host's UDP 65001 without relying on published UDP broadcast forwarding.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
import time

from hdhr_discovery_host import (
    DEFAULT_DEVICE_AUTH,
    DEFAULT_DEVICE_ID,
    DEFAULT_EXTERNAL_PORT,
    DEFAULT_TUNER_COUNT,
    DISCOVERY_PORT,
    MAX_PACKET_SIZE,
    SUPPORT_STATE_POLL_INTERVAL,
    _parse_device_id,
    _remote_support_enabled,
    _reply,
    _request_matches,
)


def _private_ipv4(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4 or not (address.is_private or address.is_link_local):
        raise ValueError(f"{value!r} is not a private/link-local IPv4 address")
    return str(address)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Answer HDHomeRun discovery from a Docker host-network service"
    )
    parser.add_argument(
        "--lan-host",
        default=os.environ.get("M3U_LAN_HOST", ""),
        help="LAN IPv4 advertised in BaseURL",
    )
    parser.add_argument(
        "--external-port",
        type=int,
        default=int(os.environ.get("M3U_EXTERNAL_PORT", str(DEFAULT_EXTERNAL_PORT))),
        help=f"advertised HTTP facade port (default: {DEFAULT_EXTERNAL_PORT})",
    )
    parser.add_argument(
        "--status-base-url",
        default=os.environ.get(
            "M3U_HDHR_STATUS_BASE_URL",
            f"http://127.0.0.1:{DEFAULT_EXTERNAL_PORT}",
        ),
        help="URL used inside the host-network container to read /api/hdhr/status",
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
        if not args.lan_host:
            raise ValueError("M3U_LAN_HOST/--lan-host is required in container mode")
        lan_host = _private_ipv4(args.lan_host)
        device_id = _parse_device_id(args.device_id)
    except (ValueError, OSError) as exc:
        print(f"HDHomeRun container discovery: {exc}", file=sys.stderr)
        return 2

    if not 1 <= args.external_port <= 65535:
        print("HDHomeRun container discovery: invalid external port", file=sys.stderr)
        return 2
    if not 1 <= args.tuners <= 255:
        print(
            "HDHomeRun container discovery: tuner count must be 1..255",
            file=sys.stderr,
        )
        return 2

    device_auth = str(args.device_auth or "").strip()
    if not device_auth:
        print("HDHomeRun container discovery: device auth must not be empty", file=sys.stderr)
        return 2

    advertised_base_url = f"http://{lan_host}:{args.external_port}"
    status_base_url = str(args.status_base_url or "").rstrip("/")
    response_packet = _reply(
        advertised_base_url,
        device_id,
        args.tuners,
        device_auth,
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as exc:
        sock.close()
        print(f"Could not bind UDP {DISCOVERY_PORT}: {exc}", file=sys.stderr)
        return 1

    print(
        f"HDHomeRun container discovery listening on UDP {DISCOVERY_PORT}; "
        f"advertising {advertised_base_url} as {device_id:08X} "
        f"({args.tuners} tuners).",
        flush=True,
    )
    print(f"Support switch: {status_base_url}/api/hdhr/status", flush=True)

    enabled = False
    next_state_check = 0.0

    try:
        while True:
            now = time.monotonic()
            if now >= next_state_check:
                current = _remote_support_enabled(status_base_url)
                if current is not None and current != enabled:
                    enabled = current
                    state = "enabled; LAN discovery is advertising" if enabled else "disabled; LAN discovery is quiet"
                    print(f"HDHomeRun support {state}.", flush=True)
                next_state_check = now + SUPPORT_STATE_POLL_INTERVAL

            try:
                data, remote = sock.recvfrom(MAX_PACKET_SIZE)
            except socket.timeout:
                continue

            if not enabled:
                continue

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

            sock.sendto(response_packet, remote)
            print(f"hdhr answered {remote_host}:{remote[1]}", flush=True)
    except KeyboardInterrupt:
        print("\nHDHomeRun container discovery stopped.")
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
