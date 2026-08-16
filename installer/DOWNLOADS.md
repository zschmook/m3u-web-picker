# M3U Web Picker installer downloads

Packaged downloads are produced by `.github/workflows/package-installers.yml`.

The workflow is intentionally **manual/release-triggered only**. It does not run on normal pushes.

For a manual build, open **Actions → Package installers → Run workflow**. GitHub will provide downloadable Windows and macOS artifacts on the completed run.

For a public download, publish a GitHub Release. The workflow automatically builds and attaches:

- `M3U-Web-Picker-Windows-Setup.exe`
- `M3U-Web-Picker-macOS.dmg`

The Windows EXE packages the tested host-Python installer. The macOS DMG contains user-scoped install/uninstall command files.
