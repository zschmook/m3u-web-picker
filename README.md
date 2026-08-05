# M3U Web Picker
### v22.0-rc1

- Starts the v22 line from the debug10 logical-event and scan-index foundation.
- Broad league/conference/sport rules now generate one best feed per game by default.
- Games involving an explicitly selected team receive the expanded home/away/national/event feed set, with the team rule controlling feed preference.
- Team and league overlap remains one logical event and never allocates duplicate channel blocks.
- The entire add-primary fieldset is locked whenever a URL or file primary exists, including Xtream username/password and both load actions.
- Xtream provider rows show provider-reported account status and expiration date in **Last Updated / Status** without exposing credentials or connection counts.
- Retains daily or every-X-hours Sports Automation scheduling, 90-minute postgame cleanup, narrow off-air filtering, replay/encore grouping, primary/fallback provider precedence, and live-only Xtream imports.
- Retains scan-local indexes and stage timings; the current real-world debug10 benchmark was about 4m30s–4m50s for a primary with roughly 6,500 live channels.
- RC validation should begin with a fresh test state so stale Jellyfin mappings and migrated generated rows do not contaminate results.

**Version 22.0-rc1**

This is the first v22 release candidate. It promotes the tested v22.0-debug1 application behavior without functional changes: compact league-wide feed generation, expanded feeds for explicitly selected teams, a complete primary-provider UI lock, Xtream account status/expiration display, replay-aware logical event grouping, and indexed sports scanning.

This is a single-page Flask application for loading an M3U playlist, manually selecting channels, ordering the custom playlist, and automatically generating temporary scheduled sports channels with matching XMLTV guide data.


## Channel Manager ordering

Manually saved/provider channels are always listed before generated sports and event channels. Generated entries remain read-only and retain their assigned automation channel numbers.

## Channel source privacy

The Channel Manager displays a short source badge such as `AstraNet` or `Sports Automation`. Stream URLs, paths, query strings, tokens, and credentials are never rendered in the channel table.

## Primary and fallback providers

Load exactly one primary provider from a direct M3U URL or an Xtream login. The primary playlist is the only provider catalog shown in Channel Manager, and saved manual channels continue to resolve only against that primary catalog.

Add one or more fallback providers below the primary. Sports Automation refreshes and scans them in priority order. Feed eligibility is evaluated first; after disabled backup feeds and other settings are removed, the lowest provider priority with a usable candidate wins. This means a fallback does not add duplicate or extra sports feeds when the primary can satisfy the event.

For Xtream logins, enter the server/base URL separately from the username and password. The application probes the conventional authentication endpoint, requests live streams and live categories through the Xtream API, excludes VOD/series records, and derives the XMLTV endpoint internally. A fallback provider is registered after a lightweight validation and does not download its channel catalog until Sports Update runs. Direct M3U URLs with embedded credentials remain supported for backward compatibility behind the same size safeguards. Saved provider rows expose only a friendly provider label, type, live-channel count, priority, refresh status, and—when reported by Xtream—account status and expiration date.

Separate credential fields prevent accidental UI/API exposure; they are not an encryption layer. Credentials are stored in the application's local `config.json`, so the runtime data directory and backups should be protected like any other secret-bearing configuration.


## EPG Manager and guide recovery

The EPG Manager appears above Channel Manager in one unified table. The add-source controls, built-in Combined and Sports guides, and named external XMLTV sources share fixed columns for Name, Type, Served URL, Status, and Action. Stored provider URLs and credentials remain hidden after saving; only the friendly source label and app-served URL are displayed.

The sports and combined guide endpoints now verify that their XML contains the persisted generated sports rows. If an empty or stale guide file exists while generated rows are present, the application rebuilds it before serving Jellyfin. A manually configured EPG source is used as a fallback when the conventional Xtream `xmltv.php` URL cannot be derived or refreshed.

## Persistent Docker state

The debug Compose stack stores runtime state in `./debug-data`. The normal stack stores it in the named volume `m3u-picker-data`. Rebuilding or using `--force-recreate` no longer erases the SQLite database, cached source, generated playlist, or guide exports. Runtime data remains excluded from Git and release ZIPs.

