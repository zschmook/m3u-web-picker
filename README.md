# M3U Web Picker — Sports Automation v20.2

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

## Private test data warning

This test bundle preserves the `config.json` and cached provider playlist from the local repository you uploaded so the sports screen has real data immediately. Provider stream URLs can contain account credentials. Do not publish or send this ZIP to other people. To make a clean distributable copy, remove `config.json`, `master_playlist_cache.m3u`, `epg_cache.xml`, and generated playlists before building or sharing it.

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
6. Rewrites `custom.m3u` atomically.

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
