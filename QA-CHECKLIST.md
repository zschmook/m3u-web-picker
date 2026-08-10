# Sports v22.1-rc3 QA checklist

## Startup and layout

1. Start the debug container and open `http://localhost:10000`.
2. Confirm a fresh database has zero sports selections.
3. Collapse and expand Channel Manager. Reload and confirm the browser remembers the state.
4. Turn Sports Automation on and confirm it expands automatically.
5. Turn Sports Automation off and confirm it collapses and generated rows disappear immediately.

## Taxonomy and picker

1. Open **Add sports selection**. It should default to **League**.
2. Filter the list by Sport and confirm results are grouped under sport headings.
3. Confirm Cycling includes Tour de France, Giro d’Italia, Vuelta a España, Tour of California, track cycling, mountain biking, cyclocross, BMX, and Olympic cycling.
4. Confirm Motorsports includes F1/F2/F3/Formula E, NASCAR series, IndyCar, endurance racing, motorcycle racing, rally, drag racing, and monster trucks.
5. Confirm MMA and Pro Wrestling are separate from amateur/Olympic wrestling.
6. Confirm College Football offers FBS, FCS, Division II, Division III, NAIA, NJCAA, and High School Football separately.
7. Confirm Golf, Track & Field, Swimming, Figure Skating, Speed Skating, Gymnastics, and other Olympic disciplines appear as first-class choices.
8. Confirm Cornhole includes ACL, ACO, college, international, and made-for-TV choices.
9. Add and remove multiple selections. Reload and restart the container; the final rule list must persist exactly.
10. Confirm the league list shows a **Channel Range** heading over the right-hand ranges.
11. Confirm **I’M INSANE, ADD EVERYTHING!!!!** is unchecked on a fresh database.
12. Add a curated rule, enable Everything Mode, and confirm the curated rule remains unchanged. Disable Everything Mode and confirm the curated rule is still present.
13. Confirm Everything Mode is one persisted setting and does not create a row for every catalog item.

## Channel block map

1. Expand **View league channel blocks**.
2. Confirm MLB is `1000–1999`, NHL `2000–2999`, NBA `3000–3999`, NFL `4000–4999`, and MiLB `5000–5999` with default settings.
3. Confirm FBS, FCS, Division II, and Division III each have separate 1,000-channel ranges.
4. Change **First league block** and confirm every displayed range shifts by the same amount.
5. Change **Channels per event** and confirm the capacity message updates.
6. Run a scan containing MLB and NHL events. Confirm MLB channels remain in the 1000s and NHL channels remain in the 2000s.
7. Confirm no competition silently spills into the next competition’s range.

## Scan behavior

1. Confirm the only user-facing automatic clock is **Master Update** below the Channels and EPG URLs and that a fresh state defaults to 03:00 local time.
2. Change the Master Update time a few minutes ahead and confirm the displayed next-update time changes immediately and the complete pipeline runs once at that minute.
3. Run **Update Now** and confirm it executes immediately without moving the next configured daily update.
4. Confirm the button is disabled/changes state while a persistent status card shows stage and elapsed time.
5. Run twice with different source data and confirm sports rows are replaced rather than accumulated.
6. Simulate a failed provider refresh and confirm the previous generated lineup remains.
7. Perform a successful scan with zero matches and confirm stale sports output is cleared.
8. Add an impossible M3U time such as `99:99:00`; valid events must continue processing.
9. Repeat with an invalid XMLTV timestamp; later valid programmes must still process.
10. Close the browser tab during a long update, reopen the UI, and confirm the running status returns from the backend.
11. Wait for completion and confirm a persistent success result shows channels, events, completion time, and duration.
12. Start an update and attempt another manual update; confirm the duplicate request reports the already-running scan and clears automatically when finished.
13. While a manual update is running, confirm **Update Now** becomes a cancel action. Cancel it and confirm the incomplete replacement is discarded and the previously published M3U/XMLTV remain unchanged.
14. Restart the app during an update and confirm the stale running state becomes a visible interrupted-scan failure.

## SD filtering

