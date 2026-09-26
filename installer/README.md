# Installer packaging

M3U Web Picker has one Docker installer implementation in `installer/docker/install.py`. It is standard-library Python and is frozen by PyInstaller on each target operating system.

The release workflow produces three downloads:

- `M3U-Web-Picker-Windows-Setup.exe`
- `M3U-Web-Picker-macOS.dmg` (unsigned)
- `M3U-Web-Picker-Linux.tar.gz`

All three downloads install the same Docker edition, use port `9999`, download source without Git, and provide the same `UPGRADE (UP)` and confirmed `CLEAN (CL)` behavior. Docker and Docker Compose v2 must already be installed and running.

Latest public downloads:

- Windows: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Windows-Setup.exe
- macOS: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-macOS.dmg
- Linux: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Linux.tar.gz

GitHub packaging is defined in `.github/workflows/package-installers.yml`. The workflow is intentionally not push-triggered. Run it manually for temporary artifacts, or provide a release tag to attach all three downloads to a release.

The older `windows-python/` and `macos/` directories are host-Python experiments. They remain available for fallback investigation, but they are not the canonical packaged installers.
