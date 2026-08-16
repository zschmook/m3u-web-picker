# Bare Windows installer

This branch intentionally removes Docker, WSL, Git, and the Go bootstrapper from the Windows runtime.

The installer itself is written in Python and packaged as a single Windows EXE with PyInstaller. The installed application also runs directly on Python/Waitress on the Windows host.

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

## What the installer does

1. The EXE starts from the Python runtime embedded by PyInstaller, so the installer itself does not require Python to already be installed.
2. Finds Python 3.12 for the application host, or installs `Python.Python.3.12` for the current user with `winget`.
3. Downloads pinned FFmpeg 8.1.2 and verifies the archive SHA-256 before extracting it.
4. Downloads the current source branch directly from GitHub. Git is not required.
5. Creates a private `venv` and installs `requirements.txt`.
6. Writes host runtime paths and the detected LAN IPv4 address to `host.env`.
7. Registers the installed Python launcher in the current user's Windows `Run` key and in Windows Apps/Installed Apps for uninstall.
8. Starts Waitress directly on port 9999 and opens the first-run wizard.

There is no Docker Desktop, WSL, Hyper-V, virtual machine, container volume, system Git, or Go requirement.

## Update and uninstall

The installed EXE supports:

```text
--update
--uninstall
```

Updates stop the Python host, replace only the application source, refresh Python dependencies, and restart it. Runtime data lives outside the source directory and is preserved.

Uninstall removes the app source, private venv, private FFmpeg, startup registration, and desktop shortcut. It asks whether application data/backups should be retained. The system Python 3.12 installation is never removed automatically because another application may also use it.

## Current development ref

The prototype intentionally downloads:

```text
agent/windows-bare-python
```

Switch the source ref/archive URL to `main` when this runtime is ready to merge.

## Build

Build it on Windows with Python 3.12+:

```text
python build.py
```

`build.py` installs/updates PyInstaller and produces:

```text
dist\M3U-Web-Picker-Bare-Setup.exe
```

All installer implementation and build logic in this directory is Python.
