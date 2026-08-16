# Python Dev Windows installer

This installer is for the isolated Windows development build.

- Python host only
- no Docker
- no WSL
- no Git requirement
- port `9998`
- separate install/data directory: `%LOCALAPPDATA%\M3U-Web-Picker-Dev`
- `M3U_DEBUG_TOOLS=true`
- Guide exposes per-channel M3U and MPEG-TS debug links

Each Guide M3U URL returns a one-channel playlist whose stream target is the app's debug MPEG-TS relay. The URL can be opened directly in VLC to verify the selected channel independently of browser/Cast/Roku playback.

The installer downloads source from:

```text
agent/windows-python-dev-m3u
```

It installs or reuses Python 3.12, creates a private virtual environment, installs `requirements.txt`, downloads a private FFmpeg, writes the host environment, starts the app with Waitress, and opens `http://localhost:9998`.

The dev installation is separate from the normal port-9999 install, including its database, backups, FFmpeg, startup registration, and uninstall entry.

## Build

Run on Windows:

```powershell
cd installer\windows-dev
py build.py
```

Output:

```text
dist\M3U-Web-Picker-Python-Dev-Setup.exe
```
