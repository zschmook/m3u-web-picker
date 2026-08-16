from __future__ import annotations

import shutil
import subprocess
import time

import installer as _installer


# The installer branch is the integration/test line for native Windows builds.
# Setup and --update both download application source from this branch.
_installer.SOURCE_REF = "installer"
_installer.SOURCE_ARCHIVE_URL = (
    "https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/installer"
)

PORT = 9999
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
        if fields[3].upper() != "LISTENING" or not fields[1].endswith(suffix):
            continue
        if fields[4].isdigit() and int(fields[4]) > 0:
            pids.add(int(fields[4]))
    return pids


def _stop_host() -> None:
    _original_stop_host()

    # A stale/missing PID file must not leave an old Picker instance holding
    # the application directory open. Only touch listeners on Picker port 9999.
    for pid in sorted(_listener_pids(PORT)):
        print(f"Stopping process {pid} on Picker port {PORT}...")
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
        if not _listener_pids(PORT):
            return
        time.sleep(0.25)

    remaining = sorted(_listener_pids(PORT))
    raise RuntimeError(
        f"Picker port {PORT} is still in use by PID(s): "
        + ", ".join(str(pid) for pid in remaining)
    )


def _unlink_best_effort(path) -> None:
    # Windows can keep a just-terminated process' log handle alive briefly.
    # Logs/env files are not persistent Picker state, so they must never abort
    # an otherwise successful -v style reset.
    for _ in range(20):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.1)
    print(f"Warning: could not remove {path.name}; continuing reinstall.")


def _reset_install_state() -> None:
    # Preview/native Windows reinstall behavior intentionally mirrors
    # `docker compose down -v`: start fresh, but retain the expensive runtime
    # pieces (Python venv and FFmpeg) so repeated QA installs remain quick.
    print("Resetting existing Picker data (-v style)...")
    for directory in (
        _installer.DATA_DIR,
        _installer.BACKUP_DIR,
        _installer.CAST_DIR,
    ):
        shutil.rmtree(directory, ignore_errors=True)

    _unlink_best_effort(_installer.HOST_LOG)
    _unlink_best_effort(_installer.HOST_ENV)


def _install_fresh() -> None:
    if _installer.read_pid() or _listener_pids(PORT):
        print("Stopping existing M3U Web Picker host...")
    _stop_host()
    _reset_install_state()
    _original_install()


_installer.stop_host = _stop_host
_installer.install = _install_fresh


if __name__ == "__main__":
    raise SystemExit(_installer.main())
