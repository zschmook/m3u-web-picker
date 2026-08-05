# M3U Web Picker Sports v21.10-debug9

## Deep logical-event matching fix

- Uses timed M3U event rows as canonical schedule anchors for a logical game.
- Treats XMLTV programmes as candidate airings of those anchors, even when a provider incorrectly marks overnight replays as `<live/>`.
- Prevents the same Nationals–Phillies game from allocating separate `1000`, `1010`, and `1020` channel blocks for 6:40 PM, 12:30 AM, and 6:00 AM airings.
- Normalizes generated event keys in the configured Sports Automation timezone instead of inheriting provider/UTC timestamp formatting.
- Preserves true doubleheaders and consecutive-day games when the playlist provides separate timed event anchors.
- Preserves explicit Game 1/Game 2 distinctions in logical identity.

## Replay behavior across frequent refreshes

- With **Replays and encores** disabled, later airings attached to a canonical game are discarded before channel allocation.
- With the option enabled, later airings become `Replay:` XMLTV programmes on the original generated channels and receive `<previously-shown/>`.
- Rehydrates recent generated games as short-lived classification anchors. If the provider removes the original timed event row after the game, a later replay cannot resurrect a new channel block during a 2-hour refresh.
- Historical anchors are still subject to the normal event-window and 90-minute postgame cleanup rules; they do not keep stale channels visible by themselves.

## Scan performance

- Builds the team catalog, normalized aliases, exact-name maps, team-feed map, and rule indexes once per scan.
- Caches repeated team-name resolution.
- Replaces quadratic exact-time cluster searches with a linear sorted merge.
- Reuses the team-feed map instead of reclassifying the full provider playlist for every selected event.
- Adds per-stage scan timings and counts to container logs and the scan result payload.
- Keeps XMLTV parsing streaming and scan-local; no large persistent EPG index is retained in memory.

## Primary-provider form lock

- Removes the initial-load race between channel state and provider state.
- Disables primary name, URL, Xtream username/password, file picker, and load actions whenever a primary exists.
- Clears browser/password-manager autofill from the add-primary credential fields while locked.
- Adds a static JavaScript version query so the browser fetches the debug9 UI code instead of retaining an older cached form-lock implementation.

## Preserved behavior

- Keeps daily and every-X-hours Sports Automation scheduling.
- Keeps the narrow **No Event(s) Today** and **Signing Off** filters.
- Keeps legitimate adjacent programming such as podcasts.
- Keeps the 90-minute postgame grace period.
- Keeps manual/static channels isolated from generated sports channels.
- Keeps primary/fallback provider support and live-only Xtream imports.
- Does not implement the deferred football-season event-timeline shortening.
