# M3U Web Picker

M3U Web Picker turns a large IPTV provider catalog into a small, curated M3U/XMLTV lineup, with optional sports automation, a browser TV guide and DVR, Roku/Cast playback helpers, and a virtual HDHomeRun surface for compatible clients.

The current main line is **v31**. Docker is the supported runtime while the packaged installation workflows are being revised.

## Docker quick start

Install Docker Desktop first. On Windows, also install [Git for Windows](https://git-scm.com/download/win), then run this command in Git Bash. On macOS, run it in Terminal after installing Git.

```bash
curl -fsSL https://raw.githubusercontent.com/zschmook/m3u-web-picker/main/scripts/docker-setup.sh | sh
```

The script verifies Docker is running, downloads or updates M3U Web Picker in `~/m3u-web-picker`, detects the computer's LAN IPv4 address, saves it in `.env`, and builds and starts the container. Existing application data is preserved. Set `M3U_PICKER_DIR` before running it to choose a different checkout location.

On Linux and Windows, the installers automatically request NVIDIA GPU passthrough when `nvidia-smi` is available. GPU passthrough is not supported yet for Docker installs on macOS, so FFmpeg uses CPU fallback there.

Open `http://localhost:9999`.

A fresh data volume opens the first-run setup wizard. Existing configured installs skip the wizard and keep their persisted state.

## Running the application

The quick-start script above is the recommended installation path. It creates or updates the checkout, prepares `.env`, detects the LAN address used by Roku/Cast/HDHomeRun, selects the NVIDIA Compose override when available, and starts the app.

To install manually from a fresh Windows PowerShell session after Docker Desktop is installed and running:

```powershell
Set-Location C:\
git clone https://github.com/zschmook/m3u-web-picker.git C:\m3u-web-picker
Set-Location C:\m3u-web-picker
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
```

Before starting, set `M3U_LAN_HOST` in `.env` to the Windows computer's private IPv4 address if Roku, Cast, or HDHomeRun clients will be used. A new Windows installation can also set `M3U_DVR_DIR=C:/DVR` before the first start. Create that folder first if the setup script was not used.

For an NVIDIA-equipped Windows or Linux host with Docker GPU support, start with both Compose files:

```powershell
Set-Location C:\m3u-web-picker
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

On macOS or Linux, the equivalent manual installation is:

```bash
git clone https://github.com/zschmook/m3u-web-picker.git ~/m3u-web-picker
cd ~/m3u-web-picker
cp .env.example .env
./scripts/detect-lan-host.sh --write-env
docker compose up -d --build
```

Once installed, open `http://localhost:9999` on the Docker host or `http://<M3U_LAN_HOST>:9999` from another device on the same LAN.

Common lifecycle commands must be run from the repository directory:

```powershell
Set-Location C:\m3u-web-picker
docker compose start
docker compose stop
docker compose logs --tail 100
```

`docker compose start` starts the existing container with the same GPU/device configuration used when it was created. `docker compose stop` stops the app without removing its container or data. `docker compose down` removes the container and network but preserves the named application-data volume. Do not use `docker compose down -v` for a normal stop, restart, or upgrade because `-v` deletes that volume.

## Adding your own providers

A provider supplies the live-channel catalog. M3U Web Picker supports one primary provider in the normal channel manager, plus optional ordered fallback providers used only by Sports Automation.

On a new installation, the first-run wizard offers two paths:

- **Your own provider:** enter either a direct M3U playlist URL or an Xtream server/base URL with a username and password.
- **Free public demo:** use the included community playlists to test the normal guide/playback pipeline before paying for or configuring a provider.

For Xtream service, enter only the base server address—such as `https://provider.example:8080`—in the provider URL field. Put the username and password in their separate fields. Do not paste a generated `get.php` URL when separate Xtream credentials are available. Picker validates `player_api.php`, requests the live-stream catalog without importing VOD/series libraries, and constructs the playlist and XMLTV endpoints internally.

To add or replace a provider after setup:

1. Open **Providers** from the sidebar.
2. Enter a friendly **Primary name**.
3. Enter the direct M3U URL, or enter the Xtream base URL plus username and password.
4. Select **Load Primary** and wait for validation and channel loading to finish.
5. Open **Channels**, search or filter the catalog, add the channels to keep, arrange their order, and save it.
6. Run **Update Now** from Overview to publish the refreshed curated M3U and guide data immediately instead of waiting for the next Master Update.

A local `.m3u` or `.m3u8` file can be selected under **M3U File** and installed with **Use File as Primary**. To change credentials or switch services, load a replacement primary provider; provider credentials remain in the persistent runtime data and are not written to the repository or exposed in generated browser URLs.

Sports fallback providers are configured separately at the bottom of **Providers**. Enter the fallback name and either its M3U URL or Xtream login, then select **Add Fallback**. Fallbacks are tried in the displayed priority order only when Sports Automation cannot find a usable feed from the primary provider. They do not populate the normal Channels catalog.

Provider XMLTV data remains authoritative. Additional public country guide sources can be enabled on the Providers/EPG controls to fill uncovered programs; they do not replace valid provider listings.

## First-run setup

The setup flow can configure:

- a primary M3U or Xtream provider, or the built-in free public M3U demo option for testing without an IPTV service;
- manual channels to keep in the curated lineup;
- Sports Automation and team/league rules;
- optional API-SPORTS schedule support for supported leagues;
- the automatic Master Update schedule;
- optional Jellyfin cache cleanup (**use with extreme care: the configured cleanup path is not safety-checked, so an incorrect path or mount can delete data outside the Jellyfin cache**).

Provider credentials and application state are stored in the runtime data directory, not in the repository.

## Main outputs

The two normal client-facing outputs are:

- M3U: `/playlist/channels.m3u`
- Combined XMLTV: `/epg/epg.xml`

When application-wide FFmpeg encoding is enabled under **Settings → Encoding**, the normal M3U routes every curated channel through Picker. The permanent fallback `/playlist/channels.direct.m3u` always bypasses Picker encoding. Enabling encoding runs a functional hardware test; when acceleration is unavailable, CPU fallback requires an explicit performance-risk acknowledgment.

### FFmpeg playback path

```text
IPTV PROVIDER
|
+-- FFmpeg disabled
|   `-- Direct provider stream
|       `-- /playlist/channels.direct.m3u
|
`-- FFmpeg enabled
    |
    +-- Run encoder check
    |   +-- Hardware works -> NVENC / QSV / VAAPI
    |   `-- Hardware unavailable -> warning -> CPU libx264
    |
    `-- Client requests channel
        |
        +-- Browser / TV Guide
        |   `-- FFmpeg -> fragmented MP4 -> browser player
        |
        +-- Jellyfin / HDHomeRun / encoded M3U client
        |   `-- Same channel already encoded as MPEG-TS?
        |       +-- Yes -> join shared stream
        |       `-- No  -> provider -> FFmpeg -> shared MPEG-TS
        |
        `-- Roku / Chromecast
            `-- Same channel already encoded as HLS?
                +-- Yes -> reuse shared HLS session
                `-- No  -> provider -> FFmpeg -> shared HLS

SESSION CLEANUP
|
+-- Another viewer remains -> keep FFmpeg/provider connection alive
`-- Last viewer disconnects -> stop FFmpeg and close provider connection
```

Browser fragmented MP4, shared MPEG-TS, and shared HLS are separate output sessions. Clients using different output formats can therefore still open separate FFmpeg processes and provider connections for the same channel.

Sports-only output and additional diagnostic/status endpoints are also exposed by the application.

## Sports Automation

Sports Automation scans provider channels and XMLTV data, matches configured teams/leagues, and publishes temporary event channels. Optional API-SPORTS schedule data can provide canonical schedules for MLB, NFL, and NCAA Football selections.

Manual channels and generated sports channels are separate namespaces. A generated sports feed must not remove or replace a saved manual channel even when both point to the same underlying stream.

Sports channel numbers are organized into stable league blocks. Generated channel identity is independent of the reusable numeric slot so guide clients do not confuse a new event with an older event that previously occupied the same number.

## TV Guide and devices

The built-in TV Guide uses the curated lineup and Combined XMLTV output. It provides a compact scrolling schedule, day navigation, program search, one-click local playback, recording controls, and remote playback destinations without replacing the normal guide with a separate multiview or sports-only interface.

Guide search matches channel metadata and individual program titles. Matching programs are highlighted in the timeline, which makes searches such as `news hour` useful even when several stations carry the same show at different times. The day buttons retain the compact current-time window for **Now** and provide full-day navigation for future dates.

Selecting a current program offers **Play now**. Selecting a future program offers DVR scheduling when the recorder is enabled. The local browser player normalizes provider video to H.264/AAC fragmented MP4, and **Pop out** uses the standard Picture-in-Picture API with a WebKit presentation-mode fallback. Closing Picture-in-Picture returns playback cleanly to the guide when the browser supports that transition.

The **Stream** menu keeps Roku and Google Cast controls together. The Devices page includes virtual HDHomeRun status, saved Roku targets, and active remote playback sessions.

Opening **DVR** switches the page from guide browsing to a dedicated recorder view; the search controls and channel grid return when DVR is closed. The top DVR button shows an active state while this mode is open. DVR contains two tabs:

- **Upcoming & Status** shows the selected day's scheduled, active, queued, failed, and ready recordings. Series rules appear as compact accordions with their next episode; expanding a rule reveals its state and cancellation control and filters the recording list to that series.
- **Library** shows only playable completed recordings. Shows are grouped into expandable title rows, groups are ordered by their newest recording, and episodes inside each group are newest first. Library playback uses the same browser-safe FFmpeg path as live TV, so saved H.265/MKV files do not need native HEVC browser support.

The red DVR badge in the header and sidebar counts recordings currently in progress; it is not a count of scheduled or saved programs.

For LAN discovery and Roku/Google Cast playback, `M3U_LAN_HOST` must contain the Docker host computer's LAN IPv4 address.

Remote access should use a trusted private network such as Tailscale rather than forwarding the Picker port to the public internet. The application does not currently provide a hardened public login boundary. When a phone reaches Picker through Tailscale and sends a channel to a Roku at home, the phone acts only as the remote control: the Picker server contacts the Roku and the Roku pulls the media over the home LAN. The video does not travel through the phone's mobile-data connection. Roku and Cast receivers still need network reachability to the Picker host and its advertised `M3U_LAN_HOST` media URLs.

From an existing checkout, Windows users can also open Git Bash and run:

```bash
cd /c/git/m3u-web-picker
./scripts/docker-windows.sh
```

The helper uses the same all-in-one setup flow while keeping that checkout location. It detects the active Windows LAN address, writes it to `.env`, updates the checkout, rebuilds and recreates the normal container, and then shows its status. It preserves the application data volume.

On macOS or Linux, `scripts/detect-lan-host.sh --write-env` updates `.env`; recreate the container afterward so it receives the new value.

## Roku receiver

M3U Web Picker includes its own Roku developer receiver so channels from the built-in TV Guide can be sent directly to a Roku on the same LAN. Multiple Roku devices can be discovered and saved, and saved devices are reconciled by stable device identity when their DHCP address changes.

The sideloadable receiver is included in the repository at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver.zip
```

Do **not** extract that ZIP before installing it on the Roku.

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

Choose **Enable installer and restart**, accept the developer agreement, set a developer password, and let the Roku restart. If necessary, also allow local-network control under Roku's **Control by mobile apps** setting.

From a computer on the same LAN, open the Roku's IP address in a browser, sign in to the Development Application Installer with username `rokudev` and the developer-mode password, choose **Install with zip**, and upload `m3u-web-picker-roku-receiver.zip`.

Roku permits only one sideloaded developer application at a time, so installing this receiver replaces any other sideloaded developer channel.

After installation, use the M3U Web Picker TV Guide/Devices controls to discover or save the Roku and send a channel to it. Roku playback uses the Picker's LAN HLS relay, so `M3U_LAN_HOST` must point to the Picker host's actual LAN IPv4 address.

More detailed Roku troubleshooting is in the [user guide](docs/USER-GUIDE.md).

## Jellyfin cache integration

Jellyfin cache cleanup is optional. If enabled, M3U Web Picker can clear the configured Jellyfin cache **only after a successful Master Update** to reduce stale Live TV logos/metadata.

**WARNING: the Jellyfin cleanup path is trusted exactly as configured. There is currently no path-safety validation to prove that it points only at a Jellyfin cache directory. A wrong host path, container mount, or configuration could cause recursive deletion of unrelated data — in the worst case, potentially an entire mounted drive/filesystem. Use this feature only after manually verifying the path and mount.**

For Docker, the cache directory must be explicitly mounted into the container with `M3U_JELLYFIN_CACHE_DIR`. The wizard requires an acknowledgement before enabling deletion because clearing Jellyfin cache data may also affect cached information for downloaded movies, downloaded TV shows, and DVR recordings.

## Persistent data and backups

The normal Compose project is `m3u-picker` and stores application state in the `m3u-picker-data` Docker volume. `docker compose down` preserves the volume. `docker compose down -v` deletes it.

Docker backups are written through the `/backups` bind mount. Override the host directory with `M3U_BACKUP_DIR` in `.env`.

In-app DVR recordings use a dedicated `/recordings` bind mount. New Windows Docker setups create and use `C:/DVR` by default; an existing custom `M3U_DVR_DIR` is preserved. Raw transport-stream captures remain in the DVR folder, while successful H.265/MKV conversions are written under `converted/` by default.

**Settings → DVR** controls whether completed recordings are processed immediately, during scheduled or manual application updates, or only through the **Process Recordings Now** action in **Upcoming & Status**. Immediate processing is serialized so only one conversion runs at a time. Comskip commercial detection runs before conversion when enabled. Implausible cut lists are rejected, and a detection or cutting failure produces an uncut MKV rather than discarding the recording. A failed conversion preserves the original transport stream.

The optional media-server library folder (currently labeled **Plex folder** in Settings) moves successful conversions into show and season folders with episode names such as `The Wall.S06E10.mkv`. The files are ordinary MKVs and are not tied to a particular media server. For the current Docker setup, the destination must be inside the mounted DVR folder—for example, `C:/DVR/PLEX`—because Docker cannot write to an arbitrary host path that was not mounted when the container started.

DVR conversion automatically prefers NVIDIA NVENC when the GPU Compose override is active, targets 3 Mbps with 4.5 Mbps peak headroom for 1080p recordings, and safely retries with CPU `libx265` if hardware encoding is unavailable. Comskip, temporary conversion files, final recordings, and media-server library files all remain on host-mounted storage; recording data is never written into the container layer. Database rows retain the relative path to each completed file so Library playback can resolve it without accepting arbitrary filesystem paths from the browser.

## Clean first-run testing

An isolated development Compose file is kept specifically for testing the setup wizard without touching the normal instance:

```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```

That stack uses host port `9998` and a separate `m3u-picker-dev-data` volume. Using `-v` is intentional for this isolated wizard test because the point is to start from a genuinely blank database.

## Updating

For a normal Docker/source checkout:

```bash
git pull --ff-only origin main
docker compose up -d --build
```

For a host configured for NVIDIA GPU passthrough, keep the GPU override active during the rebuild:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Do not remove the data volume during a normal update.

## Tests

The repository uses Python `unittest` tests and JavaScript syntax checks can be run with Node when available:

```bash
python -m unittest discover -s tests
python -m compileall -q .
find static -name '*.js' -print0 | xargs -0 -n1 node --check
```

## Current known cleanup items

Three non-blocking issues are still being tracked:

- update-state indicators can visibly finish a fraction of a cycle apart because the sidebar/status layer and Master Update lifecycle layer still have overlapping rendering responsibilities;
- provider events that lose a recognized league classification can fall into generic `football`/`sports` numbering blocks, producing very high sports channel numbers and occasional duplicate event rows.
- background update queue warnings can briefly spike and emit repetitive depth logs, especially when several guide tabs are open; tune Waitress/threading and UI polling during scheduled runs before considering it a production signal.

These are functional cleanup items rather than data-loss problems.

See the [user guide](docs/USER-GUIDE.md) for operator-oriented setup and troubleshooting notes.
