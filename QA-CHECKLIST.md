# Sports v20.7 test checklist

1. Start the debug container and open `http://localhost:10000`.
2. Confirm Sports Automation starts with zero selections.
3. Add several selections through **Add selection** using Type, Search, and Sport filters.
4. Remove every selection, reload the page, and restart the container. The list must remain empty.
5. Load an M3U URL. The visible URL field should clear and the page should say **Source loaded.**
6. Confirm all Sports headings, checkbox labels, and **Auto update** text are readable in dark mode.
7. Set a refresh time and reload. It should survive as the same time without an `hour must be in 0..23` error.
8. Turn Sports Automation on and Auto update off. **Update now** should still work.
9. Turn Sports Automation off. **Update now** and scheduled updates should be unavailable.
10. Run **Update now** twice on different source data and confirm previously generated sports channels are replaced rather than accumulated.
11. Simulate a failed source refresh and confirm the existing generated sports channels remain.
12. Confirm stream URLs shown in Channel Manager mask both credential path segments.

## Malformed provider data

- Add an M3U event with an impossible embedded time such as `99:99:00`.
- Click **Update now**.
- Confirm valid events are still generated and the malformed entry is only reported in Docker logs.
- Repeat with an invalid XMLTV programme timestamp and confirm later programmes still process.


## Generated XMLTV guide

- Run **Update now** and confirm the button becomes a disabled spinner until the request completes.
- Open `/epg/sports.xml` and confirm every generated M3U `tvg-id` has a matching XMLTV `<channel id>` and at least one `<programme>`.
- Confirm a scheduled game has an **Upcoming** guide entry beginning 24 hours before start, a game entry, and a post-event entry.
- Open `/epg/combined.xml` and confirm existing provider guide entries remain present alongside the generated sports entries.
- Configure Jellyfin with `/epg/sports.xml` as an additional guide source, or replace the existing guide URL with `/epg/combined.xml`, then refresh guide data.
- Confirm generated channels show titles, feed subtitles, start times, and logos in Jellyfin.



## Jellyfin guide mapping regression

- Run **Update now**, then confirm generated M3U IDs are fixed numbered slots such as `m3u-picker-sports-1000`.
- Open `/epg/combined.xml` and confirm no `<channel>` element appears after the first `<programme>` element.
- Refresh the Jellyfin tuner and guide data.
- At each scheduled game time, confirm every generated feed has one visible programme tile rather than a blank row.
- Run another sports update with different events occupying the same channel numbers and confirm the guide remains mapped without recreating the XMLTV source.

## MLB / MiLB separation

- Select MLB only and confirm no MiLB matchup is generated from a shared `MLB / MiLB` provider group.
- Select MiLB only and confirm no MLB matchup is generated.
- Confirm both league choices appear independently in Add selection.

## Generated guide continuity

- At the scheduled first-pitch time, every generated feed has exactly one XMLTV programme.
- Restart with persisted generated rows and confirm stale provider timestamps produce fallback guide coverage rather than a blank Jellyfin grid.

## Full MLB abbreviation regression

- Select the MLB league rule with provider channels grouped under `MLB / MiLB`.
- Confirm abbreviation-only XMLTV matchups for all 30 teams are included.
- Confirm `CHW at TB` displays as `Chicago White Sox at Tampa Bay Rays`.
- Confirm the same game is generated once when both M3U and XMLTV describe it.
- Confirm a clearly marked MiLB event remains excluded from the MLB rule.