## Jellyfin URLs

The M3U creates the channels. Jellyfin still needs an XMLTV guide source.

```text
http://YOUR-SERVER-IP:10000/playlist/custom.m3u
http://YOUR-SERVER-IP:10000/epg/combined.xml
```

Use `combined.xml` for the selected manual channels' provider guide data plus generated sports programmes. v21.5 and later no longer copy the provider's entire XMLTV catalog into this file. Use the sports-only guide when the provider XMLTV source is already configured separately:

```text
http://YOUR-SERVER-IP:10000/epg/sports.xml
```

The examples use the sports debug port `10000`; the normal Compose instance uses `9999`. The custom M3U advertises `combined.xml`, but Jellyfin may still require adding the XMLTV URL explicitly under Live TV guide sources.

## Fresh debug state (recommended)

For v22 testing, remove the old test container and extracted directory, then recreate an empty `debug-data` directory. Re-enter the small test configuration manually so stale generated channels, provider caches, and Jellyfin mappings cannot affect results.

The legacy `scripts/migrate-debug-state.sh` helper remains available for deliberate migration tests, but it is not the default v22 workflow.

## Run the separate sports test container

```bash
docker compose -f docker-compose.sports-debug.yml up -d --build --force-recreate
```

Open `http://localhost:10000`. Docker Desktop lists the test container as `m3u-picker-sports-test`.

```bash
docker compose -f docker-compose.sports-debug.yml logs -f
docker compose -f docker-compose.sports-debug.yml down
```

## Run the normal instance

```bash
cp .env.example .env
mkdir -p backups
docker compose up -d --build
```

Open `http://localhost:9999`.

## Channel numbering

Sports are grouped by a stable 1,000-channel primary block for each league, series, tour, promotion, or division. With the default 10 channels per event, a primary block holds 100 events.

```text
MLB                          1000–1999
NHL                          2000–2999
NBA                          3000–3999
NFL                          4000–4999
MiLB                         5000–5999
NCAA Football — FBS          6000–6999
NCAA Football — FCS          7000–7999
NCAA Football — Division II  8000–8999
NCAA Football — Division III 9000–9999
```

The complete map is visible inside Sports Automation under **View league / series channel blocks**, with a Sport filter. A competition never spills into the next competition’s range. The rare 101st event is moved into a separate high-number continuation block and logged instead of being silently truncated.

Changing **First league block** shifts the complete map while preserving the 1,000-channel spacing. Changing **Channels per event** changes the number of event slots available inside each range.

## Sports taxonomy

The Add Sports Selection dialog supports four levels:

```text
Sport
League / series / tour / promotion / division
Conference
Team / competitor
```

Broad sport selections pull matching events from their child competitions, but generated channels remain numbered inside the child competition’s range.

Included first-class areas include:

- Major team sports: baseball, basketball, American football, hockey, and soccer.
- College football split into FBS, FCS, Division II, Division III, NAIA, NJCAA, and High School Football.
- International sports: cricket, rugby union, rugby league, curling, handball, field hockey, and international competitions.
- Golf, tennis, volleyball, softball, lacrosse, horse racing, bowling, billiards, darts, poker, and cornhole.
- Track and field, swimming, diving, water polo, artistic swimming, gymnastics, figure skating, speed skating, skiing, snowboarding, biathlon, and sliding sports.
- Cycling: Tour de France, Giro d’Italia, Vuelta a España, Tour of California, road classics, track cycling, mountain biking, cyclocross, BMX, world championships, and Olympic cycling.
- Motorsports: Formula 1/2/3/E, NASCAR Cup/Xfinity/Trucks, IndyCar, IMSA, WEC, MotoGP, superbikes, motocross, supercross, dirt bikes, rally, off-road, drag racing, and monster trucks.
- Combat and sports entertainment: UFC, PFL/Bellator, ONE Championship, regional MMA, WWE, AEW, TNA, NJPW, ROH, amateur wrestling, boxing, judo, taekwondo, and fencing.
- Olympic umbrella matching plus individual Olympic disciplines such as rowing, canoe/kayak, sailing, triathlon, archery, shooting, weightlifting, equestrian, badminton, table tennis, climbing, surfing, skateboarding, and modern pentathlon.