1. Add a sports event under provider group `LOW BANDWIDTH` or with an SD label.
2. Enable **Hide SD / LOW BANDWIDTH channels** and run Update now.
3. Confirm the SD event/feed is not generated.
4. Disable the filter and confirm the feed may be generated on the next successful scan.

## Placeholder cleanup and postgame grace

1. Supply guide titles `No EVENT Today`, `No Events Today!`, and `Signing-Off`; confirm none generates a sports channel.
2. Supply a legitimate title such as `Golf Channel Podcast With Rex & Lav`; confirm it remains eligible.
3. Confirm filtering is case-insensitive and tolerant of punctuation/spacing but does not broadly remove podcasts, studio shows, pregame, or postgame programming.
4. Confirm a generated event remains present through 90 minutes after its known or estimated end, then disappears on the next successful scan after that boundary.

## XMLTV and Jellyfin

1. Configure Jellyfin with `/playlist/channels.m3u`.
2. Add only `/epg/epg.xml` as the XMLTV guide source. The sports-only XMLTV remains an internal diagnostic output and is not a normal Jellyfin guide source.
3. Confirm every generated M3U `tvg-id` has a matching XMLTV `<channel>` and at least one `<programme>`.
4. Confirm generated XMLTV `<channel>` elements appear before the first `<programme>` in `epg.xml`.
5. Open `/api/sports/guide-check` and confirm `ok` is true after a successful populated scan.
6. Add a provider XMLTV containing many unrelated channels. Confirm `epg.xml` includes only exact `tvg-id` matches for manual channels in `channels.m3u`, plus generated sports channels and programmes.
7. Confirm `epg.xml` remains reasonably sized instead of copying the provider's full XMLTV catalog.
8. Run another update with different events occupying the same numbered slots and confirm Jellyfin keeps guide mapping.


## Public EPG system-wide fallback

1. Select at least one ordinary non-sports manual channel (for example a local NBC affiliate) and enable the appropriate public country guide.
2. Confirm `epg.xml` can use the public guide when the provider/configured guide has no programme data for that selected channel.
3. Give the provider guide one programme window and the public guide a later non-overlapping window for the same channel; confirm both appear in `epg.xml`.
4. Give the public guide a programme that overlaps the provider programme; confirm the provider programme wins and the overlapping public programme is omitted.
5. Confirm unselected public-guide channels are still filtered out and the full country XMLTV is never copied into `epg.xml`.

## Disabled recovery cache

1. Generate sports channels, then turn Sports Automation off.
2. Confirm generated rows disappear from Channel Manager, `/playlist/channels.m3u`, `/epg/sports.xml`, and `/epg/epg.xml`.
3. Turn Sports Automation back on within 24 hours and confirm cached channels return immediately.
4. Confirm saved selection rules were never removed.
5. Simulate more than 24 hours disabled and confirm only generated cache rows are purged.

## EPG output and public-guide UI

1. Confirm the top toolbar shows exactly one **Channels** URL and one **EPG** URL with Copy buttons.
2. Confirm the normal UI no longer shows the old EPG Manager table.
3. Confirm **Free Public EPG — By Country** remains available and United States is enabled by default on fresh state.
4. Confirm `/playlist/channels.m3u` advertises `/epg/epg.xml` in its M3U header.
5. Confirm `/playlist/custom.m3u` and `/epg/combined.xml` still return the same content as temporary compatibility aliases, but neither old name is advertised in the UI.
6. Confirm `/epg/epg.xml` rebuilds automatically when a stale/empty file exists but generated sports rows are present; the sports-only guide remains diagnostic.
7. Recreate the container and confirm source configuration, sports rules, generated rows, and guide exports survive persistent Docker state.

## v21.8 authoritative programme propagation

