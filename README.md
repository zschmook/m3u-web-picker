# Quick Start

Requires **Git** and **Docker Desktop / Docker Compose**.

## macOS

```bash
cd ~/Desktop && git clone --branch sports --single-branch https://github.com/zschmook/m3u-web-picker.git && cd m3u-web-picker && docker compose up -d --build
```

## Linux

```bash
cd ~/Desktop && git clone --branch sports --single-branch https://github.com/zschmook/m3u-web-picker.git && cd m3u-web-picker && docker compose up -d --build
```

## Windows PowerShell

```powershell
cd ([Environment]::GetFolderPath("Desktop")); git clone --branch sports --single-branch https://github.com/zschmook/m3u-web-picker.git; cd m3u-web-picker; docker compose up -d --build
```

Once started, open **http://localhost:9999**.

---
# M3U Web Picker
### v22.1-rc2

**Release candidate:** see `RELEASE-NOTES-v22.1-rc2.md`.

- Makes the schedule API event ID the hard logical-game identity before provider/XMLTV airing grouping. A same-matchup rebroadcast can no longer allocate a second sports channel block when the API identifies it as the same game.
- API-assisted live-airing selection now evaluates provider rows around the canonical API start and rejects obvious supporting content such as pre/postgame, Gameday/in-game wagering, Squeeze Play, betting/odds, previews, recaps, and studio shows as the primary game stream.
- Provider rows are clustered with a tight 90-second tolerance in API mode so a 6:00 PM support program is not merged into a 6:05 PM first-pitch candidate.
- With replays/encores disabled, later same-matchup airings are dropped. With replays enabled, later airings are attached as replay programme windows on the same API event/channel IDs instead of generating new channel blocks.
- Regression coverage includes the real Aug. 8 Phillies case (6:00 betting Gameday → 6:05 live game → 11:00 rebroadcast) and the Dodgers Squeeze Play false match.
- The three-file MLB fixture (API schedule + sanitized provider M3U + filtered public U.S. EPG) now resolves 15 API games into 15 logical events and 17 generated feeds for All MLB + explicit Phillies.

- Schedule API enable/disable now persists immediately from the switch. API URL/key controls are disabled while the API is off, and configured schedule APIs appear in a proper Loaded Schedule APIs table with status, URL, last update, and Remove action.
- Fixes dark-mode contrast for the Free Public EPG country section header and country labels.
- Replaces the separate sports/provider/EPG schedules with one application-wide **Master Update**, enabled by default at **3:00 AM local time** and user-changeable from the top of the page below the M3U URL.
- **Update Now** runs the same dependency-ordered pipeline without changing the next scheduled daily run.
- Master Update now shows a spinner and live elapsed timer while a manual or scheduled cycle is running, and the last completed cycle records/displays its total duration.
- Master pipeline order is schedule API (optional) → provider refresh → guide refresh → sports match → channel build → XMLTV publish → M3U publish → validation.
- Adds **Free Public EPG — By Country** under EPG Manager. It is collapsed by default, checkbox-only, persists immediately, and starts with **United States enabled only**.
- Public country guides use IPTV-EPG's compressed `.xml.gz` feeds, remain compressed on disk, and are stream-decompressed while matching/filtering so giant guides are not expanded into memory or permanent raw XML files.
- Free public country guides are system-wide lowest-priority fallback/enrichment sources for every selected manual channel, not a sports-only feature. Provider/configured guide data wins on overlapping time windows; public guide programmes can fill uncovered gaps on the same channel. The same filtered public data remains available for sports corroboration.
- Corrects the API-BASEBALL free-plan fetch shape to the known-good **date + timezone** request and filters MLB (`league.id == 1`) locally.
- The schedule API remains optional. Disabled, blank, or incompletely configured API settings use the original provider-derived matcher and ignore canonical API cache data.
- Provider Sources keeps separate **Last Updated** and **Status** columns.
- Narrow filler filtering now also recognizes **No Game Today** alongside No Event(s) Today and Signing Off.

**Version 22.1-rc2**

- Builds on v22.1-debug1's optional canonical MLB schedule layer and v22.0's logical-event/feed-selection work.
- API schedule storage remains source/league keyed so later adapters can support NFL/NCAA, basketball, hockey, soccer, and other sports without redesigning the cache model.
- Broad league/conference/sport rules still generate one best feed per game, while explicitly selected teams can expand home/away/national/event feeds without duplicate logical events.
- Retains 90-minute postgame cleanup, manual/static channel isolation, replay/encore grouping, primary/fallback provider precedence, live-only Xtream imports, and scan-local matching indexes.
- Fresh debug state remains recommended for RC validation.

This is a single-page Flask application for loading an M3U playlist, manually selecting channels, ordering the custom playlist, and automatically generating temporary scheduled sports channels with matching XMLTV guide data.


## Channel Manager ordering

Manually saved/provider channels are always listed before generated sports and event channels. Generated entries remain read-only and retain their assigned automation channel numbers.

## Channel source privacy

The Channel Manager displays a short source badge such as `AstraNet` or `Sports Automation`. Stream URLs, paths, query strings, tokens, and credentials are never rendered in the channel table.

## Primary and fallback providers

Load exactly one primary provider from a direct M3U URL or an Xtream login. The primary playlist is the only provider catalog shown in Channel Manager, and saved manual channels continue to resolve only against that primary catalog.

