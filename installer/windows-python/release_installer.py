from __future__ import annotations

import installer as _installer

# The proven installer was developed on agent/windows-bare-python. The
# packaged release build lives on main, so point source/update downloads at
# main without changing the tested installer implementation underneath.
_installer.SOURCE_REF = "main"
_installer.SOURCE_ARCHIVE_URL = (
    "https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/main"
)

# Windows will not rename/replace the app directory while the existing host is
# using it. Stop the installed host before the normal install path downloads or
# swaps any application source.
_original_install = _installer.install


def _install_with_host_stopped() -> None:
    if _installer.read_pid() or _installer.app_reachable():
        print("Stopping existing M3U Web Picker host...")
    _installer.stop_host()
    if _installer.app_reachable():
        raise RuntimeError(
            "The existing M3U Web Picker host is still running on port 9999. "
            "Close it and run the installer again."
        )
    _original_install()


_installer.install = _install_with_host_stopped


if __name__ == "__main__":
    raise SystemExit(_installer.main())