1. Use a provider XMLTV programme that is already in progress when the sports scan begins.
2. Confirm the game is retained even though its start time is before the scan timestamp.
3. Confirm every generated feed for that event shows the provider programme's exact start and stop time.
4. Confirm every generated feed uses the provider programme title rather than `League • Event` or a generic `Event window` title while the game is airing.
5. Confirm each generated feed keeps its own feed-specific subtitle and description context.
6. Confirm provider `<live/>`, `<previously-shown/>`, and `<new/>` markers are propagated only when present in the source programme.
7. Confirm the post-event placeholder begins at the authoritative XMLTV stop time and lasts only through the 90-minute grace period.
8. Restart the application and rebuild the guide from SQLite. Confirm the authoritative programme metadata survives and remains attached to all generated feeds.

## v21.9 manual/generated namespace regression checks

1. Save a full-time manual channel, then generate a sports event feed that uses the exact same stream URL. Confirm both entries remain in `/playlist/channels.m3u`.
2. Confirm the manual row retains its original provider `tvg-id`, name, group, saved order, and low channel number.
3. Confirm the generated row retains a unique `m3u-picker-sports-*` `tvg-id`, generated event name, Sports Today group, and assigned sports channel number.
4. Confirm Jellyfin displays the full-time manual channel and temporary generated event feed as separate channels even though playback resolves to the same stream URL.
5. Repeat with a manual source row whose provider `tvg-id` also appears as the generated feed's source ID; neither namespace may suppress the other.
6. Load two distinct manual provider rows that share one stream URL but have different names or `tvg-id` values. Select both, restart, and confirm both remain saved and ordered.
7. Upgrade a v21.8 database containing URL-only selection keys. Confirm selected manual channels migrate automatically and none disappear after provider refresh.

## Finished-event cleanup

1. Provide an XMLTV game whose `stop` time ended more than 90 minutes before the scan.
2. Confirm the event is not generated, even when the provider's event channel still exists.
3. Provide a currently active replay programme and enable **Include replays**; confirm it appears as a Replay programme on the original logical event channel and does not allocate another channel block.
4. Provide an M3U-only event title with no date or time and no matching XMLTV programme. Confirm it is skipped rather than treated as permanently live.
5. Add a matching XMLTV programme for that same event and confirm the provider stream is generated using the XMLTV timing.

## v21.6 event lifecycle regression checks

1. Set the event window to **Next 24 hours** and scan after a game has started but before its stop time. Confirm the game remains in the generated lineup.
2. Scan just after the configured refresh boundary while a game that started before the boundary is still underway. Confirm the game remains present.
3. Confirm a game remains present through its scheduled/estimated end and the 90-minute grace period, then disappears on the first scan at or after the grace boundary.
4. Remove an XMLTV `stop` value and confirm the league/sport-specific estimated duration is used instead.
5. Provide two same-day programmes with the same matchup and both marked `<live/>`, with start times several hours apart. Confirm they remain two separate events with different event keys/channel slots.
6. Provide M3U and XMLTV times for the same game that differ by only a few minutes. Confirm they merge into one event and the XMLTV timing wins.
7. Start a deliberately slow scan and confirm all lifecycle decisions use the same scan-start timestamp rather than drifting as the parse runs.

## v21.10-debug3 provider fallback and Xtream checks

1. Load a direct M3U as the primary and confirm only its channels appear in Channel Manager.
2. Load an Xtream primary using only server/base URL plus separate username/password fields. Confirm the saved row reports `Xtream API` or `Xtream-compatible` and never displays the URL or credentials.
3. Enter an invalid Xtream password and confirm the provider is rejected without replacing the working primary or exposing credentials in the error.
4. Add two fallback providers and confirm they display as Fallback 1 and Fallback 2 in insertion order.
5. Confirm fallback channels never appear as manually selectable rows in Channel Manager.
6. Provide the same sports event on primary and fallback providers. Run Sports Automation and confirm only the primary provider stream is generated.
7. Reverse the internal/list input order in a test fixture and confirm the primary still wins; precedence must not depend on concatenation order.
8. Remove the event from the primary while retaining it on Fallback 1. Confirm Fallback 1 supplies the generated event.
9. Remove the event from Fallback 1 while retaining it on Fallback 2. Confirm Fallback 2 supplies the generated event.
10. Disable backup feeds and give the primary only a backup-labeled candidate while a fallback has a normal candidate. Confirm the eligible fallback may fill the event.
11. Confirm a failed fallback refresh leaves the previous working generated sports lineup intact and reports a fallback warning rather than failing the primary update.
12. Restart the container and confirm the primary/fallback ordering, separate Xtream credentials, playlist caches, and guide caches persist.
13. Upgrade from a v21.9 `config.json` containing only `source_url`; confirm it migrates automatically as the primary provider.
14. Upload a local M3U file as primary and confirm URL provider/fallback configuration is cleared rather than silently mixed with the file catalog.


