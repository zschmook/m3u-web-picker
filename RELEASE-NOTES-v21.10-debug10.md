# M3U Web Picker Sports v21.10-debug10

## Root cause of the surviving replay blocks

Debug9 fixed the expensive matching path, but a migrated debug database could still contain the old `1000`, `1010`, and `1020` logical-event rows. The previous-scan recovery code rehydrated every one of those rows as an independent schedule anchor. Current provider data then merged into those historical anchors, allowing the duplicate replay blocks to survive indefinitely even with **Replays and encores** disabled.

A second ambiguity existed when providers exposed separate timed playlist rows for the evening game and its after-midnight repeats. Those rows were all treated as equally canonical.

## Broadcast-day logical-event grouping

- Groups stable matchup identities by a provider broadcast day with a noon local-time rollover.
- Maps an evening game plus 12:30 AM and 6:00 AM airings to the same logical game.
- Keeps the following evening's game in a new logical-event bucket.
- Treats migrated `sports_generated` rows only as replay-classification hints; current provider records take ownership when they merge into the same airing.
- Collapses duplicate historical anchors before channel allocation, so a debug8/debug9 state migration cleans itself on the next successful scan.
- Does not merge replay-slot source URLs into the canonical live event.

## Replays and doubleheaders

- With **Replays and encores** disabled, later same-broadcast-day airings are discarded before channel allocation.
- With the option enabled, XMLTV-backed later airings are attached as `Replay:` programmes to the original generated channels.
- EPG-only afternoon/evening same-matchup programmes remain separate to avoid collapsing an ambiguous doubleheader.
- Obvious EPG-only after-midnight repeats are folded into the prior evening event even when the provider incorrectly marks them `<live/>`.
- Explicit `Game 1`, `Game 2`, `First Game`, and `Second Game` identities remain separate.
- Consecutive-evening games remain separate.

## Preserved behavior

- Retains debug9's scan-local indexes and stage timing logs.
- Retains daily and every-X-hours Sports Automation scheduling.
- Retains the narrow **No Event(s) Today** and **Signing Off** filters.
- Retains the 90-minute postgame grace period.
- Retains manual/generated channel isolation and unique local sports playback URLs.
- Retains primary/fallback providers and live-only Xtream imports.
- Retains the complete primary-provider form lock and JavaScript cache busting.
- Does not implement the deferred football-season event-timeline shortening.
