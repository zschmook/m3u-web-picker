# Cross-platform Docker installer

`install.py` is the single source for the Windows, macOS, and Linux Docker installers. It uses only the Python standard library. Release builds freeze it into platform-native executables so the target machine does not need Python.

Run a packaged installer with `--credits` to print its bundled Python and PyInstaller license notices without installing or changing any containers. Application dependency and artwork credits are in **Settings → Open Source & Credits**.

All three installers:

- require an installed and running Docker-compatible Linux container engine with Docker Compose v2;
- download the latest published release's configuration without requiring Git;
- pull its matching `ghcr.io/zschmook/m3u-web-picker:vN` image before stopping an existing installation, without a local build or registry login;
- create the configured backup, cache, and recording bind-mount folders on the host before replacing a container, so Docker Desktop does not have to create them under AppData;
- install production on port `9999`;
- install and start the bundled VPN helper, which supervises connection tests and the connection manager without a separate Python installation;
- preserve `.env`, runtime folders, the Docker application-data volume, and DVR recordings during `UPGRADE (UP)`;
- require the full word `CLEAN` before deleting an existing Docker application-data volume;
- keep the separately mounted DVR directory during a clean install;
- open the first-run setup guide after Docker becomes reachable.

Default install locations:

- Windows: `%LOCALAPPDATA%\m3u-web-picker`
- macOS: `~/Library/Application Support/m3u-web-picker`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/m3u-web-picker`

The installer does not install a container engine. Windows and macOS need a VM-backed engine to run the Linux image; Linux can use native Docker Engine. There is no portable Windows `dockerd` substitute for this image because Docker's standalone Windows daemon runs Windows containers only.

## Running a release package

- Windows: double-click `M3U-Web-Picker-Windows-Setup.exe`.
- macOS: open the DMG, then Control-click **Install M3U Web Picker** and choose **Open**. The package is not signed or notarized, so Gatekeeper may require explicit approval in **System Settings → Privacy & Security**.
- Linux: extract the archive, mark `M3U-Web-Picker-Linux-Setup` executable if necessary, and run it from a terminal. `M3U-Web-Picker-Linux-Setup.py` is included as a readable fallback for machines with Python 3.9 or newer.

## Windows: Docker cannot create the runtime folder

If Docker reports `mkdir ...\m3u-web-picker\runtime: Access is denied`, open
PowerShell on the affected PC and create the mount folders before retrying:

```powershell
New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\m3u-web-picker\runtime\backups"
New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\m3u-web-picker\runtime\jellyfin-cache-disabled"
```

Run the installer again and choose **UPGRADE (UP)** to keep the existing setup.
If folder creation itself is denied, that PC needs its folder permissions or
chosen installation location checked. The installer prepares resolved bind-mount
folders before replacing a container and reports folder preparation failures
before stopping an existing installation.

## Build locally

Build on the target operating system:

```text
python installer/docker/build.py
```

PyInstaller cannot cross-compile. The release workflow builds each artifact on its corresponding operating system. The macOS executable is built as `universal2` and placed in an unsigned DMG.

Every push to `main` runs **Release main**, reserves the next numbered tag, and publishes the image and release after smoke tests and an anonymous image check. Existing installer packaging is then dispatched separately against that exact release tag. Retrying **Release main** reuses its existing tag.

## Bundled VPN helper

The native installer contains **M3U-Web-Picker-VPN-Helper**. It is copied into a versioned directory under `runtime/vpn-helper/bin`, started automatically, and registered for the current user's login:

- Windows: a current-user `Run` entry; the helper and its workers run without console windows.
- macOS: a LaunchAgent with restart after failed exits.
- Linux: a systemd user service, with a desktop-login fallback when a user systemd session is unavailable.

The supervisor waits for Docker/Picker to become available and restarts failed workers. The app uses the helper's test and connection-manager heartbeats to show availability. Installation verifies both heartbeats before opening the guide. User login is required for Windows/macOS and the Linux desktop fallback; an existing Linux user service follows that user's service-manager lifecycle. This does not enable systemd lingering or change Docker's own startup settings.

UP keeps the managed VPN graph and private credential volume, prepares the new executable before interrupting the old installation, and waits for any owned worker operation to finish before moving its runtime. Future switches use the upgraded `.env` image. The helper validates the Compose installation directory and production container before acting; it does not operate on the development instance. The web app does not receive the Docker socket.

Helper logs and ownership state are under `runtime/vpn-helper`. WireGuard keys remain in Gluetun's private Docker volume. Use `--verify-vpn-helper` on the installer to extract and check its bundled helper without installing startup entries or changing Docker. Build jobs run this check on all three platforms.

Startup formats follow the [Windows current-user Run documentation](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys), [Apple LaunchAgent documentation](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html), [systemd service documentation](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml), and [Desktop Entry specification](https://xdg.pages.freedesktop.org/xdg-specs/desktop-entry/latest/exec-variables.html).

GHCR initially creates private packages. For the first image publication, set the `m3u-web-picker` container package to **Public** under **Package settings → Change visibility**, then rerun **Release main**. Future versions inherit that visibility. Releases remain drafts until the anonymous check passes.
