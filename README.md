# M3U Web Picker

**Sports build v20.8:** the exact served M3U and XMLTV exports are now validated after every sports scan, generated XMLTV channels include a numeric mapping alias, and live/replay programme markers are emitted. The v20.7 stable slot IDs and channel-before-programme ordering remain included.

A single-page Flask application for loading an M3U playlist, selecting channels, ordering the custom playlist, and automatically generating a daily sports channel block.

## Run the separate sports test container

This is the easiest way to test without touching an existing instance:

```bash
docker compose -f docker-compose.sports-debug.yml up -d --build
```

Open:

```text
http://localhost:10000
```

Docker Desktop shows it separately as:

```text
m3u-picker-sports-test
```

Source files are bind-mounted into the debug container, so Flask reloads after local code edits while the debug database remains inside the container.

View logs:

```bash
docker compose -f docker-compose.sports-debug.yml logs -f
```

Stop only the test instance:

```bash
docker compose -f docker-compose.sports-debug.yml down
```

## Run the normal instance

```bash
cp .env.example .env
mkdir -p backups
docker compose up -d --build
```

Open `http://localhost:9999`.

The normal Compose file runs Waitress and creates a verified SQLite backup each night at the configured `BACKUP_TIME` and `BACKUP_TIMEZONE`, without a host cron job. The live database stays inside the container at `/app/m3u_picker.db`; only backup copies are written through the `/backups` bind mount.

## Sports automation behavior

The Sports Automation section sits directly below the existing Channel Manager. It is not a tab and does not add a sidebar.

Settings auto-save silently to SQLite. There is no Save button. The interface only displays an error when a setting cannot be saved. Refresh time is stored as a canonical 24-hour `HH:MM` value and formatted by the browser for display.

Fresh installs start with **zero sports selections**. The cached catalog contains available choices, but the application never assumes which teams, leagues, conferences, or sports the user follows.

Use **Add selection** to filter by type and sport, search the cached catalog, select one or more items, and add them to the nightly rules. Team names and logos are also discovered from permanent provider team feeds and cached in SQLite. Removing the final rule leaves the list empty after refreshes and container restarts.

The top **Enable sports** switch is the master control. **Auto update** controls the nightly schedule, while **Update now** performs the same refresh immediately even when Auto update is off. Update now is disabled only when the master switch is off.

At the configured update time, the application:

1. Refreshes the provider M3U when a URL source is configured.
2. Tries to derive and cache the provider's Xtream XMLTV feed.
3. Matches the current sports day's events against saved rules.
4. Builds home, away, national, event, Spanish, and backup feed variants when the provider data makes them identifiable.
5. Replaces the previous generated sports rows in one SQLite transaction.
6. Generates matching XMLTV guide data for every temporary sports channel.
7. Rewrites `custom.m3u`, `sports.xml`, and `combined.xml` as one replacement operation.

A failed provider refresh or failed scan leaves the previous generated sports channels intact. A successful scan with zero matches removes stale sports channels.

Generated sports channels begin at the user-selected channel number, default `1000`. Each event reserves a configurable block, default `10`, so late-added alternate feeds do not renumber every following event.

The Channel Manager displays generated sports entries automatically. Those rows are checked and locked because they are controlled by Sports Automation rather than manual selection. Manually selected channels keep their normal ordering and numbering.

## Current matching strategy

This MVP uses provider data first:

- M3U channel names and groups
- M3U `tvg-id`, `tvg-name`, and `tvg-logo`
- Dynamic event dates embedded in provider channel names
- Permanent team streams for home/away feed association
- XMLTV programme data when the Xtream XMLTV endpoint is available

It does not require an external sports API. The cached catalog is designed so external metadata enrichment can be added later without changing the UI or rule model.




## v20.8 served-guide validation and Jellyfin mapping

After every successful sports scan, the application validates the actual `custom.m3u`, `sports.xml`, and `combined.xml` files that Jellyfin receives. The scan response includes a credential-free `guide_check` object, and the same report is available at:

```text
http://HOST:PORT/api/sports/guide-check
```

For every generated sports slot, the check confirms that the M3U contains the expected `tvg-id`, both XMLTV exports contain the matching `<channel>`, at least one valid `<programme>` exists, and the scheduled event start is covered by a programme interval. No stream URLs are returned by the diagnostic.

