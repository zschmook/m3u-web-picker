# M3U Web Picker installer downloads

Packaged Docker installers are produced by `.github/workflows/package-installers.yml` from the shared implementation in `installer/docker/install.py`.

## Latest public installers

- Windows: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Windows-Setup.exe
- macOS: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-macOS.dmg
- Linux: https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Linux.tar.gz

These URLs point at installer assets on the current latest GitHub Release.

Every push to `main` starts **Release main**. The workflow publishes the versioned public image (`ghcr.io/zschmook/m3u-web-picker:vN`) and updates `latest`, then dispatches installer packaging for that tag. Installers can take a few additional minutes to appear. Releases also include `docker-compose.release.yml`, GPU/discovery overrides, and `.env.example` for running without an installer.

## Build or publish

Open **Actions → Package installers → Run workflow**.

- Leave the release tag blank to create temporary Actions artifacts retained for 30 days.
- Enter a tag to create or update that GitHub Release and attach all three installers.

The target machine must already have a running Docker-compatible Linux container engine and Docker Compose v2. The installers do not install Docker, alter virtualization settings, or replace the container engine.
