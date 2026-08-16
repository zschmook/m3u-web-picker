# Windows bootstrap installer

This is the first Windows installer prototype for M3U Web Picker.

`M3U-Web-Picker-Setup.exe` is a small native bootstrap executable. It does not require a system Git installation and does not modify the user's Git configuration or PATH.

## What it does

1. Detects Docker Desktop / `docker.exe`.
2. If Docker Desktop is missing and `winget` is available, offers to install `Docker.DockerDesktop`.
3. Starts Docker Desktop when installed but not running and waits for the daemon.
4. Downloads the official 64-bit Git for Windows **MinGit 2.55.0.4** archive into the app's private installation directory and verifies its SHA-256 before extraction.
5. Uses that private MinGit to clone or refresh `zschmook/m3u-web-picker` from `main`.
6. Detects a private LAN IPv4 address and writes `M3U_LAN_HOST` into `.env` when the setting is absent. This makes Cast/Roku LAN relay setup automatic for the common case.
7. Runs `docker compose up -d --build`.
8. Creates simple Open/Update helper CMD files and a Desktop URL shortcut.
9. Opens `http://localhost:9999` so a new user lands in the first-run wizard.

The managed checkout lives under `%LOCALAPPDATA%\M3U-Web-Picker\repo`. Private MinGit lives beside it under `%LOCALAPPDATA%\M3U-Web-Picker\mingit`.

## Build

From PowerShell with Go 1.23+ installed:

```powershell
.\build.ps1
```

The result is `dist\M3U-Web-Picker-Setup.exe`.

The executable itself does **not** contain MinGit. It downloads the pinned official release on first install and verifies this SHA-256:

```text
MinGit-2.55.0.4-64-bit.zip
4e03f94c2ffbf70be337e005cee02661c732dbfc81031a078bda9299b9a7d644
```

That keeps the installer small while still making Git completely private to M3U Web Picker.

## Current prototype limitations

- x64 Windows only for this first pass.
- Docker Desktop installation can require UAC, WSL2 setup, sign-out, or reboot depending on the PC.
- This is a bootstrap EXE rather than an MSI/Inno Setup package; uninstall UI and a background updater service are not implemented yet.
- The managed repository is intentionally reset to `origin/main` during installer reruns/updates. User application state remains in the Docker volume, not the repository checkout.
