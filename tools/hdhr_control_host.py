#!/usr/bin/env python3
"""Minimal native HDHomeRun TCP control service for compatibility testing.

SiliconDust discovery uses UDP 65001, while native device control uses TCP
65001 with the same framed TLV packet format.  Docker Desktop makes the native
LAN side awkward for this experiment, so this helper runs on the Mac host next
to ``hdhr_discovery_host.py``.

The goal is deliberately conservative: answer identity and idle tuner queries,
log every control request, and return a protocol-correct error for unsupported
variables.  It does not pretend to implement RF tuning hardware.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import socket
import socketserver
import struct
import sys
import threading

from tools.hdhr_discovery_host import (
    DISCOVERY_PORT,
    MAX_PACKET_SIZE,
    PacketError,
    _open_frame,
    _parse_tlvs,
    _seal_frame,
    _tlv,
)


CONTROL_PORT = DISCOVERY_PORT
TYPE_GETSET_REQ = 0x0004
TYPE_GETSET_RPY = 0x0005
TAG_GETSET_NAME = 0x03
TAG_GETSET_VALUE = 0x04
TAG_ERROR_MESSAGE = 0x05
TAG_GETSET_LOCKKEY = 0x15

DEFAULT_MODEL = "hdhomeruntc_atsc"
DEFAULT_HWMODEL = "HDTC-2US"
DEFAULT_VERSION = "20150826"
DEFAULT_TUNER_COUNT = 2

_TUNER_VAR_RE = re.compile(r"^/tuner(\d+)/(channel|channelmap|filter|program|target|status|streaminfo|debug|lockkey|vchannel|vstatus)$")
_WRITABLE_TUNER_VARS = {
    "channel",
    "channelmap",
    "filter",
    "program",
    "target",
    "lockkey",
    "vchannel",
}


class ControlRequestError(ValueError):
    pass


def _cstring(value: bytes) -> str:
    return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _control_request(frame: bytes) -> tuple[str, str | None, int | None]:
    frame_type, payload = _open_frame(frame)
    if frame_type != TYPE_GETSET_REQ:
        raise ControlRequestError(f"unexpected packet type 0x{frame_type:04X}")

    name = ""
    value: str | None = None
    lockkey: int | None = None
    for tag, raw in _parse_tlvs(payload):
        if tag == TAG_GETSET_NAME:
            name = _cstring(raw)
        elif tag == TAG_GETSET_VALUE:
            value = _cstring(raw)
        elif tag == TAG_GETSET_LOCKKEY and len(raw) == 4:
            lockkey = struct.unpack(">I", raw)[0]

    if not name:
        raise ControlRequestError("missing get/set name")
    return name, value, lockkey


def _control_reply(name: str, value: str | None = None, error: str | None = None) -> bytes:
    payload = _tlv(TAG_GETSET_NAME, name.encode("utf-8") + b"\x00")
    if error is not None:
        payload += _tlv(TAG_ERROR_MESSAGE, error.encode("utf-8") + b"\x00")
    else:
        payload += _tlv(TAG_GETSET_VALUE, str(value or "").encode("utf-8") + b"\x00")
    return _seal_frame(TYPE_GETSET_RPY, payload)


def _help_text() -> str:
    return "\n".join(
        (
            "/sys/model",
            "/sys/hwmodel",
            "/sys/features",
            "/sys/version",
            "/tuner<n>/channel",
            "/tuner<n>/channelmap",
            "/tuner<n>/filter",
            "/tuner<n>/program",
            "/tuner<n>/target",
            "/tuner<n>/status",
            "/tuner<n>/streaminfo",
            "/tuner<n>/debug",
            "/tuner<n>/lockkey",
            "/tuner<n>/vchannel",
            "/tuner<n>/vstatus",
        )
    )


def _default_tuner_value(kind: str) -> str:
    return {
        "channel": "none",
        "channelmap": "us-bcast",
        "filter": "0x0000-0x1FFF",
        "program": "0",
        "target": "none",
        "status": "ch=none lock=none ss=0 snq=0 seq=0 bps=0 pps=0",
        "streaminfo": "",
        "debug": "tun: ch=none lock=none",
        "lockkey": "none",
        "vchannel": "none",
        "vstatus": "vch=none name=none auth=none cci=none cgms=none",
    }[kind]


class ControlState:
    def __init__(self, tuner_count: int = DEFAULT_TUNER_COUNT):
        self.tuner_count = tuner_count
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> str | None:
        if name == "help":
            return _help_text()
        system_values = {
            "/sys/model": DEFAULT_MODEL,
            "/sys/hwmodel": DEFAULT_HWMODEL,
            "/sys/version": DEFAULT_VERSION,
            "/sys/features": "channelmap:us-bcast us-cable us-hrc us-irc\nmodulation:8vsb qam64 qam256",
            "/sys/copyright": "M3U Web Picker HDHomeRun compatibility layer",
            "/sys/debug": "M3U Web Picker virtual tuner",
        }
        if name in system_values:
            return system_values[name]

        match = _TUNER_VAR_RE.fullmatch(name)
        if not match:
            return None
        tuner = int(match.group(1))
        kind = match.group(2)
        if tuner < 0 or tuner >= self.tuner_count:
            return None
        with self._lock:
            return self._values.get(name, _default_tuner_value(kind))

    def set(self, name: str, value: str) -> str | None:
        match = _TUNER_VAR_RE.fullmatch(name)
        if match:
            tuner = int(match.group(1))
            kind = match.group(2)
            if tuner < 0 or tuner >= self.tuner_count or kind not in _WRITABLE_TUNER_VARS:
                return None
            with self._lock:
                self._values[name] = value
                if kind == "channel" and value == "none":
                    self._values[f"/tuner{tuner}/vchannel"] = "none"
                if kind == "vchannel" and value == "none":
                    self._values[f"/tuner{tuner}/channel"] = "none"
            return value

        # A virtual tuner has nothing to reboot. Acknowledge the command rather
        # than doing anything destructive on the host.
        if name == "/sys/restart":
            return value
        return None


class _ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_class, state: ControlState):
        self.state = state
        super().__init__(server_address, handler_class)


class _ControlHandler(socketserver.BaseRequestHandler):
    def _allowed_remote(self) -> bool:
        try:
            address = ipaddress.ip_address(str(self.client_address[0] or ""))
        except ValueError:
            return False
        return address.version == 4 and (
            address.is_private or address.is_link_local or address.is_loopback
        )

    def _recv_exact(self, length: int) -> bytes | None:
        output = bytearray()
        while len(output) < length:
            chunk = self.request.recv(length - len(output))
            if not chunk:
                return None
            output.extend(chunk)
        return bytes(output)

    def handle(self) -> None:
        if not self._allowed_remote():
            return
        remote = f"{self.client_address[0]}:{self.client_address[1]}"
        self.request.settimeout(5.0)
        while True:
            try:
                header = self._recv_exact(4)
                if header is None:
                    return
                _, payload_length = struct.unpack(">HH", header)
                total_length = 4 + payload_length + 4
                if total_length < 8 or total_length > MAX_PACKET_SIZE:
                    print(f"control {remote} invalid frame length {total_length}", flush=True)
                    return
                rest = self._recv_exact(payload_length + 4)
                if rest is None:
                    return
                frame = header + rest
                name, requested_value, lockkey = _control_request(frame)
            except (OSError, PacketError, ControlRequestError) as exc:
                print(f"control {remote} protocol error: {exc}", flush=True)
                return

            state: ControlState = self.server.state
            if requested_value is None:
                value = state.get(name)
                action = "GET"
            else:
                value = state.set(name, requested_value)
                action = f"SET {requested_value!r}"

            if value is None:
                print(f"control {remote} {action} {name} -> unsupported", flush=True)
                response = _control_reply(name, error="unknown getset variable")
            else:
                lock_suffix = f" lockkey=0x{lockkey:08X}" if lockkey is not None else ""
                print(
                    f"control {remote} {action} {name}{lock_suffix} -> {value!r}",
                    flush=True,
                )
                response = _control_reply(name, value=value)

            try:
                self.request.sendall(response)
            except OSError:
                return


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer native HDHomeRun TCP control queries")
    parser.add_argument(
        "--tuners",
        type=int,
        default=int(os.environ.get("M3U_HDHR_TUNERS", str(DEFAULT_TUNER_COUNT))),
        help=f"virtual tuner count (default: {DEFAULT_TUNER_COUNT})",
    )
    args = parser.parse_args()
    if not 1 <= args.tuners <= 255:
        print("HDHomeRun control: tuner count must be 1..255", file=sys.stderr)
        return 2

    try:
        server = _ControlServer(("0.0.0.0", CONTROL_PORT), _ControlHandler, ControlState(args.tuners))
    except OSError as exc:
        print(f"Could not bind TCP {CONTROL_PORT}: {exc}", file=sys.stderr)
        return 1

    print(
        f"HDHomeRun native control listening on TCP {CONTROL_PORT} "
        f"as {DEFAULT_HWMODEL}/{DEFAULT_MODEL} ({args.tuners} tuners).",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nHDHomeRun native control stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
