> **v30-experiments / exp11-roku-bundled — DEBUG ONLY:** Full standalone experiment on `http://localhost:10000`. It uses its own Compose project/container and local `./debug-data`; it does not mount the stable main instance's Docker volume. This build includes the pop-out TV Guide, ffmpeg-backed browser playback, Chromecast playback, and experimental Roku playback.

# M3U Web Picker

### v22.1-rc8


- RC8 adds an experimental **TV Guide** pop-out link in the top header. The first pass shows the exact currently curated/served lineup in channel order and provides an in-window browser Play/Stop control without exposing provider stream URLs in the guide payload.
- The guide is intentionally a small first step: channel list + player now, with time-grid/Now/Next guide work left for the next iteration after playback behavior is tested against real provider streams.
- RC7 moves **Manage Order** beneath the EPG output row so the sticky header reads more cleanly.
- Renames the replay option to **Include replays and classic games**.
- A successful Schedule API window is now authoritative for its supported league when replays/classics are off: provider/XMLTV rows that cannot be mapped to a canonical current game are suppressed instead of inventing historical/offseason games. API errors, plan/date restrictions, auth failures, quota failures, and stale caches remain non-authoritative and continue to fall back to legacy matching.
- Preseason/spring-football API behavior is intentionally unchanged in RC7 pending live API verification when the dates enter the free-plan window.
- RC6 tightens the sticky top header: the M3U Web Picker brand stays top-aligned instead of vertically floating beside the taller controls.
- Expands the right-side Channels/EPG/Master Update control area on desktop so the Next / Last / Took / Timezone status stays on one line when space is available; responsive wrapping remains for narrower windows.

- Expands the optional Schedule API layer from MLB-only into a quota-first **API-SPORTS** request planner. API-SPORTS is the only supported schedule-data provider in RC5; the UI links directly to https://api-sports.io.
- Removes the user-entered Schedule API base URL. The user supplies only the API-SPORTS key; M3U Web Picker owns the product endpoints internally and derives the needed products from existing Sports Automation selections.
- Current API-backed schedule adapters: **MLB** through API-SPORTS Baseball, plus **NFL** and **NCAA football** through API-SPORTS American Football. Unsupported or intentionally legacy-only sports (for example golf and track & field) continue through the existing provider/XMLTV matcher without making an API call.
- Collapses overlapping rules into the minimum unique schedule datasets. Selecting a league, conferences, and multiple teams does not create one request per rule; rules are filtered locally against the already-fetched league/day slate.
- Caches API schedule results by provider/product/endpoint plus normalized request parameters (including date/season/timezone where applicable) and reuses same-day data across normal Master Update and repeated **Update Now** runs. **Refresh API schedules** is an explicit advanced control that bypasses only the same-day schedule cache.
- Caches NCAA conference membership as long-lived reference data so Big Ten, ACC, SEC, and explicit team rules can be evaluated locally against one NCAA schedule dataset. Existing seeded conference membership remains a fallback if reference data cannot be refreshed.
- API failures first preserve/reuse relevant cached canonical schedules when possible; if no usable canonical schedule is available, the affected sport falls back to the existing provider/EPG matcher instead of taking down the lineup.
- The Schedule API table reports each planned dataset independently. **Cached**, **No successful cache**, **Refresh failed**, **Partial refresh**, and **Using cached fallback** are distinct states; a configured API key no longer makes every dataset misleadingly look active.
- The most recent per-dataset refresh attempt and generic failure reason are persisted in the local sports database so a failed NFL/NCAA refresh remains visible after polling or page reload. API credentials are never included in this health payload.
- **Update Now** is disabled for the entire time any update pipeline is running, preventing overlapping refreshes or accidental double-click cancellation.
- Retains the RC4 sticky top control area, equal-width Channels/EPG labels, right-aligned status rows, and visible User Guide link.
- No cache/backup storage cleanup changes are included in RC5; that investigation remains separate.

[User Guide (PDF)](M3U-Web-Picker-v22.1-RC5-User-Guide.pdf)

- Makes the schedule API event ID the hard logical-game identity before provider/XMLTV airing grouping. A same-matchup rebroadcast can no longer allocate a second sports channel block when the API identifies it as the same game.
- API-assisted live-airing selection now evaluates provider rows around the canonical API start and rejects obvious supporting content such as pre/postgame, Gameday/in-game wagering, Squeeze Play, betting/odds, previews, recaps, and studio shows as the primary game stream.
- Provider rows are clustered with a tight 90-second tolerance in API mode so a 6:00 PM support program is not merged into a 6:05 PM first-pitch candidate.
- With replays/encores disabled, later same-matchup airings are dropped. With replays enabled, later airings are attached as replay programme windows on the same API event/channel IDs instead of generating new channel blocks.
- Regression coverage includes the real Aug. 8 Phillies case (6:00 betting Gameday → 6:05 live game → 11:00 rebroadcast), UTC-vs-Eastern schedule equivalence, and the Dodgers Squeeze Play false match.
- A provider description such as `Game 2 of 3` no longer creates a fake doubleheader identity. Only explicit Game 1/Game 2 wording in the event title can split a same-day matchup.
- Finished generated sports channels are pruned by a lightweight lifecycle cleanup after the same 90-minute postgame grace, so a once-daily master scan does not leave blank expired rows in Jellyfin for the rest of the day.
- Team-specific generated feeds prefer canonical schedule-API team artwork when available; event/national feeds retain provider/network artwork.

