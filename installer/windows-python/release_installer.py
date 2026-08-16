from __future__ import annotations

import installer as _installer

# The proven installer was developed on agent/windows-bare-python.  The
# packaged release build lives on main, so point source/update downloads at
# main without changing the tested installer implementation underneath.
_installer.SOURCE_REF = "main"
_installer.SOURCE_ARCHIVE_URL = (
    "https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/main"
)


if __name__ == "__main__":
    raise SystemExit(_installer.main())
