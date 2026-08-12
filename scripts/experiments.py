#!/usr/bin/env python3
"""Start/stop the experiments stack and its native HDHomeRun discovery helper.

Docker Desktop cannot reliably deliver HDHomeRun LAN broadcasts to a container,
so the tiny UDP discovery responder runs natively on the Docker host. This
manager keeps that detail invisible during normal startup and shutdown.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


ROOT_DIR = Path(__file__).resolve().parent.parent
HELPER = ROOT_DIR / "tools" / "hdhr_discovery_host.py"
RUNTIME_DIR = Path(tempfile.gettempdir())
STATE_FILE = RUNTIME_DIR / "m3u-web-picker-hdhr-discovery.json"
LOG_FILE = RUNTIME_DIR / "m3u-web-picker-hdhr-discovery.log"
DEFAULT_EXTERNAL_PORT = 10000


def _detect_lan_host() -> str:
    configured = str(os.environ.get("M3U_LAN_HOST", "") or "").strip()
    if configured:
        return configured

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect performs route selection without requiring the target to
        # answer, making this work on macOS and Windows without OS-specific tools.
        sock.connect(("192.0.2.1", 9))
        host = str(sock.getsockname()[0] or "").strip()
    finally:
        sock.close()
    if not host or host == "0.0.0.0":
        raise RuntimeError("could not determine LAN IPv4 address; set M3U_LAN_HOST")
    return host


def _external_port() -> int:
    raw = str(os.environ.get("M3U_EXTERNAL_PORT", DEFAULT_EXTERNAL_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid M3U_EXTERNAL_PORT: {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"invalid M3U_EXTERNAL_PORT: {port}")
    return port


def _read_state() -> dict:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(pid: int, lan_host: str, external_port: int) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "pid": int(pid),
                "lan_host": lan_host,
                "external_port": int(external_port),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _state_pid(state: dict) -> int:
    try:
        return int(state.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _stop_helper() -> None:
    state = _read_state()
    pid = _state_pid(state)
    if pid and _process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _process_alive(pid):
            time.sleep(0.05)
        if _process_alive(pid) and os.name != "nt":
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def _recent_log(lines: int = 20) -> str:
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _start_helper(lan_host: str, external_port: int) -> None:
    state = _read_state()
    pid = _state_pid(state)
    same_config = (
        str(state.get("lan_host", "")) == lan_host
        and int(state.get("external_port", 0) or 0) == external_port
    )
    if pid and _process_alive(pid) and same_config:
        return
    if pid and _process_alive(pid):
        _stop_helper()
    else:
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass

    env = os.environ.copy()
    env["M3U_LAN_HOST"] = lan_host
    env["M3U_EXTERNAL_PORT"] = str(external_port)

    popen_kwargs: dict = {
        "cwd": str(ROOT_DIR),
        "env": env,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        popen_kwargs["start_new_session"] = True

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                str(HELPER),
                "--lan-host",
                lan_host,
                "--external-port",
                str(external_port),
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )

    _write_state(process.pid, lan_host, external_port)
    time.sleep(0.3)
    if process.poll() is not None:
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass
        detail = _recent_log()
        suffix = f"\n{detail}" if detail else ""
        raise RuntimeError(f"HDHomeRun discovery helper failed to start{suffix}")


def _compose(*args: str, env: dict | None = None) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT_DIR,
        env=env,
        check=True,
    )


def start() -> None:
    lan_host = _detect_lan_host()
    external_port = _external_port()
    env = os.environ.copy()
    env["M3U_LAN_HOST"] = lan_host
    env["M3U_EXTERNAL_PORT"] = str(external_port)

    # Remove the old failed containerized discovery experiment if it exists.
    _compose("up", "-d", "--build", "--remove-orphans", env=env)
    _start_helper(lan_host, external_port)


def stop() -> None:
    _stop_helper()
    _compose("down", "--remove-orphans")


def status() -> None:
    state = _read_state()
    pid = _state_pid(state)
    helper = "running" if pid and _process_alive(pid) else "stopped"
    print(f"HDHomeRun discovery helper: {helper}")
    if helper == "running":
        print(
            f"  pid={pid} lan={state.get('lan_host')} "
            f"port={state.get('external_port')}"
        )
    _compose("ps")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the v30 experiments stack")
    parser.add_argument("command", choices=("start", "stop", "status"))
    args = parser.parse_args()

    try:
        if args.command == "start":
            start()
        elif args.command == "stop":
            stop()
        else:
            status()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"experiments: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