- Schedule API enable/disable persists immediately from the switch. RC5 uses one API-SPORTS key and derives product URLs internally; the planned-dataset table shows provider, product, scope, cache status, and last update.
- Fixes dark-mode contrast for the Free Public EPG country section header and country labels.
- Replaces the separate sports/provider/EPG schedules with one application-wide **Master Update**, enabled by default at **3:00 AM local time** and user-changeable from the top of the page below the M3U URL.
- **Update Now** runs the same dependency-ordered pipeline without changing the next scheduled daily run.
- Master Update now shows a spinner and live elapsed timer while a manual or scheduled cycle is running, and the last completed cycle records/displays its total duration.
- Master pipeline order is schedule API (optional) → provider refresh → guide refresh → sports match → channel build → XMLTV publish → M3U publish → validation.
- Keeps **Free Public EPG — By Country** as the normal guide-configuration section. It is collapsed by default, checkbox-only, persists immediately, and starts with **United States enabled only**.
- Public country guides use IPTV-EPG's compressed `.xml.gz` feeds, remain compressed on disk, and are stream-decompressed while matching/filtering so giant guides are not expanded into memory or permanent raw XML files.
- Free public country guides are system-wide lowest-priority fallback/enrichment sources for every selected manual channel, not a sports-only feature. Provider/configured guide data wins on overlapping time windows; public guide programmes can fill uncovered gaps on the same channel. The same filtered public data remains available for sports corroboration.
- Corrects the API-BASEBALL free-plan fetch shape to the known-good **date + timezone** request and filters MLB (`league.id == 1`) locally.
- The schedule API remains optional. Disabled or keyless configuration uses the original provider-derived matcher and ignores canonical API cache data. Unsupported sports always retain that legacy matcher even when API-SPORTS is enabled for other selections.
- Provider Sources keeps separate **Last Updated** and **Status** columns.
- Narrow filler filtering now also recognizes **No Game Today** alongside No Event(s) Today and Signing Off.

**Version 22.1-rc8**

- Builds on v22.1-debug1's optional canonical MLB schedule layer and v22.0's logical-event/feed-selection work.
- API schedule storage remains source/league keyed so RC5 adapters support NFL/NCAA alongside MLB, while additional sports can still be added later without redesigning the cache model.
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


## EPG output and guide recovery

The normal UI exposes the final **EPG** URL beside the Channels URL at the top of the page. Provider guide discovery remains automatic, while the visible guide configuration is the worldwide public-country fallback selector.

`epg.xml` is the single user-facing Jellyfin/Plex guide. The sports-only XMLTV file is still generated internally for diagnostics and validation, but it is not advertised as a normal user-facing output. If an empty or stale guide file exists while generated sports rows are present, the application rebuilds it before serving Jellyfin. A manually configured EPG source is used as a fallback when the conventional Xtream `xmltv.php` URL cannot be derived or refreshed.

The public endpoints are `/playlist/channels.m3u` and `/epg/epg.xml`. The former `/playlist/custom.m3u` and `/epg/combined.xml` routes remain compatibility aliases for existing clients, but the UI and documentation advertise only the new names.

### Master Update and free public country guides

The only automatic update clock exposed by the UI is **Master Update**, directly below the served Channels and EPG URLs. It defaults to 03:00 in the configured local sports timezone and can be moved to run before an external Jellyfin/Plex guide refresh. Manual **Update Now** does not alter the next scheduled daily time.

**Free Public EPG — By Country** is an expandable checkbox list in its own guide section. United States is enabled on a fresh configuration and every other country starts disabled. Enabled countries use the built-in IPTV-EPG registry and compressed `epg-<country>.xml.gz` sources. The application caches those compressed files once per local day, line-streams the large gzip to create a compact filtered gzip containing only relevant manual/provider/sports channel IDs and names, and hands that compact subset to the normal XMLTV matcher. Only useful selected-channel/programme data reaches `epg.xml`; the full public guide is never intentionally expanded to a permanent XML file or parsed into one giant in-memory XML tree.

Guide precedence is provider/base guide first, then user-configured guide sources, then enabled free public-country guides. Precedence is applied per channel **and time window**: a lower-priority source may fill an uncovered gap on a selected channel, but any programme that overlaps a higher-priority programme is discarded. This lets a public guide repair partial provider-guide holes without replacing good provider metadata.

## Experimental Roku playback

The TV Guide can send the currently playing channel to a Roku TV on the same LAN. This is still an experimental/developer-mode workflow; the normal M3U Web Picker instance is unchanged.

