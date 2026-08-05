# Sports v21.10-debug9 QA checklist

## Startup and layout

1. Start the debug container and open `http://localhost:10000`.
2. Confirm a fresh database has zero sports selections.
3. Collapse and expand Channel Manager. Reload and confirm the browser remembers the state.
4. Turn Sports Automation on and confirm it expands automatically.
5. Turn Sports Automation off and confirm it collapses and generated rows disappear immediately.

## Taxonomy and picker

1. Open **Add sports selection**. It should default to **League / series / tour**.
2. Filter the list by Sport and confirm results are grouped under sport headings.
3. Confirm Cycling includes Tour de France, Giro d’Italia, Vuelta a España, Tour of California, track cycling, mountain biking, cyclocross, BMX, and Olympic cycling.
4. Confirm Motorsports includes F1/F2/F3/Formula E, NASCAR series, IndyCar, endurance racing, motorcycle racing, rally, drag racing, and monster trucks.
5. Confirm MMA and Pro Wrestling are separate from amateur/Olympic wrestling.
6. Confirm College Football offers FBS, FCS, Division II, Division III, NAIA, NJCAA, and High School Football separately.
7. Confirm Golf, Track & Field, Swimming, Figure Skating, Speed Skating, Gymnastics, and other Olympic disciplines appear as first-class choices.
8. Confirm Cornhole includes ACL, ACO, college, international, and made-for-TV choices.
9. Add and remove multiple selections. Reload and restart the container; the final rule list must persist exactly.
10. Confirm the league/series list shows a **Channel Range** heading over the right-hand ranges.
11. Confirm **I’M INSANE, ADD EVERYTHING!!!!** is unchecked on a fresh database.
12. Add a curated rule, enable Everything Mode, and confirm the curated rule remains unchanged. Disable Everything Mode and confirm the curated rule is still present.
13. Confirm Everything Mode is one persisted setting and does not create a row for every catalog item.

## Channel block map

1. Expand **View league / series channel blocks**.
2. Confirm MLB is `1000–1999`, NHL `2000–2999`, NBA `3000–3999`, NFL `4000–4999`, and MiLB `5000–5999` with default settings.
3. Confirm FBS, FCS, Division II, and Division III each have separate 1,000-channel ranges.
4. Change **First league block** and confirm every displayed range shifts by the same amount.
5. Change **Channels per event** and confirm the capacity message updates.
6. Run a scan containing MLB and NHL events. Confirm MLB channels remain in the 1000s and NHL channels remain in the 2000s.
7. Confirm no competition silently spills into the next competition’s range.

## Scan behavior

1. Turn Auto update off while Sports Automation remains enabled. **Update now** must still work.
2. Select **Once daily**, choose a refresh time a few minutes ahead, and confirm the displayed next-update time changes immediately and the scan runs once at that minute.
3. Select **Every X hours**, set `2`, and confirm the displayed next-update time is two hours after the last completed attempt.
4. Run **Update now** and confirm the two-hour interval resets from that completion time.
5. Simulate a failed interval scan and confirm the next attempt waits the full configured interval instead of retrying every scheduler poll.
6. Change the interval and confirm the next-update display recalculates without restarting the app.
2. Click Update now and confirm the button becomes disabled while a persistent status card cycles `.` → `..` → `...` and shows stage plus elapsed time.
3. Run twice with different source data and confirm sports rows are replaced rather than accumulated.
4. Simulate a failed provider refresh and confirm the previous generated lineup remains.
5. Perform a successful scan with zero matches and confirm stale sports output is cleared.
6. Add an impossible M3U time such as `99:99:00`; valid events must continue processing.
7. Repeat with an invalid XMLTV timestamp; later valid programmes must still process.
8. Close the browser tab during a long scan, reopen the UI, and confirm the running status returns from the backend.
9. Wait for completion and confirm a persistent success result shows channels, events, completion time, and duration.
10. Start a scan and attempt another manual update; confirm the duplicate request reports the already-running scan and clears automatically when finished.
11. While a manual scan is running, confirm **Update now** becomes **Cancel scan**. Click it and confirm the status changes to cancellation requested, the incomplete replacement is discarded, and the previously published M3U/XMLTV remain unchanged.
12. Restart the app during a scan and confirm the stale running state becomes a visible interrupted-scan failure.

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