## v21.10-debug3 live-only provider safety checks

1. Load an Xtream primary with separate credentials and confirm the progress panel advances through authentication, live-stream download, category loading, and playlist building while showing elapsed time.
2. Confirm the provider row reports only live channels, not the provider's VOD/series catalog.
3. Confirm the provider table headers read Priority, Name, Type, Live Channels, Last Updated / Status, and Action.
4. Confirm primary URL and file controls are disabled after a primary is loaded.
5. Remove the primary and confirm primary controls become available again.
6. Add a fallback and confirm it saves quickly with `Ready — loads during Master Update` and a zero count before the first sports scan.
7. Run Master Update and confirm the fallback changes from deferred to a live-channel count only.
8. Confirm a fallback response above the safety limit fails cleanly without restarting the container or replacing its previous cache.
9. Confirm removing a primary preserves configured fallbacks as inactive rows until a replacement primary is added.


## v21.10-debug8 logical-event and replay checks

1. Load a primary provider and reload the page. Confirm Name, Provider URL, Xtream username, Xtream password, M3U file, and both primary load buttons are disabled.
2. Remove the primary and confirm all primary-provider controls become available again.
3. Select the same team and its league, then scan one game. Confirm the overlap produces one logical game/channel block, with the team rule retaining preference priority.
4. Provide one live-marked game plus later unmarked same-matchup airings overnight. With **Include replays and encore broadcasts** disabled, confirm only the live game block is generated.
5. Enable replays and scan the same data. Confirm the same channel block remains and the later airings appear as XMLTV programmes labeled `Replay:` with `<previously-shown/>`.
6. Confirm no extra 10-channel event blocks are allocated for those replay windows.
7. Provide two same-matchup programmes both marked `<live/>`. Confirm they remain distinct logical events so a true doubleheader is not collapsed.
8. Run a follow-up Master Update after the canonical live game ends but before an overnight replay. Confirm recent canonical programme context prevents the replay from resurrecting duplicate channels when replays are disabled.
9. Confirm every generated `— Event window` programme lasts no more than 90 minutes and ends before the next retained replay begins.
10. Restart and rebuild XMLTV from SQLite. Confirm attached replay airings remain on the original generated channel IDs.


## v21.10-debug9 canonical-anchor and performance checks

1. Load a primary provider, reload the page, and confirm every add-primary field and action is disabled: name, URL, Xtream username, Xtream password, M3U file, Load Primary, and Use File as Primary.
2. Confirm the credential fields are blank while locked even if the browser previously autofilled them. Remove the primary and confirm all controls unlock.
3. Scan a timed event row plus 6:40 PM, 12:30 AM, and 6:00 AM XMLTV programmes for the same matchup, with all three programmes marked `<live/>`. With replays disabled, confirm only one channel block is allocated.
4. Enable replays and scan the same data. Confirm the overnight airings are attached as Replay programmes on the original channel IDs and do not allocate `1010`/`1020` blocks.
5. After the live game ends, refresh the provider playlist without its original timed event row and leave only an overnight replay in XMLTV. Confirm the prior scan anchor suppresses replay-channel resurrection when replays are disabled.
6. Provide two distinct timed event rows for the same teams (doubleheader or consecutive day). Confirm both remain separate logical events.
7. Inspect `docker compose ... logs` for `Sports scan timings:` and verify stage durations, provider channel count, EPG/M3U event counts, history-anchor count, team-cache hits/misses, selected events, and generated channels are reported.
8. Compare a broad scan against the debug8 26m10s benchmark. Confirm memory remains near the prior range and total time is materially reduced without changing the final lineup.

