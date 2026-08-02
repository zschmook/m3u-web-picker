# Sports v21.1 QA checklist

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
2. Click Update now and confirm the button becomes disabled while a persistent status card cycles `.` → `..` → `...` and shows stage plus elapsed time.
3. Run twice with different source data and confirm sports rows are replaced rather than accumulated.
4. Simulate a failed provider refresh and confirm the previous generated lineup remains.
5. Perform a successful scan with zero matches and confirm stale sports output is cleared.
6. Add an impossible M3U time such as `99:99:00`; valid events must continue processing.
7. Repeat with an invalid XMLTV timestamp; later valid programmes must still process.
8. Close the browser tab during a long scan, reopen the UI, and confirm the running status returns from the backend.
9. Wait for completion and confirm a persistent success result shows channels, events, completion time, and duration.
10. Start a scan and attempt another manual update; confirm the duplicate request reports the already-running scan and clears automatically when finished.
11. Restart the app during a scan and confirm the stale running state becomes a visible interrupted-scan failure.

## SD filtering

1. Add a sports event under provider group `LOW BANDWIDTH` or with an SD label.
2. Enable **Hide SD / LOW BANDWIDTH channels** and run Update now.
3. Confirm the SD event/feed is not generated.
4. Disable the filter and confirm the feed may be generated on the next successful scan.

## XMLTV and Jellyfin

1. Configure Jellyfin with `/playlist/custom.m3u`.
2. Add `/epg/combined.xml` as the XMLTV guide source, or add `/epg/sports.xml` when the provider guide is configured separately.
3. Confirm every generated M3U `tvg-id` has a matching XMLTV `<channel>` and at least one `<programme>`.
4. Confirm generated XMLTV `<channel>` elements appear before the first `<programme>` in `combined.xml`.
5. Open `/api/sports/guide-check` and confirm `ok` is true after a successful populated scan.
6. Run another update with different events occupying the same numbered slots and confirm Jellyfin keeps guide mapping.

## Disabled recovery cache

1. Generate sports channels, then turn Sports Automation off.
2. Confirm generated rows disappear from Channel Manager, `/playlist/custom.m3u`, `/epg/sports.xml`, and `/epg/combined.xml`.
3. Turn Sports Automation back on within 24 hours and confirm cached channels return immediately.
4. Confirm saved selection rules were never removed.
5. Simulate more than 24 hours disabled and confirm only generated cache rows are purged.
