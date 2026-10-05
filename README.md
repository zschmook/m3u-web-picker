# M3U Web Picker

Turn a large IPTV catalog into the channels you actually watch. M3U Web Picker combines a curated M3U/XMLTV lineup, sports event channels, a browser TV guide, DVR, and Roku/Cast playback. It can also build continuous TV and movie channels from an accessible Plex library.

The app runs in Docker on Windows, macOS, and Linux. Start with a [packaged installer](#install-recommended) or the [published Compose configuration](#install-with-docker-compose). Git and a local image build are only needed for development.

## What it does

- **Channels and guide:** load an M3U URL/file or Xtream provider, choose and reorder channels, and combine provider XMLTV with optional public guide sources.
- **Sports Automation:** follow teams and leagues, generate temporary event channels, and try ordered fallback providers when the primary provider has no usable feed.
- **Browser playback:** watch live TV, use Picture-in-Picture, or listen to an audio-only stream. Android phones can hand playback to VLC.
- **DVR:** record a program or series, browse completed recordings, and optionally remove commercials and convert recordings to H.265/MKV.
- **TV devices:** browse the native Roku guide, send playback from the browser to Roku or Google Cast, or use the virtual HDHomeRun interface with compatible clients.
- **Plex custom channels — experimental:** create continuous show channels, movie genre channels, and optional nostalgia commercial breaks from your own media.
- **Movie lighting — experimental:** set playing and paused brightness for a selected Roku and an identified compatible light.

## Install (recommended)

1. Install and start **Docker Desktop** on Windows/macOS, or **Docker Engine with Compose v2** on Linux. Docker Desktop must use Linux containers.
2. Download the installer for your computer from the [latest release](https://github.com/zschmook/m3u-web-picker/releases/latest):

   | System | Download |
   | --- | --- |
   | Windows | [M3U-Web-Picker-Windows-Setup.exe](https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Windows-Setup.exe) |
   | macOS | [M3U-Web-Picker-macOS.dmg](https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-macOS.dmg) |
   | Linux | [M3U-Web-Picker-Linux.tar.gz](https://github.com/zschmook/m3u-web-picker/releases/latest/download/M3U-Web-Picker-Linux.tar.gz) |

3. Run the installer. On macOS, open the DMG and Control-click **Install M3U Web Picker → Open**; the package is unsigned. On Linux, extract the archive and run the executable from a terminal; a Python fallback is included.
4. Open [http://localhost:9999](http://localhost:9999) and complete first-run setup.

The installer downloads a published release and pulls its matching Docker image. It detects the host's LAN address, configures persistent storage, and opens setup. You do not need Git, Python, a GitHub login, or a separate host FFmpeg installation. Docker itself must already be installed and running.

For an existing installation, choose **UPGRADE (UP)** to keep settings and recordings. **CLEAN (CL)** requires typing `CLEAN` before deleting saved application data; the separate DVR recording folder is retained. See the [installer documentation](installer/docker/README.md) for install locations and platform details.

## Install with Docker Compose

For a new installation, save [docker-compose.release.yml](https://github.com/zschmook/m3u-web-picker/releases/latest/download/docker-compose.release.yml) in an empty folder. Open a terminal in that folder and run:

```sh
docker compose -f docker-compose.release.yml up -d
```

Open [http://localhost:9999](http://localhost:9999). Compose pulls the image and creates persistent storage automatically.

The public image is **`ghcr.io/zschmook/m3u-web-picker:latest`**. It supports Linux AMD64 and ARM64, including Docker Desktop on Windows and Intel/Apple Silicon Macs. `latest` follows the newest successfully published main release. To pin a release, set `M3U_IMAGE=ghcr.io/zschmook/m3u-web-picker:v33` in `.env`, using the version you want from [Releases](https://github.com/zschmook/m3u-web-picker/releases).

### Docker Desktop

Docker Desktop's Docker Hub search does not list this GitHub-hosted image. A GitHub repository URL cannot be used as an image name. Pull the full registry name from a terminal:

```sh
docker pull ghcr.io/zschmook/m3u-web-picker:latest
```

The image then appears under **Images → Local**. To run it there, choose **Run → Optional settings**, map host port `9999` to container port `9999`, and mount persistent storage at `/app/data`. The installer or Compose configuration is recommended because it also configures DVR and backup storage.

### Network, recording paths, and GPU

For manual Compose installs, create `.env` beside the Compose file before starting. You can download the [environment template](https://github.com/zschmook/m3u-web-picker/releases/latest/download/default.env.example) and save it as `.env`, or add only the settings you need:

```dotenv
M3U_LAN_HOST=192.168.1.25
M3U_DVR_DIR=C:/Users/YourName/Videos/M3U-Web-Picker-DVR
```

Replace the sample address and path with your own. On macOS/Linux, use an absolute recording path such as `/home/yourname/Videos/M3U-Web-Picker-DVR`. The packaged installer sets these values for you.

| Setting | Purpose |
| --- | --- |
| `M3U_LAN_HOST` | Docker host's private IPv4 address, required for advertised Roku/Cast/HDHomeRun media URLs. |
| `M3U_HOST_PORT` / `M3U_EXTERNAL_PORT` | Host port and advertised URL port; both default to `9999` and should match. |
| `M3U_DVR_DIR` | Host recording folder; manual Compose defaults to `./runtime/recordings`. |
| `M3U_BACKUP_DIR` | Host backup folder; defaults to `./runtime/backups`. |
| `M3U_IMAGE` | Published image/version; manual Compose defaults to `latest`, while the installer pins its release. |

Reach the app from another LAN device at `http://<your-host-IP>:9999`. Changing the advertised address in **Settings → Network** does not change Docker's port mapping; port or environment changes require recreating the container and interrupt playback.

For NVIDIA acceleration on a supported Windows/Linux host, download [docker-compose.gpu.yml](https://github.com/zschmook/m3u-web-picker/releases/latest/download/docker-compose.gpu.yml) beside the release Compose file, then use both files:

```sh
docker compose -f docker-compose.release.yml -f docker-compose.gpu.yml up -d
```

The installer requests NVIDIA passthrough when `nvidia-smi` is available. Docker GPU passthrough is not supported on macOS; FFmpeg uses CPU fallback there.

Port `80` is optional. Only add [docker-compose.discovery.yml](https://github.com/zschmook/m3u-web-picker/releases/latest/download/docker-compose.discovery.yml) if an HDHomeRun client requires bare-IP HTTP discovery and that port is free. Normal setup and browser access use port `9999`.

## First-run setup

A fresh application-data volume opens the setup wizard. Existing configured installations keep their saved state and skip it.

1. **Choose a provider:** enter a direct M3U URL or an Xtream base URL with its username and password. The built-in free public demo lets you try the app without an IPTV subscription.
2. **Choose channels:** search/filter the catalog, select channels to keep, arrange their order, and save. **Hide SD / Low Bandwidth Channels** can reduce the catalog.
3. **Choose optional sports rules:** select teams/leagues and, if wanted, API-SPORTS schedule support.
4. **Set the Master Update schedule:** this refreshes provider, guide, and sports data automatically. **Update Now** on Overview runs it immediately.

For Xtream, use the server/base address, such as `https://provider.example:8080`, and put credentials in their separate fields. The app constructs the API, playlist, and XMLTV endpoints. It imports live TV rather than the provider's VOD/series library.

To change providers later, open **Providers → Load Primary**. A local `.m3u`/`.m3u8` file can also be used through **Use File as Primary**. Sports fallback providers are configured separately and do not populate the normal Channels catalog. Provider credentials and saved settings live in persistent runtime storage.

## Playlists and guide URLs

Use the app's **Outputs** controls to copy complete URLs for your host. The main endpoints are:

| Output | Path |
| --- | --- |
| Curated M3U lineup | `/playlist/channels.m3u` |
| Direct-provider M3U fallback | `/playlist/channels.direct.m3u` |
| Combined XMLTV guide | `/epg/epg.xml` |
| Sports XMLTV guide | `/epg/sports.xml` |
| Browser TV Guide | `/guide` |
| Channel 0.2 phone remote | `/remote` |
| Current Roku app ZIP | `/roku/app.zip` |

For example, a LAN client's combined guide URL is `http://<your-host-IP>:9999/epg/epg.xml`. Provider XMLTV remains authoritative; additional public country feeds fill uncovered guide windows.

**Settings → Encoding** controls application-wide FFmpeg encoding and is disabled by default. When enabled, the normal curated M3U routes IPTV channels through Picker; the direct-provider fallback bypasses that encoding. Enabling encoding runs a hardware check, and CPU fallback requires an explicit performance acknowledgement.

The Docker image includes FFmpeg and Comskip. Browser, Roku, Cast, custom-channel, and DVR features use FFmpeg as needed even when global IPTV encoding is disabled. Browser fragmented MP4, MPEG-TS clients, and HLS clients use separate output sessions, so different client types may consume separate provider connections for the same channel.

## TV Guide, phones, and devices

The TV Guide provides a scrolling schedule, channel/program search, day navigation, playback, and DVR controls. Current programs offer **Play now** or **Listen**; future programs can be scheduled for recording. Listen mode requires FFmpeg, which is included in the Docker image; it removes video on the server and sends audio to the browser. **Pop out** uses Picture-in-Picture where supported.

Android phones show **Play on this phone → VLC / Browser**. Install VLC to use the app handoff; for locked-screen audio, enable **Play videos in background** in VLC settings. If it does not open, choose **Browser** and press Play again.

Use **Stream → Roku / Cast** to send a channel to a TV. Multiple Roku devices can be saved, and the Devices page shows device and remote playback status. Receivers must be able to reach the Picker host's advertised LAN address.

The **Remote** page controls virtual channel **0.2**. Tune a browser or Roku to that output once, then use your phone to switch among enabled channels and playable sports feeds. The selector remuxes the chosen feed into a stable HLS output without re-encoding it.

Remote phone access currently requires Tailscale. The Guide and Remote can be installed as separate PWAs from their HTTPS addresses. Do not expose M3U Web Picker directly to the internet: it does not provide a hardened public login boundary. A remote phone sending playback to a Roku acts as the controller; the Roku receives video from Picker on the home network.

## Roku app

The included Roku developer app has a TV guide with category tabs, program details, direct playback, and movie pause/resume controls. It can browse IPTV, sports, and Plex custom channels; it also remains compatible with playback sent from the web guide.

Download the current app from **`http://<your-host-IP>:9999/roku/app.zip`** after configuring `M3U_LAN_HOST`. The ZIP is generated from the installed app and includes your server address. It is sideloaded through Roku developer mode, rather than installed from the Roku Channel Store.

1. From Roku's home screen, press **Home ×3, Up ×2, Right, Left, Right, Left, Right**.
2. Choose **Enable installer and restart**, accept the developer agreement, and set a developer password.
3. From a computer on the same LAN, open the Roku's IP address in a browser. Sign in as `rokudev` with that password.
4. Upload the downloaded ZIP through the Development Application Installer. **Do not extract it.**

Roku allows one sideloaded developer application at a time, so this replaces any existing sideloaded app. If remote control is blocked, check Roku's **Control by mobile apps** setting. See the [user guide](docs/USER-GUIDE.md) for further LAN troubleshooting.

## Plex custom channels and movie lighting

Open **Settings → Custom Channels** to enable the experimental Plex catalog. Refresh reachable, authorized Plex libraries and choose which shows become channels. Show channels can run in episode order or shuffled order, with season limits. Optional commercials use clips imported from your own MP4 collection.

Movie channels include genre mixes, Hallmark, Film Noir, and **Just Released**, which uses the five newest movie release dates. They play without commercials and shuffle without repeats until a channel's pool is exhausted. Custom channels join the existing playlists and XMLTV guide. For Plex movies, **Restart Movie**, **Pause Movie**, and **Back to Live** switch between playback from the beginning and the shared channel schedule.

Under **Settings → Movie Lighting**, select the room's Roku and an identified compatible LAN bulb, then set playing brightness, paused brightness, and fade time. During movie playback, that Roku controls only the selected bulb. Pausing or returning to the guide uses the paused brightness. This experimental integration requires previously identified local dimmable bulbs; **Refresh Light Names** updates known bulbs rather than discovering new smart-light platforms.

## Sports Automation

Follow teams, leagues, conferences, or broad sports. Automation matches provider channels and XMLTV, then publishes temporary event channels with stable league numbering. Saved manual channels remain separate from generated sports feeds.

Ordered fallback providers are tried when the primary provider has no usable sports feed. Optional API-SPORTS adapters provide canonical schedules for MLB, NFL, and NCAA Football; other sports use provider/XMLTV matching. Sports Updates and Master Updates refresh the generated lineup, with a postgame grace period to accommodate games that run long.

## DVR, storage, and backups

Open **DVR** in the guide for **Upcoming & Status** or the completed-recording **Library**. Record individual programs or series, then play saved recordings through the browser. The red DVR badge counts recordings in progress.

**Settings → DVR** controls when completed recordings are processed: immediately, during application updates, or manually with **Process Recordings Now**. Optional Comskip detection runs before H.265/MKV conversion. Failed detection/cutting keeps the recording uncut; failed conversion preserves the original capture. Processing prefers NVIDIA NVENC where available and can retry with CPU encoding.

The optional **Plex folder** exports converted recordings into show/season folders with episode names. These are ordinary MKVs usable by other media servers too. In Docker, the destination must be inside the mounted DVR folder, such as `C:/Users/YourName/Videos/M3U-Web-Picker-DVR/PLEX`.

| Storage | Container location | Default host location |
| --- | --- | --- |
| Settings, providers, channel selections, schedules | `/app/data` | Persistent `m3u-picker-data` volume in Compose project `m3u-picker` |
| DVR captures and converted recordings | `/recordings` | Installer: user's Videos/M3U-Web-Picker-DVR folder (Movies on macOS); manual Compose: `./runtime/recordings` |
| Backups | `/backups` | `./runtime/backups`, or `M3U_BACKUP_DIR` |
| Imported commercial clips | `/commercials` | Persistent `m3u-picker-commercials` volume |

Container replacement preserves these mounts. `docker compose down` retains named volumes; **`down -v` deletes them** and must not be used for a normal update.

Optional **Jellyfin cache cleanup** runs only after a successful Master Update. It requires an explicit `M3U_JELLYFIN_CACHE_DIR` mount and acknowledgement in Settings. The configured path is trusted: verify that it is only Jellyfin's cache, because a wrong path/mount can recursively delete unrelated files.

## Updating

**Installer users:** run the installer again and choose **UPGRADE (UP)**. It downloads the latest release and pulls its matching image before stopping the existing installation.

**Manual Compose users:** run these commands from the same installation folder:

```sh
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

Keep any GPU/discovery override files in both commands. If `.env` pins `M3U_IMAGE` to a numbered version, change it to the desired release first; pulling does not change a pinned version.

An update replaces the running container and briefly interrupts playback/recording sessions. Keep the same project, `.env`, volumes, and recording paths. For an existing source install, use the release Compose file from the existing checkout directory to retain its project and relative runtime paths.

For logs with the release configuration:

```sh
docker compose -f docker-compose.release.yml logs --tail 100
```

Every push to `main` reserves the next numbered tag and draft release, builds AMD64/ARM64 images, runs container tests, and verifies anonymous image access. Successful runs publish the release and update `latest` when their commit is still the current main head. Windows/macOS/Linux installer packaging then runs against that exact tag. Retrying a release workflow reuses its tag; installer downloads may appear shortly after the image and Compose files.

## Development and source builds

Use a source checkout when changing the app. The root `docker-compose.yml` builds locally; `docker-compose.release.yml` pulls the published image.

```sh
git clone https://github.com/zschmook/m3u-web-picker.git
cd m3u-web-picker
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell, or `cp .env.example .env` on macOS/Linux), set the host address and storage paths, then build:

```sh
docker compose up -d --build
```

For source updates, run `git pull --ff-only origin main` followed by the same build command. Preserve GPU/discovery overrides and application data. Source setup helpers remain available in [scripts/docker-setup.sh](scripts/docker-setup.sh) and [scripts/docker-windows.sh](scripts/docker-windows.sh); they update/build the checkout rather than install the published image.

The isolated setup stacks use port `9998` and separate data volumes; see [standalone wizard development](docs/STANDALONE-SETUP-WIZARD.md). They are separate from the normal port-`9999` installation.

Run the Python suite inside a development image, which includes the required dependencies and FFmpeg tools:

```sh
docker build -t m3u-web-picker-tests .
docker run --rm --entrypoint python m3u-web-picker-tests -m unittest discover -s tests
```

For the guide's JavaScript behavior checks, with Node installed:

```sh
node --test tests/guide_stop_playback.test.cjs tests/guide_vlc.test.cjs
```

More documentation: [User guide](docs/USER-GUIDE.md), [installer packaging](installer/docker/README.md), and [project documentation](docs/README.md).