## v21.10-debug10 migrated-replay cleanup checks

1. Upgrade from debug8/debug9 with the existing `debug-data` directory that already contains `1000–1002`, `1010–1012`, and `1020–1022` for one matchup.
2. Keep **Replays and encores** disabled and run Master Update. Confirm the next successful scan replaces those rows with only one channel block.
3. Confirm the canonical block uses the evening game start, while the 12:30 AM and 6:00 AM provider airings do not allocate channels.
4. Enable replays and rescan. Confirm later XMLTV airings appear as `Replay:` programmes on the original channel IDs rather than new channel blocks.
5. Confirm a same-matchup game on the following evening remains a separate logical event.
6. Confirm an EPG-only 1:00 PM / 7:00 PM doubleheader remains two events; explicit Game 1/Game 2 provider rows must also remain separate.
7. Inspect `Sports scan timings:` to confirm debug9's indexed matching path remains active.
8. Reload the page with a primary provider loaded and confirm primary name, URL, username, password, file picker, and both load actions remain locked.

## v22.0-debug1 team/league, provider status, and fresh-state checks

1. Start with an empty `debug-data` directory and configure one Xtream primary from base URL plus separate username/password fields.
2. Confirm the primary name, URL, username, password, file picker, Load Primary, and Use File as Primary controls all lock together after load and remain locked after reload.
3. Confirm removing the primary unlocks all add-primary controls together.
4. Confirm the provider row shows provider-reported account status and expiration when present, but never shows credentials, a credential-bearing URL, or connection counts.
5. Add an MLB league rule only. Confirm each MLB game generates one best feed even if the league rule previously used an `all` preference.
6. Add an explicit Phillies team rule while keeping MLB selected. Confirm Phillies games expand to available home/away/national/event feeds while non-Phillies MLB games remain one feed each.
7. Confirm the Phillies game appears as one logical event block, not one block from the team rule plus another from the league rule.
8. Add explicit rules for both teams in one matchup. Confirm the game remains one logical event block and does not duplicate channels.
9. With replays disabled, confirm overnight repeat airings do not allocate new channel blocks.
10. With replays enabled, confirm repeats appear as Replay programmes on the original channel IDs.
11. Confirm manual/static channels remain unchanged when their provider stream is also used by a generated sports channel.
12. Inspect `Sports scan timings:` logs and compare broad-rule scans against the 4m30s–4m50s debug10 baseline.


## v22.0-rc1 final release-candidate checks

1. Begin with an empty test runtime directory and configure a primary provider from separate Xtream fields.
2. Confirm all add-primary controls lock after load and unlock only after primary removal.
3. Confirm provider status and expiration render without credentials or connection counts.
4. Configure a broad league plus an explicit team in that league; verify league-only games receive one feed while selected-team games receive the expanded feed set.
5. Confirm overlapping team/league rules produce one logical game block.
6. Verify replay behavior both disabled and enabled, including cross-midnight repeats.
7. Confirm manual/static channels remain intact when generated sports channels reuse the same provider stream.
8. Verify the single daily Master Update defaults to 03:00 local, can be changed, shows the next run, and manual Update Now does not shift the scheduled time.
9. Verify the 90-minute postgame grace period and narrow placeholder filtering.
10. Validate `playlist/channels.m3u`, `epg/sports.xml`, and `epg/epg.xml` directly before evaluating Jellyfin cache behavior.
11. Review `Sports scan timings:` and compare with the current 4m30s–4m50s baseline.
12. Confirm the production Compose stack defaults to host port 9999 and the sports debug stack remains isolated on host port 10000.


## v22.1-rc5 Schedule API and ordered-cycle checks

