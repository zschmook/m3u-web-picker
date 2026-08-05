# M3U Web Picker Sports v21.10-debug8

## Logical games versus provider airings

- Deduplicates overlapping team and league discovery before channel allocation.
- Uses a stable logical matchup identity rather than treating each provider programme start time as a new game.
- Keeps separately live-marked doubleheaders as separate events.
- Retains recent canonical programme context for 24 hours so frequent interval scans can classify overnight replays without resurrecting duplicate channels.

## Replay and encore behavior

- With **Include replays and encore broadcasts** disabled, later replay/time-shifted airings of the same live-marked game are ignored.
- With the option enabled, later airings are stored as additional XMLTV programme windows on the original generated channels.
- Inferred repeats are labeled `Replay:` and exported with `<previously-shown/>`.
- Replays no longer allocate additional sports channel blocks.
- The logical channel remains available through the last retained airing and its 90-minute grace period.

## Provider form locking

- A saved primary provider disables the primary name, URL, Xtream username/password, file picker, and load actions after initial load or page refresh.
- Removing the primary re-enables those controls.

## Event-window correction

- Every post-airing Event window is clipped to exactly the configured 90-minute grace period.
- Grace placeholders are clipped at the next retained airing so XMLTV programmes do not overlap.

## Preserved behavior

- Keeps daily and every-X-hours Sports Automation scheduling.
- Keeps the narrow **No Event(s) Today** and **Signing Off** filters.
- Keeps manual/static channels isolated from generated sports channels.
- Keeps primary/fallback provider support and live-only Xtream imports.
- Does not implement the deferred football-season event-timeline shortening.
