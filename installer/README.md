# Installer packaging

M3U Web Picker ships two packaged host-runtime installers:

- `windows-python/` — Windows Python/Waitress installer, packaged as a single EXE with PyInstaller.
- `macos/` — macOS user-scoped host installer packaged as a DMG containing install/uninstall `.command` files.

Latest public downloads:

- Windows: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Windows-Setup.exe
- macOS: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-macOS.dmg

GitHub packaging is defined in `.github/workflows/package-installers.yml`. The workflow is intentionally **not** push-triggered.

For a manual run, open **Actions → Package installers → Run workflow**. Leave `release_tag` blank for temporary Actions artifacts, or enter a tag such as `v30.0` to create/update a GitHub Release and attach the installers permanently.

Publishing a GitHub Release normally also runs the packaging workflow and attaches:

```text
M3U-Web-Picker-Windows-Setup.exe
M3U-Web-Picker-macOS.dmg
```

Linux does not have a packaged installer. Use the source/Docker runtime and handle the host details yourself.