1. Start with fresh runtime state; load the primary provider and enable Sports Automation.
2. Leave **Use schedule API** off. Run Update Now and confirm legacy provider/EPG matching completes normally.
3. Enable the API switch with no key and confirm the UI requests only an API-SPORTS key. There must be no editable base-URL field.
4. Confirm the provider note says **API-SPORTS only for now** and links to `https://api-sports.io`.
5. Save a valid API-SPORTS key. Confirm the key field clears, the secret is never returned to the browser, and the request-plan table reflects the current sports selections.
6. With MLB + Phillies selected, confirm the plan contains one MLB dataset, not separate league/team calls.
7. Add NFL plus multiple NFL teams. Confirm the plan contains one NFL dataset regardless of the number of NFL rules.
8. Add Big Ten, ACC, SEC, and NCAA teams. Confirm the plan contains one NCAA dataset and conference/team filtering happens locally.
9. Add Golf and Track & Field selections. Confirm they are identified as legacy-only and do not create API-SPORTS requests.
10. Run Update Now. Confirm the first run fetches only the unique required dataset/date combinations and records cache/quota status.
11. Run Update Now again on the same local day. Confirm already-covered schedule requests (same provider/product/endpoint/date/season/timezone parameters) are reused from cache with no duplicate API calls. Change the sports timezone and confirm the old request identity is not incorrectly reused.
12. Use **Force Schedule Refresh** and confirm only the daily schedule cache is deliberately bypassed; the control is separate from normal Update Now.
13. Confirm NCAA conference reference membership is fetched only when a conference rule needs it, then reused for that season without a per-team or per-conference daily call. An NCAA team-only rule must not require the standings-membership reference call.
14. With a valid schedule cache, simulate an API fetch failure and confirm cached canonical events remain usable. With no usable cache, confirm the affected sport falls back to legacy provider/EPG matching and the lineup remains available.
15. With MLB/NFL/NCAA canonical IDs present, verify overlapping provider airings map to one logical event and replay rules do not allocate duplicate blocks.
16. Click **Update Now** and confirm the button becomes disabled until the update succeeds or fails; repeated clicks cannot start or cancel a second update.
17. Confirm the Master Update cycle trace remains `schedule_api → provider_refresh → epg_refresh → sports_scan_match → channel_build → epg_publish → m3u_publish`.


## v22.1-debug2 Master Update / public EPG checks

1. Start fresh and confirm **Master Update** appears directly below the M3U copy URL, is enabled, and defaults to **03:00** local time.
2. Change the daily time, reload the page, and confirm the new time persists and the next-run display updates.
3. Run **Update Now** and confirm the next scheduled daily run remains at the configured clock time rather than being reset from the manual run.
4. Expand **Free Public EPG — By Country** in its standalone guide section. Confirm United States is checked by default and all other countries are unchecked.
5. Check/uncheck a country and confirm it persists immediately without a Save button. Unchecked countries must not be downloaded.
6. Confirm the built-in public URL for US resolves internally to `https://iptv-epg.org/files/epg-us.xml.gz`; no public-guide URL field is shown in the UI.
7. After a successful Master Update, confirm the cached public guide remains `.xml.gz` on disk and no full expanded `epg-us.xml` is created in the runtime data directory.
8. Run Master Update again on the same local day and confirm the fresh public EPG cache is reused instead of redownloaded.
9. With a selected channel lacking provider programme data but present in the public guide, confirm public XMLTV fills the hole in `epg.xml`.
10. When both provider and public guides contain programming for the same selected channel, confirm provider programme data wins and the public copy does not duplicate/overwrite it.
11. Confirm `epg.xml` contains only relevant selected manual/generated sports records, not the complete public-country guide.
12. Confirm the cycle trace is exactly `schedule_api → provider_refresh → epg_refresh → sports_scan_match → channel_build → epg_publish → m3u_publish`.
13. Turn the schedule API off or leave the API key blank and confirm the same Master Update completes through legacy provider-derived sports matching.
14. Confirm filler titles `No Event Today`, `No Events Today`, `No Game Today`, and `Signing Off` are ignored while legitimate podcast/studio titles remain eligible.


## v22.1-rc5 Schedule API UI checks

