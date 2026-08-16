# Installer packaging

M3U Web Picker ships two packaged host-runtime installers:

- `windows-python/` — Windows Python/Waitress installer, packaged as a single EXE with PyInstaller.
- `macos/` — macOS user-scoped host installer packaged as a zip containing install/uninstall `.command` files.

GitHub packaging is defined in `.github/workflows/package-installers.yml`. The workflow is intentionally **not** push-triggered. It runs only when started manually or when a GitHub Release is published.

On a release, the workflow attaches:

```text
M3U-Web-Picker-Windows-Setup.exe
M3U-Web-Picker-macOS.zip
```

Linux does not have a packaged installer. Use the source/Docker runtime and handle the host details yourself.
