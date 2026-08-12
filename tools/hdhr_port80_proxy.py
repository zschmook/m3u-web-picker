#!/usr/bin/env python3
"""Forward Mac host TCP port 80 to the Docker-hosted HDHomeRun facade.

Jellyfin HDHomeRun auto-discovery currently accepts a UDP 65001 reply, then
constructs ``http://<reply-source-ip>`` instead of honoring the BaseURL/port
advertised in the discovery packet.  The experiments facade normally lives on
host port 10000, so a discovered tuner fails the follow-up /discover.json GET
unless something answers port 80.

This helper is intentionally protocol-agnostic: it forwards raw TCP bytes from
one listen socket to the existing facade.  That keeps HTTP headers, streaming,
and future HDHR endpoints untouched.  Binding TCP 80 on macOS requires root,
so run this helper with sudo.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import socketserver
import sys


DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 80
DEFAULT_TARGET_HOST = "127.0.0.1"
DEFAULT_TARGET_PORT = 10000
BUFFER_SIZE = 64 * 1024


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        target_host = str(getattr(server, "target_host"))
        target_port = int(getattr(server, "target_port"))

        try:
            upstream = socket.create_connection((target_host, target_port), timeout=5.0)
        except OSError as exc:
            print(
                f"port80 proxy: could not connect to {target_host}:{target_port}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return

        client = self.request
        client.setblocking(False)
        upstream.setblocking(False)

        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)

        try:
            while selector.get_map():
                for key, _mask in selector.select(timeout=30.0):
                    source = key.fileobj
                    destination = key.data
                    try:
                        data = source.recv(BUFFER_SIZE)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        data = b""

                    if not data:
                        try:
                            selector.unregister(source)
                        except Exception:
                            pass
                        try:
                            destination.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue

                    view = memoryview(data)
                    while view:
                        try:
                            sent = destination.send(view)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            return
                        view = view[sent:]
        finally:
            selector.close()
            try:
                upstream.close()
            except OSError:
                pass


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[socketserver.BaseRequestHandler],
        *,
        target_host: str,
        target_port: int,
    ) -> None:
        self.target_host = target_host
        self.target_port = target_port
        super().__init__(server_address, handler_class)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forward host TCP port 80 to the experimental HDHomeRun facade"
    )
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--target-host", default=DEFAULT_TARGET_HOST)
    parser.add_argument("--target-port", type=int, default=DEFAULT_TARGET_PORT)
    args = parser.parse_args()

    if not 1 <= args.listen_port <= 65535:
        parser.error("listen port must be 1..65535")
    if not 1 <= args.target_port <= 65535:
        parser.error("target port must be 1..65535")

    try:
        server = _ThreadingTCPServer(
            (args.listen_host, args.listen_port),
            _ProxyHandler,
            target_host=args.target_host,
            target_port=args.target_port,
        )
    except PermissionError:
        print(
            f"Could not bind TCP {args.listen_port}. On macOS, run this helper with sudo.",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"Could not bind TCP {args.listen_port}: {exc}", file=sys.stderr)
        return 1

    print(
        f"HDHomeRun Jellyfin compatibility proxy listening on "
        f"{args.listen_host}:{args.listen_port} -> "
        f"{args.target_host}:{args.target_port}",
        flush=True,
    )
    print("Ctrl-C to stop.", flush=True)

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nHDHomeRun port 80 proxy stopped.", flush=True)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
