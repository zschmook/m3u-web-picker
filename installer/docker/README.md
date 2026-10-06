# Cross-platform Docker installer

`install.py` is the single source for the Windows, macOS, and Linux Docker installers. It uses only the Python standard library. Release builds freeze it into platform-native executables so the target machine does not need Python.

All three installers:

- require an installed and running Docker-compatible Linux container engine with Docker Compose v2;
- download the latest published release's configuration without requiring Git;
- pull its matching `ghcr.io/zschmook/m3u-web-picker:vN` image before stopping an existing installation, without a local build or registry login;
- create the configured backup, cache, and recording bind-mount folders on the host before replacing a container, so Docker Desktop does not have to create them under AppData;
- install production on port `9999`;
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

GHCR initially creates private packages. For the first image publication, set the `m3u-web-picker` container package to **Public** under **Package settings → Change visibility**, then rerun **Release main**. Future versions inherit that visibility. Releases remain drafts until the anonymous check passes.