Add one or more fallback providers below the primary. Sports Automation refreshes and scans them in priority order. Feed eligibility is evaluated first; after disabled backup feeds and other settings are removed, the lowest provider priority with a usable candidate wins. This means a fallback does not add duplicate or extra sports feeds when the primary can satisfy the event.

For Xtream logins, enter the server/base URL separately from the username and password. The application probes the conventional authentication endpoint, requests live streams and live categories through the Xtream API, excludes VOD/series records, and derives the XMLTV endpoint internally. A fallback provider is registered after a lightweight validation and does not download its channel catalog until Master Update runs. Direct M3U URLs with embedded credentials remain supported for backward compatibility behind the same size safeguards. Saved provider rows expose only a friendly provider label, type, live-channel count, priority, refresh status, and—when reported by Xtream—account status and expiration date.

Separate credential fields prevent accidental UI/API exposure; they are not an encryption layer. Credentials are stored in the application's local `config.json`, so the runtime data directory and backups should be protected like any other secret-bearing configuration.


## EPG Manager and guide recovery

The EPG Manager appears above Channel Manager in one unified table. The add-source controls, the built-in **Combined** guide, and named external XMLTV sources share fixed columns for Name, Type, Served URL, Status, and Action. Stored provider URLs and credentials remain hidden after saving; only the friendly source label and app-served URL are displayed.

`combined.xml` is the single user-facing Jellyfin/Plex guide. The sports-only XMLTV file is still generated internally for diagnostics and validation, but it is no longer advertised as a normal EPG Manager output. If an empty or stale guide file exists while generated sports rows are present, the application rebuilds it before serving Jellyfin. A manually configured EPG source is used as a fallback when the conventional Xtream `xmltv.php` URL cannot be derived or refreshed.

### Master Update and free public country guides

The only automatic update clock exposed by the UI is **Master Update**, directly below the served M3U URL. It defaults to 03:00 in the configured local sports timezone and can be moved to run before an external Jellyfin/Plex guide refresh. Manual **Update Now** does not alter the next scheduled daily time.

**Free Public EPG — By Country** is an expandable checkbox list inside EPG Manager. United States is enabled on a fresh configuration and every other country starts disabled. Enabled countries use the built-in IPTV-EPG registry and compressed `epg-<country>.xml.gz` sources. The application caches those compressed files once per local day, line-streams the large gzip to create a compact filtered gzip containing only relevant manual/provider/sports channel IDs and names, and hands that compact subset to the normal XMLTV matcher. Only useful selected-channel/programme data reaches `combined.xml`; the full public guide is never intentionally expanded to a permanent XML file or parsed into one giant in-memory XML tree.

Guide precedence is provider/base guide first, then user-configured guide sources, then enabled free public-country guides. Precedence is applied per channel **and time window**: a lower-priority source may fill an uncovered gap on a selected channel, but any programme that overlaps a higher-priority programme is discarded. This lets a public guide repair partial provider-guide holes without replacing good provider metadata.

## Persistent Docker state

The debug Compose stack stores runtime state in `./debug-data`. The normal stack stores it in the named volume `m3u-picker-data`. Rebuilding or using `--force-recreate` no longer erases the SQLite database, cached source, generated playlist, or guide exports. Runtime data remains excluded from Git and release ZIPs.

## Jellyfin URLs

The M3U creates the channels. Jellyfin still needs an XMLTV guide source.

```text
http://YOUR-SERVER-IP:10000/playlist/custom.m3u
http://YOUR-SERVER-IP:10000/epg/combined.xml
```

Use `combined.xml` for everything: selected manual channels use provider/configured guide data first and enabled free public-country guides as fallback, while generated sports channels are merged into the same file. The provider's full XMLTV catalog is never copied into this output.

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

The master **Enable sports** switch controls whether sports-generated channels participate in the application-wide update. Scheduling itself is owned by **Master Update** at the top of the page: one configurable daily run defaults to **3:00 AM local time**. **Update Now** runs that same full pipeline immediately and does not move the next scheduled daily run. There is no hourly/every-X-hours UI.

Turning Sports Automation off immediately removes generated sports rows from the served M3U and XMLTV files. The generated data remains privately cached for 24 hours so an accidental toggle can be reversed immediately. Saved sport, league, conference, and team rules are never deleted.

The global **Hide SD / LOW BANDWIDTH channels** control now also excludes SD feeds from sports generation.

At update time the application:

1. Refreshes/checks the optional canonical sports schedule API cache.
2. Refreshes the primary/fallback provider M3U catalogs as required.
3. Refreshes provider, configured, and enabled public-country EPG caches.
4. Parses M3U/XMLTV and matches provider airings against canonical events when available.
5. Matches events against saved sports rules and builds the selected feed set.
6. Assigns generated channels inside the event’s league/series range.
7. Builds filtered Sports and Combined XMLTV output, with provider guides ahead of public fallback guides.
8. Replaces generated database rows/XMLTV through the existing guarded publish path, atomically writes the M3U, and validates the cycle order/output.

A failed refresh or scan preserves the previous working sports lineup. A successful scan with zero matches clears stale sports output.

Clear provider filler titles matching **No Event Today**, **No Events Today**, **No Game Today**, or **Signing Off** are excluded case-insensitively after punctuation normalization. Legitimate scheduled programming, including podcasts and studio shows, is not broadly filtered. Generated channels remain available until 90 minutes after the known or estimated event end so long games and overtime are not removed early.

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
