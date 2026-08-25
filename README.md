# M3U Web Picker

M3U Web Picker turns a large IPTV provider catalog into a small, curated M3U/XMLTV lineup, with optional sports automation, a browser TV guide, Roku/Cast playback helpers, and a virtual HDHomeRun surface for compatible clients.

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

The built-in TV Guide uses the curated lineup and Combined XMLTV output. The Devices page includes virtual HDHomeRun status, saved Roku targets, and active remote playback sessions.

For LAN discovery and Roku/Google Cast playback, `M3U_LAN_HOST` must contain the Docker host computer's LAN IPv4 address.

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

More detailed Roku troubleshooting is in `USER-GUIDE.md`.

## Jellyfin cache integration

Jellyfin cache cleanup is optional. If enabled, M3U Web Picker can clear the configured Jellyfin cache **only after a successful Master Update** to reduce stale Live TV logos/metadata.

**WARNING: the Jellyfin cleanup path is trusted exactly as configured. There is currently no path-safety validation to prove that it points only at a Jellyfin cache directory. A wrong host path, container mount, or configuration could cause recursive deletion of unrelated data — in the worst case, potentially an entire mounted drive/filesystem. Use this feature only after manually verifying the path and mount.**

For Docker, the cache directory must be explicitly mounted into the container with `M3U_JELLYFIN_CACHE_DIR`. The wizard requires an acknowledgement before enabling deletion because clearing Jellyfin cache data may also affect cached information for downloaded movies, downloaded TV shows, and DVR recordings.

## Persistent data and backups

The normal Compose project is `m3u-picker` and stores application state in the `m3u-picker-data` Docker volume. `docker compose down` preserves the volume. `docker compose down -v` deletes it.

Docker backups are written through the `/backups` bind mount. Override the host directory with `M3U_BACKUP_DIR` in `.env`.

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

Do not remove the data volume during a normal update.

## Tests

The repository uses Python `unittest` tests and JavaScript syntax checks can be run with Node when available:

```bash
python -m unittest discover -s tests
python -m compileall -q .
find static -name '*.js' -print0 | xargs -0 -n1 node --check
```

## Planned channel-balanced cold start — not yet implemented

> **Status: design only. This global seed is not used by the application yet.**

Commercial detection currently keeps every channel's learned profile independent. The planned cold-start layer will let an unfamiliar channel begin with a weak hint from other mature, compatible channels without sharing logo fingerprints, screen positions, or raw observations.

A contributing channel must have roughly three hours of retained observations, 20–30 minutes of commercial observations, at least six complete commercial-to-program transitions, and a usable balance of both classes. Normal bug-based television, sports-generated channels, and bugless/countdown channels will use separate pools.

For the current feature window `x`, each eligible channel supplies its independent score `S_c(x)`. Scores are converted to log-odds and combined with a trimmed mean so that one unusual channel cannot dominate:

```text
z_c = log(S_c / (1 - S_c))
S_global = sigmoid(trimmed_mean(z_1, z_2, ... z_k))
```

Each mature channel receives approximately one vote regardless of how many extra hours it was watched. The target channel is excluded from its own seed.

Local maturity depends on retained program hours `H_p`, commercial hours `H_a`, and complete commercial transitions `T`:

```text
m = cube_root(
    (1 - exp(-H_p / 0.75))
  * (1 - exp(-H_a / 0.25))
  * (1 - exp(-T / 6))
)
```

The effective score blends the global hint with the independent local model:

```text
S_effective = (1 - m) * S_global + m * S_channel
```

For example, if the global seed scores a window at 82%, the local channel scores it at 65%, and local maturity is 55%, the effective score is 72.7%. At 90% maturity it becomes 66.7%, so inherited guidance fades naturally as local observations accumulate.

Global predictions will never be written into local history as confirmed facts, and a seeded channel will not contribute to the global pool until it independently satisfies the maturity requirements. Manual corrections retain stronger weight than inferred labels.

## Current known cleanup items

Two non-blocking issues are still being tracked:

- update-state indicators can visibly finish a fraction of a cycle apart because the sidebar/status layer and Master Update lifecycle layer still have overlapping rendering responsibilities;
- provider events that lose a recognized league classification can fall into generic `football`/`sports` numbering blocks, producing very high sports channel numbers and occasional duplicate event rows.

Both are functional cleanup items rather than data-loss problems.

See `USER-GUIDE.md` for the operator-oriented setup and troubleshooting notes.
