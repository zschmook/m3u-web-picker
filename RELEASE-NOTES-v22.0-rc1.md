# M3U Web Picker Sports v22.0-rc1

## Release candidate scope

- Promotes the v22.0-debug1 application code to the first v22 release candidate.
- No intentional application-behavior changes were made during promotion; only version, cache-busting, documentation, tests, and release labeling changed.
- Intended for final validation on the GitHub `sports` branch before the v22 production release.

## Provider sources

- Supports one primary provider and ordered fallback providers for Sports Automation.
- Fallback providers remain optional and do not affect manual/static channel selection.
- Xtream providers use separate server, username, and password fields and import live streams only.
- The add-primary form locks completely while a primary exists and unlocks only after removal.
- Provider rows show provider-reported account status and expiration date when available, without exposing credentials or connection counts.

## Sports event and feed behavior

- Broad league, conference, sport, and Everything Mode rules generate one best feed per logical game by default.
- Games involving an explicitly selected team receive the expanded available feed set, with the team rule controlling preference.
- Team and league overlap merges into one logical event and does not allocate duplicate channel blocks.
- Cross-midnight replay/time-shifted airings are grouped with the canonical game.
- With replays disabled, later airings do not allocate channels; with replays enabled, they attach as Replay programmes to the original generated channels.
- Generated sports channels remain separate from manual/static channels, even when both resolve to the same provider stream.
- Generated event channels retain a 90-minute postgame grace period.
- Clear placeholders such as `No Event(s) Today` and `Signing Off` are filtered while legitimate programming remains eligible.

## Scheduling and performance

- Supports once-daily Sports Automation updates or updates every X hours.
- Retains scan-local team, feed, rule, and event indexes plus stage-timing logs.
- Current real-world test scans were approximately 4m30s–4m50s with a primary containing roughly 6,500 live channels, including an all-MLB plus Philadelphia-team rule set.

## RC validation notes

- Start test deployments with a fresh runtime state unless migration behavior is the explicit subject of the test.
- Jellyfin may retain stale tuner/guide mappings after channel identity changes; validate the app-served M3U and XMLTV directly before attributing a display issue to the application.
- Runtime databases, cached playlists, generated guides, provider credentials, and compiled files are excluded from the release archive.