- Toggle Use schedule API on, reload the page, and verify it remains on. Toggle it off, reload, and verify it remains off.
- Verify only the API key / Save API / Remove API controls are disabled while the switch is off; no API URL input exists.
- Save API-SPORTS credentials and verify the planned-dataset table shows Provider, Product, Scope, Status, Last Updated, and Cache.
- Verify the UI shows API-backed, legacy-only, and mixed selection summaries when those rule types are present.
- Disable the API without removing the key and verify the configuration remains saved while legacy provider/EPG matching is active.
- Remove the API and verify the secret is cleared and never returned to the browser.
- Confirm Force Schedule Refresh is disabled when the API is not effective or no API-backed dataset is required.
- Expand Free Public EPG — By Country in dark mode and verify the section title, disclosure marker, and all country names are readable.


## v22.1-debug4 Master Update progress

1. Click **Update Now** and verify a spinner plus elapsed timer appears immediately beside the Master Update controls and increments once per second.
2. Let the update finish and verify the spinner disappears and the status row includes **Took <duration>**.
3. Reload the page and verify the last duration remains visible.
4. During a scheduled daily run, verify the same progress indicator shows **Automatic update** with an elapsed timer.
5. Confirm manual Update Now still does not move the next scheduled daily run.

## v22.1-debug5 API canonical airing validation

- [x] API event ID is one logical game regardless of later provider airing times.
- [x] 6:00 PM Phillies Gameday/wagering support row does not become a game feed.
- [x] 6:05 PM Phillies live programme is selected for API event 179771.
- [x] 11:00 PM Phillies rebroadcast does not allocate another channel block with replays off.
- [x] Replays on attaches the 11:00 PM airing to the same API event/channel IDs with replay semantics.
- [x] Earlier Dodgers Squeeze Play content does not become the 8:10 PM Dodgers/Diamondbacks game.
- [x] Real three-file MLB fixture resolves 15 API games → 15 logical events → 17 feeds for MLB + Phillies.


## v22.1-rc3 correctness checks

1. Use the Aug. 8 MLB schedule with Blue Jays at Phillies at 6:05 PM EDT. Confirm API game `179771` is the single logical event identity.
2. Supply a 6:00 PM `In-Game Live Gameday` betting programme, the 6:05 PM live game, and an 11:00 PM rebroadcast. With replays off, confirm only the 6:05 PM live airing supplies generated channels.
3. Enable replays and repeat. Confirm the 11:00 PM airing becomes a replay programme window on the same channel IDs and never allocates another event block.
4. Feed the same 6:05 PM game as XMLTV `22:05 +0000` and API `18:05 -0400`. Confirm both map to the same API event.
5. Put `Game 2 of 3` only in the provider description. Confirm it does not create a `game-2` event variant. Put explicit `Game 2` in the title and confirm a real doubleheader variant remains supported.
6. After an event ends, verify generated channels remain through the 90-minute grace and are then removed by the lightweight lifecycle cleanup without waiting for the next daily Master Update.
7. Confirm synthetic post-event guide windows never exceed the 90-minute grace period.
8. Confirm a successful zero-event Schedule API window is treated as authoritative when replays/classics are off; confirm API errors or stale cache still make legacy fallback obvious.
9. Confirm every generated M3U `tvg-logo` is mirrored by the XMLTV `<channel><icon>`. For API-backed home/away team feeds, confirm API team artwork is preferred when available.
10. Confirm the top UI advertises `/playlist/channels.m3u` and `/epg/epg.xml`, the old EPG Manager is absent, and the worldwide public-country selector remains.
11. Confirm the Add sports selection Type control says **League**, not `League / series / tour`.


## v22.1-rc7 final-candidate checks

- Verify Manage Order is directly beneath the EPG row in the sticky header.
- Verify the sports option reads “Include replays and classic games.”
- With a successful current Schedule API cache and replays/classics off, unmatched historical/provider events for that API-backed league must not generate channels.
- With an API error/plan restriction or stale cache, legacy provider/XMLTV matching must remain available.
- Legacy-only sports remain unaffected by authoritative Schedule API gating.
- Football preseason/spring handling remains unchanged until the live API can be checked inside the free-plan date window.