Pro wrestling is deliberately separate from collegiate/Olympic wrestling. Rugby union and rugby league remain separate. MLB and MiLB remain separate even when the provider uses a shared `MLB / MiLB` group.

## Sports Automation behavior

Sports Automation sits below Channel Manager and does not add a sidebar or second application surface. Both sections are collapsible and remember their browser state. Turning Sports Automation on always expands its controls; turning it off collapses them.

Settings save silently to SQLite. There is no Save button. Fresh installations start with zero sports selections.

**Everything Mode** is deliberately stored as its own SQLite setting rather than creating hundreds of normal rules. It is off by default. Turning it on scans every detected event while preserving the curated sport, league, conference, and team selections exactly as they were. Turning it off returns the next successful scan to the curated list. Broad-scope feed preferences and the backup-feed setting still control which variants are generated.

Manual scan activity is persisted in SQLite. Closing or reopening the browser tab does not lose the running state. The UI polls the backend, displays the current stage and elapsed time, and leaves a dismissible success or failure result after completion. An app restart converts an interrupted running state into a visible failure instead of leaving a permanent “already running” message.

The master **Enable sports** switch controls both automatic and manual generation. **Auto update** can run either once daily at the configured time or every 1–24 hours. Interval mode is anchored to the most recently completed attempt, so a manual **Update now**, a successful scheduled run, a failed run, or a cancelled run starts a fresh interval and prevents rapid retry loops. **Update now** still works when Auto update is off, disables duplicate clicks, and shows a looping `.` → `..` → `...` activity indicator while the backend works.

Turning Sports Automation off immediately removes generated sports rows from the served M3U and XMLTV files. The generated data remains privately cached for 24 hours so an accidental toggle can be reversed immediately. Saved sport, league, conference, and team rules are never deleted.

The global **Hide SD / LOW BANDWIDTH channels** control now also excludes SD feeds from sports generation.

At update time the application:

1. Refreshes the provider M3U when a URL source is configured.
2. Tries to derive and cache the provider XMLTV endpoint.
3. Parses M3U and XMLTV events without aborting on a single malformed entry.
4. Matches events against saved rules and deduplicates duplicate M3U/XMLTV descriptions.
5. Builds identifiable home, away, national, event, alternate-language, and backup feeds.
6. Assigns channels inside the event’s league/series range.
7. Replaces generated database rows and XMLTV exports atomically.
8. Rewrites the served custom playlist.

A failed refresh or scan preserves the previous working sports lineup. A successful scan with zero matches clears stale sports output.

Clear provider filler titles matching **No Event Today**, **No Events Today**, or **Signing Off** are excluded case-insensitively after punctuation normalization. Legitimate scheduled programming, including podcasts and studio shows, is not broadly filtered. Generated channels remain available until 90 minutes after the known or estimated event end so long games and overtime are not removed early.

## Generated guide behavior

Every generated feed receives a credential-free ID based on its assigned numbered slot, for example:

```text
m3u-picker-sports-1000
```

The same ID appears in the M3U and XMLTV `<channel>` and `<programme>` records. Scheduled events receive upcoming, live/event, and post-event guide coverage. Replay programmes are marked separately.

The credential-free guide validation report is available at:

```text
http://HOST:PORT/api/sports/guide-check
```

It verifies the actual served playlist and both XMLTV exports without returning provider stream URLs.

## Provider-first matching

The application uses provider data as its primary source:

- M3U channel names and groups
- `tvg-id`, `tvg-name`, `tvg-logo`, and embedded event dates
- permanent team feeds for feed association
- XMLTV programme titles, categories, and start times

No external sports API is required. Provider terminology varies, so the taxonomy includes common aliases while avoiding broad fuzzy matches that would mix unrelated sports.

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
