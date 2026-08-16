# M3U Web Picker installer downloads

Packaged downloads are produced by `.github/workflows/package-installers.yml`.

The workflow is intentionally **manual/release-triggered only**. It does not run on normal pushes.

## Latest public installers

- Windows: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Windows-Setup.exe
- macOS: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-macOS.dmg

These URLs always point at the installer assets on the current latest GitHub Release.

## Manual packaging

Open **Actions → Package installers → Run workflow**.

- Leave `release_tag` blank to build Windows and macOS Actions artifacts only. Those artifacts are retained for 30 days.
- Enter a tag such as `v30.0` to create that GitHub Release if it does not already exist, then upload/replace the packaged installers as permanent release assets.

Publishing a GitHub Release normally also triggers the workflow automatically and attaches:

- `M3U-Web-Picker-Windows-Setup.exe`
- `M3U-Web-Picker-macOS.dmg`

The Windows EXE packages the tested host-Python installer. The macOS DMG contains user-scoped install/uninstall command files.
