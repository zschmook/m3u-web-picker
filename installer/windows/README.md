# Windows bootstrap installer

This is the first Windows installer prototype for M3U Web Picker.

`M3U-Web-Picker-Setup.exe` is a small native bootstrap executable. It does not require a system Git installation and does not modify the user's Git configuration or PATH.

## What it does

1. Checks that hardware virtualization is enabled in BIOS/UEFI.
2. Checks WSL 2 before touching Docker Desktop. If WSL is missing, offers to run the official `wsl --install --no-distribution` flow with elevation. If Windows requires a restart, the installer exits cleanly and tells the user to rerun it after reboot.
3. Detects Docker Desktop / `docker.exe`, including current per-user and traditional Program Files install locations.
4. If Docker Desktop is missing and `winget` is available, offers to install `Docker.DockerDesktop`.
5. Starts Docker Desktop when installed but not running and waits for the daemon.
6. Downloads the official 64-bit Git for Windows **MinGit 2.55.0.4** archive into the app's private installation directory and verifies its SHA-256 before extraction.
7. Uses that private MinGit to clone or refresh `zschmook/m3u-web-picker` from `main`.
8. Detects a private LAN IPv4 address while avoiding common Docker/WSL/virtual interfaces and writes `M3U_LAN_HOST` into `.env` when the setting is absent. This makes Cast/Roku LAN relay setup automatic for the common case.
9. Runs `docker compose up -d --build`.
10. Creates simple Open/Update helper CMD files and a Desktop URL shortcut.
11. Opens `http://localhost:9999` so a new user lands in the first-run wizard.

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
- A completely clean Windows installation may need one reboot after WSL 2 is enabled before Docker Desktop can start.
- Docker Desktop installation can require UAC, WSL2 setup, sign-out, or reboot depending on the PC.
- This is a bootstrap EXE rather than an MSI/Inno Setup package; Windows Add/Remove Programs integration and a proper uninstaller are still pending.
- The managed repository is intentionally reset to `origin/main` during installer reruns/updates. User application state remains in the Docker volume, not the repository checkout.