### 1. Enable Roku developer mode

On the Roku home screen, press this sequence on the remote:

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

Choose **Enable installer and restart**, accept the developer agreement, and set a developer password when prompted. After the Roku restarts, note its LAN IP address.

If local Roku control is disabled, enable **Control by mobile apps** under the Roku's advanced system/network settings.

### 2. Install the M3U Web Picker Roku receiver

From a computer on the same LAN, open the Roku's IP address in a browser:

```text
http://ROKU-IP
```

Log in with:

```text
Username: rokudev
Password: <the developer password set on the Roku>
```

In the Roku **Development Application Installer**, choose **Install with zip** and upload:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver-exp1.zip
```

Do not extract that receiver ZIP first. Roku permits only one sideloaded developer application at a time, so installing another development app will replace it.

### 3. Send a channel from the TV Guide

Start the experimental Docker build and open:

```text
http://localhost:10000/guide
```

Then:

1. Press **Play** on a channel so it becomes the current guide channel.
2. The guide automatically discovers verified Roku ECP devices on the current experimental LAN. Diagnostics still shows the selected Roku IP/name and retains **Test Roku** for troubleshooting.
3. Press **Roku** beside the Cast control to launch the sideloaded receiver and send the current channel to the TV.
4. Press **Disconnect Roku** to stop Roku playback, return the Roku home screen, and resume the same current channel locally.

The Mac remains the media relay: the provider stream is normalized by ffmpeg into H.264/AAC HLS, and the Roku pulls that HLS stream over the LAN. Provider credentials and raw stream URLs remain server-side. Direct Cast↔Roku switching is serialized so the old remote target is fully torn down before the new one starts. Multiple-Roku selection is a required follow-up: 0 devices hides the control, 1 device is direct, and 2+ devices will use a selector with the last-used Roku preselected.

## Persistent Docker state

The debug Compose stack stores runtime state in `./debug-data`. The normal stack stores it in the named volume `m3u-picker-data`. Rebuilding or using `--force-recreate` no longer erases the SQLite database, cached source, generated playlist, or guide exports. Runtime data remains excluded from Git and release ZIPs.

## Jellyfin URLs

The M3U creates the channels. Jellyfin still needs an XMLTV guide source.

```text
http://YOUR-SERVER-IP:10000/playlist/channels.m3u
http://YOUR-SERVER-IP:10000/epg/epg.xml
```

Use `epg.xml` for everything: selected manual channels use provider/configured guide data first and enabled free public-country guides as fallback, while generated sports channels are merged into the same file. The provider's full XMLTV catalog is never copied into this output.

The examples use the experimental/debug port `10000`; the normal Compose instance uses `9999`. The Channels M3U advertises `epg.xml`, but Jellyfin may still require adding the XMLTV URL explicitly under Live TV guide sources.

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

Sports are grouped by a stable 1,000-channel primary block for each league. With the default 10 channels per event, a primary block holds 100 events.

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

The complete map is visible inside Sports Automation under **View league channel blocks**, with a Sport filter. A competition never spills into the next competition’s range. The rare 101st event is moved into a separate high-number continuation block and logged instead of being silently truncated.

Changing **First league block** shifts the complete map while preserving the 1,000-channel spacing. Changing **Channels per event** changes the number of event slots available inside each range.

## Sports taxonomy

The Add Sports Selection dialog supports four levels:

```text
Sport
League
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
6. Assigns generated channels inside the event’s league range.
7. Builds the internal sports guide and final EPG output, with provider guides ahead of public fallback guides.
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

## Provider streams with optional schedule authority

Provider data always supplies the actual streams and remains sufficient when no schedule API is configured:

- M3U channel names and groups
- `tvg-id`, `tvg-name`, `tvg-logo`, and embedded event dates
- permanent team feeds for feed association
- XMLTV programme titles, categories, and start times

When the optional schedule API is enabled and returns a matching game, its event ID and scheduled start become authoritative for logical-game identity. Provider/XMLTV rows are then treated as candidate airings of that game. If the API is configured but supplies no canonical events for the current window, the scan reports that it fell back to legacy provider-derived matching instead of silently looking API-backed.

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


Experimental TV Guide Cast/Roku support lives only on the `experiments` branch until intentionally promoted.


### v30 experiments LAN/Cast test

This disposable build publishes port 10000 on all host interfaces. On the test Mac its configured LAN URL is `http://10.0.0.22:10000`. Use `http://localhost:10000/guide` for the Google Cast sender UI; the Chromecast/Roku fetches the selected HLS relay from `http://10.0.0.22:10000`.

### v30 experiments LAN note

This test machine is currently `10.0.0.22` on `en0`, so Chromecast/Roku HLS is advertised as `http://10.0.0.22:10000`. The Cast controller should still be opened at `http://localhost:10000/guide` on the Mac. If the Mac moves to another LAN, run `./scripts/detect-lan-host.sh` to see the new address and override `M3U_LAN_HOST` when starting the experiment rather than assuming a `192.168.*` network.
