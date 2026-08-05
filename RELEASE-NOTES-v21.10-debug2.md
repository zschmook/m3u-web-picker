# M3U Web Picker Sports v21.10-debug2

## Provider-source UI fixes

- Adds visible provider form labels and provider table headers.
- Enforces exactly one primary provider at a time.
- Disables primary URL/file entry after a primary is loaded.
- Adds Remove actions for URL primary, file primary, and fallback providers.
- Preserves configured fallbacks when the primary is removed; they remain inactive until a new primary is added.
- Adds a live provider-operation panel showing phase, elapsed time, live-channel count, success, timeout, or failure.

## Xtream live-only safety

- Authenticated Xtream providers now request `player_api.php?action=get_live_streams`.
- VOD and series records are excluded from the generated provider playlist.
- Live category names are loaded from `get_live_categories` when available.
- Standard `/live/<user>/<password>/<stream-id>.<format>` URLs are generated internally.
- Incomplete/nonstandard Xtream panels may still use the generated `get.php` endpoint, but only behind byte and channel-count safety limits.
- Provider playlists above 50,000 entries or 96 MB are rejected before full parsing by default.
- Large provider sets at or above 20,000 channels are flagged with a warning.

## Lazy fallback providers

- Adding a fallback validates the URL/login but does not download its channel catalog.
- Fallback live channels are downloaded only when Sports Update runs.
- Fallbacks remain isolated from Channel Manager and manual/static selections.
- Existing oversized fallback caches are ignored by the same channel-count safety validation.

## Validation

- 78 tests collected: 69 passed and 9 Flask-dependent tests skipped in the lightweight build environment.
- Added tests for live-only Xtream filtering, deferred fallback registration, and oversized-playlist rejection.
- Local Xtream-compatible HTTP validation passed for authentication, live streams, live categories, VOD exclusion, generated stream URLs, and deferred fallback setup.
- Python compilation and JavaScript syntax checks passed.
