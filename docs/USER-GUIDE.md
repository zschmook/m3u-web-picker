# M3U Web Picker — User Guide

This guide describes the current v31 application. Docker is the supported runtime while packaged installation workflows are being revised.

## 1. Start the application

Install Docker Desktop first. On Windows, also install [Git for Windows](https://git-scm.com/download/win), then run this command in Git Bash. On macOS, run it in Terminal after installing Git:

```bash
curl -fsSL https://raw.githubusercontent.com/zschmook/m3u-web-picker/main/scripts/docker-setup.sh | sh
```

The setup script installs or updates the application in `~/m3u-web-picker`, detects the computer's LAN IPv4 address, writes the required `.env` values, and builds the container without deleting existing application data. On Windows, a new setup also creates `C:/DVR` and uses it as the persistent DVR mount. Existing custom DVR paths are preserved. Set `M3U_PICKER_DIR` first to choose another checkout location.

To start manually from an existing checkout:

```bash
docker compose up -d --build
```

Open `http://localhost:9999`.

The normal Compose stack uses a persistent Docker volume. Rebuilding the container does not erase configured providers, channel selections, sports rules, cached guides, or generated outputs.

On Linux and Windows, the installer requests NVIDIA GPU passthrough when `nvidia-smi` is available. GPU passthrough is not supported for Docker installs on macOS, so FFmpeg uses CPU fallback there.

## 2. First-run setup

A blank install opens the setup wizard automatically.

### Primary provider

Enter either:

- an M3U URL; or
- an Xtream server/base URL plus username and password; or
- the built-in free public M3U demo for testing without an IPTV service.

Xtream users should enter the server/base URL only. M3U Web Picker probes the Xtream API and builds playlist/XMLTV URLs internally.

The provider must validate before the wizard continues.

### Automatic update schedule

The Master Update schedule controls the application-wide provider/EPG/sports refresh. The default is 3:00 AM in the configured timezone.

### Manual channels

Choose any normal provider channels that should always remain in the curated lineup. Selecting zero manual channels is allowed when the install is intended mainly for generated sports channels.

**Hide SD / Low Bandwidth Channels** hides low-bandwidth provider entries from the normal catalog and sports-generated feed selection. The same setting remains available on the Channels page after setup.

### Sports Automation

Sports Automation is optional. If enabled, select teams, leagues, conferences, or broad sports to follow.

Team-specific selections can expand to preferred home/away/national feeds. Broad league selections normally generate one best feed per game unless an explicit team rule requires the expanded feed set.

### Sports schedule API

API-SPORTS integration is optional. The current adapters support canonical schedules for MLB, NFL, and NCAA Football. Unsupported sports continue using provider/XMLTV matching.

### Jellyfin

Jellyfin integration is optional. Cache cleanup requires an explicit risk acknowledgement before it can be enabled or saved.

**Warning:** the cleanup path is trusted as configured. The application verifies that the mounted path exists and is writable, but it cannot prove that the path contains only Jellyfin cache data. A wrong host path or mount can recursively delete unrelated data. Clearing the correct Jellyfin cache may also affect cached information for downloaded movies, downloaded TV shows, and DVR recordings.

If cache cleanup is desired, start the container with the Jellyfin cache directory mounted:

```bash
M3U_JELLYFIN_CACHE_DIR="/absolute/path/to/jellyfin/cache" docker compose up -d --build
```

The wizard validates that the mounted directory exists and is writable before cleanup can be enabled. Manually confirm the host path and container mount before accepting the risk.

## 3. Providers

The primary provider supplies the catalog shown in Channels. Optional fallback providers are used only by Sports Automation when a usable primary sports feed is missing.

Removing/replacing the primary provider is the supported way to change stored primary credentials.

## 4. Channels

The Channels page is the manual/static lineup manager. Search by channel, provider group, or source; add/remove visible channels; and manage saved ordering.

Manual channels are preserved independently from generated sports rows. Sports deduplication must never delete, replace, or suppress a saved manual selection.

The **Hide SD / Low Bandwidth Channels** filter hides low-bandwidth catalog entries while preserving already-saved channels in Saved Channels mode.

## 5. EPG

The EPG page manages additional XMLTV sources and optional public country feeds. Provider guide data remains authoritative; fallback/public guide data fills uncovered windows when possible.

Main combined guide output:

```text
/epg/epg.xml
```

## 6. Sports Automation

Generated sports rows are rebuilt during Sports Updates/Master Updates. Old generated rows are removed when they no longer match the active event window, while manual rows remain untouched.

Replays are disabled by default. When replay handling is enabled, later airings should reuse the logical event/feed identity rather than allocating unrelated duplicate channels.

The normal postgame grace period prevents a channel from disappearing immediately at the scheduled end of a long game.

## 7. Master Update

Master Update refreshes the configured provider/EPG/sports pipeline in the background. The UI remains navigable while the update runs. TV Guide links are disabled while published outputs are being rewritten and unlock when the authoritative worker reports completion.

If an update completes with warnings or failures, use the status details view to see the affected stages.

## 8. Outputs

Normal client URLs are:

```text
/playlist/channels.m3u
/playlist/channels.direct.m3u
/epg/epg.xml
```

Use the Outputs button in the UI to copy fully qualified URLs for the current host.

When application-wide encoding is enabled under **Settings → Encoding**, `/playlist/channels.m3u` uses Picker's FFmpeg path. `/playlist/channels.direct.m3u` is the permanent direct-provider fallback and bypasses encoding.

Encoding is disabled by default. Enabling it runs a functional hardware check. Supported Docker acceleration can use NVIDIA NVENC, Intel QSV, or VA-API when passed through successfully; otherwise CPU `libx264` fallback requires an explicit performance-risk acknowledgement. Browser fragmented MP4, shared MPEG-TS, and shared HLS are separate sessions, so different client types can open separate FFmpeg processes for the same channel.

## 9. TV Guide and LAN playback

The built-in TV Guide displays the curated lineup and program windows. Provider URLs and credentials remain server-side; browser/Cast/Roku/HDHomeRun playback uses Picker-owned routes.

### In-app DVR

The in-app DVR is disabled by default. Before enabling it, set `M3U_DVR_DIR` in `.env` to a dedicated folder on the Docker host and rebuild/restart the container:

```text
M3U_DVR_DIR=C:/DVR
```

Then open **Settings → DVR**, enter that exact local host path, validate it, and enable DVR. The application refuses to schedule recordings until the path matches the active Docker bind mount and `/recordings` is writable inside the container.

Select a program in the TV Guide to record that airing or create a series rule for the same title and channel. When guide metadata includes an `SxxEyy` episode number, the DVR records that episode only once and ignores its rebroadcasts. Shows without episode numbers stay anchored near the airtime originally selected so an overnight repeat with the same title is not recorded as another episode. Capture begins with a temporary transport stream on the host-mounted recording folder. Under **Settings → DVR**, choose whether completed recordings are processed immediately, during scheduled or manual application updates, or only through a dedicated manual processing request. Immediate processing uses one worker and queues simultaneous completions in order. Before H.265/MKV conversion begins, every `.ts` is checked to confirm that it is not active or still changing.

When **Remove detected commercials** is enabled, Comskip creates a proposed cut list before FFmpeg performs the H.265 conversion. The app rejects implausibly large cut lists. If detection or cutting fails, it creates an uncut MKV instead and reports the fallback on the DVR recording. H.265 conversion prefers NVIDIA NVENC when GPU passthrough is active and retries safely with CPU `libx265` if NVENC fails. NVENC conversions target 3 Mbps with 4.5 Mbps peak headroom so ordinary 1080p provider recordings remain smaller than their raw transport streams. A failed conversion always leaves the original `.ts` capture in place.

By default, successful MKVs are stored under the DVR folder's `converted/` directory. To hand completed shows to Plex, enter a **Plex folder** in **Settings → DVR**. The current Docker setup requires this folder to be inside the mounted DVR folder, such as `C:/DVR/PLEX`. Successful recordings then move into show and season folders with Plex-friendly names; an episode described as `S06 E10` becomes `Show Name/Season 06/Show Name.S06E10.mkv`. Raw `.ts` captures and completed files do not share the same folder. Neither temporary nor completed recording data is stored in the container layer.

For LAN playback/discovery, configure the host LAN IPv4 address in `.env`:

```text
M3U_LAN_HOST=192.168.x.x
```

On macOS or Linux:

```bash
./scripts/detect-lan-host.sh --write-env
```

GPU passthrough is not supported yet for Docker installs on macOS. FFmpeg uses CPU fallback even if the Mac has supported graphics hardware.

Rebuild/restart after changing `.env`.

### Browser and Google Cast

With application-wide encoding enabled, the browser player uses server-side FFmpeg and fragmented MP4. With encoding disabled, clients retain the direct-provider path. Google Cast uses an HLS relay reachable from the TV over the LAN. The browser remains the controller and Google's normal Cast receiver picker chooses the target device.

### Roku

Roku devices are discovered over the local network and saved by stable device identity, with serial-number fallback, so a saved target can be reconciled after a DHCP address change. Multiple saved Roku targets are supported.

Roku playback requires the M3U Web Picker developer receiver to be sideloaded on the Roku. The receiver ZIP is kept at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver.zip
```

To enable Roku developer mode, from the Roku home screen press:

```text
Home
Home
Home
Up
Up
Right
Left
Right
Left
Right
```

Choose **Enable installer and restart**, accept the developer agreement, set a developer password, and let the Roku restart. If required, also allow local-network control under Roku's **Control by mobile apps** setting.

From a computer on the same LAN, open the Roku's IP address in a browser (`http://ROKU-IP`), sign in to the Development Application Installer with username `rokudev` and the developer-mode password, choose **Install with zip**, and upload the ZIP above without extracting it first. Roku allows only one sideloaded developer application at a time.

After installation, use the TV Guide's Roku controls to discover/save a device and send the currently selected channel to it. The Roku pulls the HLS relay from the Picker host, so `M3U_LAN_HOST` must be correct.

### Virtual HDHomeRun / Plex-style tuner clients

Virtual HDHomeRun support presents the same curated manual + generated-sports lineup through tuner-shaped HTTP endpoints. The normal M3U/XMLTV outputs remain the source of truth.

Useful endpoints on the normal `9999` application port are:

```text
/discover.json
/lineup_status.json
/lineup.json
/device.xml
/capability
```

Channel streams resolve through:

```text
/hdhr/stream/<channel-number>
/auto/v<channel-number>
```

The facade advertises two tuners. Discovery uses UDP 65001. The normal Compose stack also publishes the app on host port 80 because some HDHomeRun/Plex/Jellyfin discovery flows follow UDP discovery with a bare-IP HTTP request such as `http://<picker-ip>/discover.json`.

For a client that accepts XMLTV guide data separately, use the same final guide:

```text
http://<picker-lan-ip>:9999/epg/epg.xml
```

## 10. Backups and persistence

Normal state lives in the `m3u-picker-data` Docker volume. Backups default to `./backups` and can be redirected with `M3U_BACKUP_DIR`.

Normal rebuild/update:

```bash
docker compose down
docker compose up -d --build
```

Do **not** use `-v` unless you intentionally want to delete the normal application data volume.

For a normal source update:

```bash
git pull --ff-only origin main
docker compose up -d --build
```

Do not remove the data volume during a normal update.

## 11. Clean setup-wizard test

For an isolated blank install on port 9998:

```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

This is the one workflow where `-v` is expected: the dev stack has a separate disposable data volume and the test is specifically intended to exercise first-run behavior.

## 12. Troubleshooting

### LAN devices cannot reach the Picker

Set `M3U_LAN_HOST` to the host computer's actual private IPv4 address and rebuild/restart the Compose stack.

### Roku is discovered but playback does not start

Discovery proves that a Roku ECP device is reachable; it does not prove the sideloaded M3U Web Picker receiver is installed. Confirm developer mode is enabled and reinstall the receiver ZIP if needed.

### Jellyfin still shows old artwork

First confirm that the M3U/XMLTV output contains the new event identity/artwork. Jellyfin and web browsers may independently cache images. The optional Jellyfin cache integration can clear the configured cache after a successful Master Update; browser cache is outside the Picker's control.

### Very high generated sports channel numbers

A known classification edge case can put provider events that are detected only as generic `football` or `sports` into fallback 1,000-channel blocks after the full league taxonomy. This can produce numbers around the 200,000 range and duplicate-looking event rows. It is tracked as a low-priority cleanup issue; generated rows are cheap at normal rule counts.

### Update indicators finish slightly out of sync

The authoritative worker state is correct, but two frontend status layers currently touch some of the same update controls. The remaining visual synchronization cleanup is tracked separately.
