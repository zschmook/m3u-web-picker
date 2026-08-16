from __future__ import annotations

import shutil
import subprocess
import time

import installer as _installer


_original_stop_host = _installer.stop_host
_original_install = _installer.install


def _listener_pids(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["netstat.exe", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_installer.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    pids: set[int] = set()
    suffix = f":{port}"
    for raw_line in result.stdout.splitlines():
        fields = raw_line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        local_address = fields[1]
        state = fields[3].upper()
        pid_text = fields[4]
        if state != "LISTENING" or not local_address.endswith(suffix):
            continue
        if pid_text.isdigit():
            pid = int(pid_text)
            if pid > 0:
                pids.add(pid)
    return pids


def _stop_dev_host() -> None:
    # First use the installer's normal PID-file shutdown path.
    _original_stop_host()

    # If a stale/missing PID file left the old dev host alive, kill the actual
    # process listening on the dev port. This is intentionally scoped to 9998.
    for pid in sorted(_listener_pids(_installer.PORT)):
        print(f"Stopping process {pid} on dev port {_installer.PORT}...")
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=_installer.CREATE_NO_WINDOW,
        )

    _installer.HOST_PID.unlink(missing_ok=True)
    deadline = time.time() + 15
    while time.time() < deadline:
        if not _listener_pids(_installer.PORT):
            return
        time.sleep(0.25)

    remaining = sorted(_listener_pids(_installer.PORT))
    raise RuntimeError(
        f"Dev port {_installer.PORT} is still in use by PID(s): "
        + ", ".join(str(pid) for pid in remaining)
    )


def _reset_dev_state() -> None:
    # Preview installer behavior intentionally mirrors `docker compose down -v`:
    # reinstalling starts the app with fresh state while retaining expensive
    # runtime dependencies (Python venv and FFmpeg).
    print("Resetting existing dev data (-v style)...")
    for directory in (
        _installer.DATA_DIR,
        _installer.BACKUP_DIR,
        _installer.CAST_DIR,
    ):
        shutil.rmtree(directory, ignore_errors=True)

    for path in (
        _installer.HOST_LOG,
        _installer.HOST_ENV,
    ):
        path.unlink(missing_ok=True)


def _install_with_host_stopped() -> None:
    if _installer.read_pid() or _listener_pids(_installer.PORT):
        print("Stopping existing M3U Web Picker Dev host...")
    _stop_dev_host()
    _reset_dev_state()
    _original_install()


_installer.stop_host = _stop_dev_host
_installer.install = _install_with_host_stopped


if __name__ == "__main__":
    raise SystemExit(_installer.main())
