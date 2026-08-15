# M3U Web Picker — User Guide

This guide describes the current v30 application. Historical RC/experiment documents have been removed from the active tree; older material remains available in Git history if needed.

## 1. Start the application

From the repository directory:

```bash
docker compose up -d --build
```

Open `http://localhost:9999`.

The normal Compose stack uses a persistent Docker volume. Rebuilding the container does not erase configured providers, channel selections, sports rules, cached guides, or generated outputs.

## 2. First-run setup

A blank install opens the setup wizard automatically.

### Primary provider

Enter either:

- an M3U URL; or
- an Xtream server/base URL plus username and password.

Xtream users should enter the server/base URL only. M3U Web Picker probes the Xtream API and builds playlist/XMLTV URLs internally.

The provider must validate before the wizard continues.

### Automatic update schedule

The Master Update schedule controls the application-wide provider/EPG/sports refresh. The default is 3:00 AM in the configured timezone.

### Manual channels

Choose any normal provider channels that should always remain in the curated lineup. Selecting zero manual channels is allowed when the install is intended mainly for generated sports channels.

### Sports Automation

Sports Automation is optional. If enabled, select teams, leagues, conferences, or broad sports to follow.

Team-specific selections can expand to preferred home/away/national feeds. Broad league selections normally generate one best feed per game unless an explicit team rule requires the expanded feed set.

### Sports schedule API

API-SPORTS integration is optional. The current adapters support canonical schedules for MLB, NFL, and NCAA Football. Unsupported sports continue using provider/XMLTV matching.

### Jellyfin

Jellyfin integration is optional. The cache cleanup feature is experimental and requires an explicit acknowledgement.

**Warning:** clearing Jellyfin cache data may affect cached information for downloaded movies, downloaded TV shows, and DVR recordings.

If cache cleanup is desired, start the container with the Jellyfin cache directory mounted:

```bash
M3U_JELLYFIN_CACHE_DIR="/absolute/path/to/jellyfin/cache" docker compose up -d --build
```

The wizard validates that the mounted directory exists and is writable before cleanup can be enabled.

## 3. Providers

The primary provider supplies the catalog shown in Channels. Optional fallback providers are used only by Sports Automation when a usable primary sports feed is missing.

Removing/replacing the primary provider is the supported way to change stored primary credentials.

## 4. Channels

The Channels page is the manual/static lineup manager. Search by channel, provider group, or source; add/remove visible channels; and manage saved ordering.

Manual channels are preserved independently from generated sports rows. Sports deduplication must never delete, replace, or suppress a saved manual selection.

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
/epg/epg.xml
```

Use the Outputs button in the UI to copy fully qualified URLs for the current host.

## 9. TV Guide and LAN playback

The built-in TV Guide displays the curated lineup and programme windows. Provider URLs and credentials remain server-side; browser/Cast/Roku/HDHomeRun playback uses Picker-owned routes.

For LAN playback/discovery, configure the host LAN IPv4 address in `.env`:

```text
M3U_LAN_HOST=192.168.x.x
```

On macOS:

```bash
./scripts/detect-lan-host.sh
```

Rebuild/restart after changing `.env`.

### Browser and Google Cast

The browser player uses the server-side ffmpeg normalization path. Google Cast uses an HLS relay reachable from the TV over the LAN. The browser remains the controller and Google's normal Cast receiver picker chooses the target device.

### Roku

Roku devices are discovered over the local network and saved by stable device identity, with serial-number fallback, so a saved target can be reconciled after a DHCP address change. Multiple saved Roku targets are supported.

Roku playback requires the M3U Web Picker developer receiver to be sideloaded on the Roku. The receiver ZIP is kept at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver-exp1.zip
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