Generated XMLTV channels now include the friendly event name plus a purely numeric display-name alias such as `1000`, improving Jellyfin channel-number mapping. Live event programmes include `<live />`; replay programmes include `<previously-shown />`.

Jellyfin keeps a local XMLTV download cache for up to one hour. During rapid testing, a tuner refresh can show newly generated channels while the guide task still reads the previous cached XMLTV file. Restarting Jellyfin or clearing its XMLTV cache forces an immediate re-download; normal scheduled operation naturally ages past that cache window.

## v20.7 Jellyfin guide mapping fix

Temporary sports channels now use durable XMLTV IDs based on their assigned channel slots, such as `m3u-picker-sports-1000`. Event names and provider stream URLs change from scan to scan, but the numbered slot remains stable, allowing Jellyfin to retain the correct guide mapping while the event occupying the slot changes.

The combined XMLTV writer now inserts generated `<channel>` records before the provider's first `<programme>` record and inserts generated `<programme>` records before `</tv>`. This preserves XMLTV's channel-first document order. Previous builds appended both kinds of records at the end, which produced parseable XML but could cause Jellyfin to ignore the generated channel definitions and leave the guide blank.

Each generated channel is tested to have exactly one programme covering its scheduled first-pitch time. Existing v20.4-v20.6 database rows are migrated to the stable slot IDs automatically.

Team-priority ordering remains a planned feature and is not implemented in this build.

## v20.6 baseball classification fix

- Shared provider groups named `MLB / MiLB` are not treated as a league by themselves.
- Explicit `MLB` or `MiLB` event metadata wins when present.
- Otherwise, both matchup participants must resolve inside the same league catalog before the event is classified.
- Standard abbreviations for all 30 MLB teams map to canonical full team names, so XMLTV titles such as `CHW at TB` render as `Chicago White Sox at Tampa Bay Rays`.
- MLB and MiLB rules remain independent, and duplicate M3U/XMLTV versions of the same matchup converge on the same event identity when their date matches.

## v20.4 generated sports guide data

Every generated sports feed now receives a unique, credential-free `tvg-id`. The same ID appears in the M3U and in generated XMLTV `<channel>` and `<programme>` records.

The app exposes two guide endpoints:

```text
http://HOST:PORT/epg/sports.xml
http://HOST:PORT/epg/combined.xml
```

- `sports.xml` contains only the temporary sports channels. Add this as a second XMLTV guide source when the existing provider guide is already configured in Jellyfin.
- `combined.xml` contains the cached provider XMLTV guide plus the generated sports channels. Use this as the single Jellyfin guide source when you want the app to provide both normal and sports guide data.

The custom M3U response also advertises `combined.xml` in its `url-tvg` and `x-tvg-url` header attributes. Jellyfin may still require adding the XMLTV guide URL explicitly in Live TV settings.

For scheduled events, each feed receives:

- an **Upcoming** programme beginning 24 hours before the event,
- a live event programme using a league-specific estimated duration, and
- a two-hour post-event channel window.

When an exact event time is unavailable, the generated guide supplies a broad placeholder programme instead of leaving the Jellyfin guide row blank. Logos and feed-specific subtitles are included. The **Update now** button is disabled and replaced by a spinner while scanning.

## v20.3 parser resilience hotfix

- A malformed timestamp in one M3U event no longer aborts the entire sports update.
- Invalid XMLTV programme timestamps are skipped independently.
- Successful scans report how many malformed provider entries were skipped.
- Docker logs identify a limited set of offending channel/programme names without printing stream URLs.
- Debug containers print a complete traceback for unexpected system-level failures.
- Existing generated sports channels are still replaced only after the new scan completes successfully.

## v20.2 QA fixes

- No demo sports rules are inserted into new databases; the untouched v20.1 demo set is removed once during upgrade.
- Removing every rule no longer causes demo selections to return.
- The Add Selection dialog is compact, searchable, sport-filtered, logo-aware, and supports adding several choices at once.
- Dark-theme labels and headings remain readable.
- Successful URL/file loads clear the visible source field and show `Source loaded.`
- Invalid refresh times cannot corrupt the scheduler or break a manual update.
- User-facing update failures no longer expose Python exceptions or credential-bearing URLs.
- Stream URLs shown in the channel table mask both Xtream path credentials.

## Manual backup

```bash
docker compose exec -T m3u-picker python /app/backup_db.py
```

## Local Python run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py --dev
```

Open `http://localhost:9998`.
