# Bare Windows installer

This branch intentionally removes Docker and WSL from the Windows runtime.

`M3U-Web-Picker-Bare-Setup.exe` is a small native bootstrapper. The application itself still runs as normal Python on the Windows host.

## Install layout

Everything managed by M3U Web Picker lives below:

```text
%LOCALAPPDATA%\M3U-Web-Picker
```

The important directories are:

- `app` - downloaded application source;
- `venv` - private Python virtual environment;
- `data` - SQLite database, generated playlists/guides, provider caches, and runtime state;
- `backups` - database backups;
- `cast-hls` - temporary Cast/Roku HLS relay output;
- `ffmpeg` - private FFmpeg executable used by browser/Cast/Roku transcoding.

## What the bootstrapper does

1. Finds Python 3.12, or installs the current `Python.Python.3.12` package for the current user with `winget`.
2. Downloads a pinned private FFmpeg 8.1.2 executable and verifies the archive SHA-256 before extracting it.
3. Downloads the current source branch directly from GitHub. Git is not required.
4. Creates a private `venv` and installs `requirements.txt`.
5. Writes host runtime paths and the detected LAN IPv4 address to `host.env`.
6. Registers the installed launcher in the current user's Windows `Run` key.
7. Starts `host_runtime.py` with `pythonw.exe` and Waitress on port 9999.
8. Opens the first-run wizard.

There is no Docker Desktop, WSL, Hyper-V, virtual machine, container volume, or system Git requirement.

## Update and uninstall

Start Menu helpers call the installed launcher with:

```text
--update
--uninstall
```

Updates stop the host process, replace only the application source, refresh Python dependencies, and restart it. Runtime data lives outside the source directory and is preserved.

Uninstall removes the app source, private venv, private FFmpeg, startup registration, and shortcuts. It asks whether application data/backups should be retained. A system Python installation is never removed automatically because another application may also use it.

## Current development ref

The prototype intentionally downloads:

```text
agent/windows-bare-python
```

Switch the bootstrapper source ref/archive URL to `main` when this runtime is ready to merge.

## Build

From PowerShell with Go 1.23+ installed:

```powershell
.\build.ps1
```

The output is:

```text
dist\M3U-Web-Picker-Bare-Setup.exe
```
