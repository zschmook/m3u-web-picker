# M3U Web Picker

M3U Web Picker turns a large IPTV provider catalog into a small, curated M3U/XMLTV lineup, with optional sports automation, a browser TV guide, Roku/Cast playback helpers, and a virtual HDHomeRun surface for compatible clients.

The current main line is **v31**. Docker is the supported runtime while the packaged installation workflows are being revised.

## Docker quick start

Install Docker Desktop first. On Windows, also install [Git for Windows](https://git-scm.com/download/win), then run this command in Git Bash. On macOS, run it in Terminal after installing Git.

```bash
curl -fsSL https://raw.githubusercontent.com/zschmook/m3u-web-picker/main/scripts/docker-setup.sh | sh
```

The script verifies Docker is running, downloads or updates M3U Web Picker in `~/m3u-web-picker`, detects the computer's LAN IPv4 address, saves it in `.env`, and builds and starts the container. Existing application data is preserved. Set `M3U_PICKER_DIR` before running it to choose a different checkout location.

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

Jellyfin cache cleanup is optional and experimental. If enabled, M3U Web Picker can clear the configured Jellyfin cache **only after a successful Master Update** to reduce stale Live TV logos/metadata.

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

## Current known cleanup items

Two non-blocking issues are still being tracked:

- update-state indicators can visibly finish a fraction of a cycle apart because the sidebar/status layer and Master Update lifecycle layer still have overlapping rendering responsibilities;
- provider events that lose a recognized league classification can fall into generic `football`/`sports` numbering blocks, producing very high sports channel numbers and occasional duplicate event rows.

Both are functional cleanup items rather than data-loss problems.

See `USER-GUIDE.md` for the operator-oriented setup and troubleshooting notes.
