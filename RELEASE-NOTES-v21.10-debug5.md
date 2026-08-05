# M3U Web Picker Sports v21.10-debug5

This debug release fixes Jellyfin collapsing a full-time manual channel when Sports Automation generates a temporary event feed from the exact same provider stream.

## Manual/generated playback separation

- Manual/static channels keep their original provider playback URL, `tvg-id`, name, group, order, and low channel number.
- Generated sports channels keep their synthetic `m3u-picker-sports-*` guide identity and assigned sports channel number.
- Each generated channel now publishes a unique app-local playback URL: `/sports/stream/<assigned-number>`.
- The app responds with a temporary non-cacheable redirect to the current provider stream for that generated slot.
- The original provider stream remains internal to the generated-row database and is no longer exposed as the generated channel URL through the Channel Manager API.
- Existing generated database rows are normalized to the local playback URL when read, so a rescan is not required solely for this migration.

This adds a third separation boundary beyond channel number and `tvg-id`: Jellyfin now sees different tuner playback URLs for the manual and generated rows even though both ultimately play the same source. Video bytes continue to flow directly from the provider after the redirect; Flask does not proxy the stream.

## Event cleanup grace

- The generated-channel expiration grace period is increased from 30 minutes to 90 minutes after the expected or authoritative XMLTV end time.
- This protects delayed and extended games, including college football overtime, while still allowing periodic Sports Updates to remove stale event channels.

Provider loading, fallback-provider support, combined XMLTV behavior, and automatic sports-number range shifting are otherwise unchanged from debug4.
