# M3U Web Picker

M3U Web Picker is a self-hosted Flask application for curating IPTV channels, generating clean M3U/XMLTV outputs, automating temporary sports channels, and serving the result to clients such as Jellyfin, Plex, VLC, Roku, Chromecast/Google TV, and HDHomeRun-compatible software.

The current `main` branch is the v30 UI and device stack.

## Highlights

- Modern sidebar UI with dedicated Overview, Providers, Channels, EPG, Sports Automation, and Devices pages.
- Theme support: Midnight, Slate, OLED Black, Carbon, Light, Ice, Terminal Amber, Terminal Green, Cornfield, and Ketchup & Mustard.
- One primary IPTV provider plus optional fallback providers for Sports Automation.
- Direct M3U and Xtream provider support.
- Manual channel curation, search, filtering, numbering, and ordering.
- Generated sports channels with separate numbering and lifecycle cleanup.
- Combined served M3U and XMLTV outputs.
- Optional API-SPORTS schedule data for MLB, NFL, and NCAA football.
- Free public-country EPG fallback sources.
- Built-in TV Guide with Now/Next programme data and an eight-hour timeline.
- Browser playback through the local HLS relay.
- Chromecast / Google Cast handoff.
- Persistent multi-Roku discovery, saved Roku identities, and independent Roku playback sessions.
- Virtual HDHomeRun discovery and lineup support.
- Application-wide automatic update scheduling plus manual Update Now.

## Quick start

```bash
cp .env.example .env 2>/dev/null || true
docker compose up -d --build --force-recreate
```

Open:

```text
http://localhost:10000
```

The current v30 Compose stack also exposes host port 80 for HDHomeRun/Jellyfin bare-IP discovery compatibility.

To stop it:

```bash
docker compose down
```

Runtime state for the current v30 stack is stored in `./debug-data`. Do not delete that directory unless you intentionally want a fresh configuration.

## Main outputs

The two normal client-facing endpoints are:

```text
http://YOUR-SERVER-IP:10000/playlist/channels.m3u
http://YOUR-SERVER-IP:10000/epg/epg.xml
```

`channels.m3u` contains the curated manual lineup plus generated Sports Automation channels. `epg.xml` contains guide data only for channels that are actually being served.

Older output routes remain compatibility aliases where supported, but new client configuration should use the endpoints above.

## Providers

M3U Web Picker uses one primary provider as the normal channel catalog. The primary may be loaded from a direct M3U URL or from Xtream credentials.

Fallback providers are optional and are used by Sports Automation when a suitable feed is not available from the primary. They do not replace the primary Channel Manager catalog.

For Xtream providers, the UI can display account state and expiration metadata when the provider reports it. Credentials remain server-side and are stored in the local runtime configuration, so protect the data directory accordingly.

## Channels

Manual/static channels and generated sports channels are intentionally separate namespaces.

- Saved manual channels remain exactly as selected and ordered.
- Sports Automation never removes or replaces a saved manual channel because the stream happens to match.
- A generated sports channel is also allowed to coexist with a manual channel using the same underlying provider stream.
- Manual channels are ordered before generated sports channels.
- Generated sports channel numbers can use large values; the TV Guide keeps the number column separate from station logos and names.

## EPG

Provider guide data is preferred. User-configured guide sources and enabled free public-country guides can fill uncovered gaps without replacing higher-priority programmes that overlap the same time window.

The public-country guide selector is available from the EPG page. United States is enabled on a fresh configuration and additional countries can be enabled as needed.

## Sports Automation

Sports Automation creates temporary event channels from saved rules and removes them when they are no longer relevant.

Important behavior:

- Broad league/conference rules normally choose one best feed per game.
- Explicit team rules can expand the game into the requested home, away, national, or event feeds.
- Team + league overlap resolves to one logical event instead of duplicate channel blocks.
- Replays/classic games can be included or excluded.
- Replay airings reuse the same logical event/channel identity when enabled.
- Generated channels use a 90-minute postgame grace period before lifecycle cleanup.
- Clear filler rows such as `No Event Today`, `No Game Today`, and `Signing Off` are ignored.

API-SPORTS schedule integration is optional. When configured, the application can use canonical MLB, NFL, and NCAA football schedule data while retaining provider/XMLTV matching as the fallback path.

## Automatic updates

Overview contains the application-wide update schedule and status.

A master update refreshes the required schedule/provider/guide data, rebuilds generated sports channels, publishes M3U/XMLTV outputs, and records the result. Manual **Update Now** runs the same pipeline without changing the next scheduled run.

The sidebar status card shows provider state, channel counts, HDHomeRun state, saved Roku count, active streams, last update, next update, and the most recent update result.

## TV Guide

Open **TV Guide** from the sidebar.

The standalone guide uses the same saved application theme and includes:

- Search across channel and programme data.
- Current-time marker and programme timeline.
- Direct browser Play/Stop.
- Cast and Roku handoff for the current channel.
- Multi-Roku selection when more than one saved/discovered Roku is available.
- Back navigation to the app.

Provider stream URLs and credentials are not exposed in the guide payload.

## Roku and Cast

Roku playback uses the M3U Web Picker Roku receiver and the local ffmpeg-backed HLS relay. Saved Roku devices use stable Roku identity rather than IP address as identity, so a DHCP address change can be reconciled during discovery.

Each Roku has an independent playback session. Starting playback on Roku B does not stop Roku A.

Google Cast uses the same current-channel handoff model: select/play a channel first, then choose the remote receiver.

See [`CASTING.md`](CASTING.md) for device-specific details.

## HDHomeRun

The Devices page includes the virtual HDHomeRun controls and status. The application provides HDHomeRun-style discovery, device metadata, lineup data, and tuner configuration for compatible clients.

See [`PLEX-HDHR.md`](PLEX-HDHR.md) for additional integration notes.

## Updating the checkout

```bash
cd /Users/zacharyschmook/Desktop/repos/m3u-web-picker
git switch main
git pull --ff-only origin main
docker compose down
docker compose up -d --build --force-recreate
```

Persistent state is not removed by those commands.

## Tests

The repository includes regression tests for the application API, sports matching/lifecycle behavior, multi-Roku handling, update reporting, and the v30 UI contract.

Run the test suite with:

```bash
pytest
```

## Project structure

- `app.py` - Flask application entry point and UI routes.
- `api/` - application/device/output API routes.
- `sports/` - sports rules, matching, schedule integration, and generated-channel logic.
- `static/` - application, TV Guide, theme, and device UI assets.
- `templates/` - Flask templates.
- `roku-receiver/` - Roku receiver application.
- `tests/` - regression tests.

## Notes

This project is intended for streams and guide data that you are authorized to access. M3U Web Picker does not provide IPTV service, provider credentials, or channel subscriptions.