1. Configure Jellyfin with `/playlist/custom.m3u`.
2. Add `/epg/combined.xml` as the XMLTV guide source, or add `/epg/sports.xml` when the provider guide is configured separately.
3. Confirm every generated M3U `tvg-id` has a matching XMLTV `<channel>` and at least one `<programme>`.
4. Confirm generated XMLTV `<channel>` elements appear before the first `<programme>` in `combined.xml`.
5. Open `/api/sports/guide-check` and confirm `ok` is true after a successful populated scan.
6. Add a provider XMLTV containing many unrelated channels. Confirm `combined.xml` includes only exact `tvg-id` matches for manual channels in `custom.m3u`, plus generated sports channels and programmes.
7. Confirm `combined.xml` remains reasonably sized instead of copying the provider's full XMLTV catalog.
8. Run another update with different events occupying the same numbered slots and confirm Jellyfin keeps guide mapping.

## Disabled recovery cache

1. Generate sports channels, then turn Sports Automation off.
2. Confirm generated rows disappear from Channel Manager, `/playlist/custom.m3u`, `/epg/sports.xml`, and `/epg/combined.xml`.
3. Turn Sports Automation back on within 24 hours and confirm cached channels return immediately.
4. Confirm saved selection rules were never removed.
5. Simulate more than 24 hours disabled and confirm only generated cache rows are purged.

## EPG Manager regression checks retained in v21.9

1. Confirm the EPG Manager appears between Source Manager and Channel Manager.
2. Add a named XMLTV URL and confirm the UI displays only a friendly Source badge, never the stored URL or credentials.
3. Confirm the served named EPG URL downloads valid XMLTV.
4. Confirm `/epg/combined.xml` and `/epg/sports.xml` rebuild automatically when a stale empty file exists but generated sports rows are present.
5. Run `docker compose -f docker-compose.sports-debug.yml up -d --build --force-recreate` twice and confirm source configuration, sports rules, generated rows, and guide exports survive in `./debug-data`.


## v21.7 EPG Manager layout

1. Confirm the EPG Manager uses one unified table with Name, Type, Served URL, Status, and Action columns.
2. Confirm the add-source inputs line up exactly with the same columns used by Combined, Sports, and external source rows.
3. Confirm long names, source labels, URLs, and status text truncate without shifting any column.
4. Confirm Combined and Sports rows expose Copy buttons and show generated-file timestamp/size when available.
5. Confirm external rows expose only the app-served URL and friendly source badge; the original provider URL and credentials never reappear.
6. Resize the browser narrow enough to trigger horizontal scrolling and confirm all rows still share the same column boundaries.
7. Add and delete an external source and confirm the table does not resize or misalign.

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

1. Save a full-time manual channel, then generate a sports event feed that uses the exact same stream URL. Confirm both entries remain in `/playlist/custom.m3u`.
2. Confirm the manual row retains its original provider `tvg-id`, name, group, saved order, and low channel number.
3. Confirm the generated row retains a unique `m3u-picker-sports-*` `tvg-id`, generated event name, Sports Today group, and assigned sports channel number.
4. Confirm Jellyfin displays the full-time manual channel and temporary generated event feed as separate channels even though playback resolves to the same stream URL.
5. Repeat with a manual source row whose provider `tvg-id` also appears as the generated feed's source ID; neither namespace may suppress the other.
6. Load two distinct manual provider rows that share one stream URL but have different names or `tvg-id` values. Select both, restart, and confirm both remain saved and ordered.
7. Upgrade a v21.8 database containing URL-only selection keys. Confirm selected manual channels migrate automatically and none disappear after provider refresh.

## v21.9 EPG Status cleanup

1. Confirm the table header reads **Status**.
2. Confirm the add-source Status cell is blank while idle and shows only real validation/submission feedback.
3. Confirm built-in rows display `Updated <timestamp>` with file size as smaller secondary text.
4. Confirm external rows display `Updated`, `Never updated`, or `Refresh failed` instead of unrelated credential text.

## Finished-event cleanup

1. Provide an XMLTV game whose `stop` time ended more than 30 minutes before the scan.
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
6. Add a fallback and confirm it saves quickly with `Ready — loads during Sports Update` and a zero count before the first sports scan.
7. Run Sports Update and confirm the fallback changes from deferred to a live-channel count only.
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
8. Run a follow-up interval scan after the canonical live game ends but before an overnight replay. Confirm recent canonical programme context prevents the replay from resurrecting duplicate channels when replays are disabled.
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
2. Keep **Replays and encores** disabled and run Sports Update. Confirm the next successful scan replaces those rows with only one channel block.
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
8. Verify once-daily and every-X-hours scheduling, next-run display, and manual Update now reset behavior.
9. Verify the 90-minute postgame grace period and narrow placeholder filtering.
10. Validate `playlist/custom.m3u`, `epg/sports.xml`, and `epg/combined.xml` directly before evaluating Jellyfin cache behavior.
11. Review `Sports scan timings:` and compare with the current 4m30s–4m50s baseline.
12. Confirm the production Compose stack defaults to host port 9999 and the sports debug stack remains isolated on host port 10000.
