# Sports v21.8 release notes

v21.8 keeps the v21.7 event-lifecycle and EPG Manager changes, then fixes generated XMLTV programme provenance for games that are already airing.

## Authoritative provider programme propagation

- Provider XMLTV programme title, start, stop, description, categories, and live/replay/new markers are retained during sports-event parsing.
- The best current provider programme is selected without widening nearby XMLTV records into an artificial combined interval.
- Every generated feed for the same event receives the same authoritative programme timing and title.
- Feed-specific subtitles remain distinct, such as provider event stream, away broadcast, and home broadcast.
- Synthetic upcoming/live/event-window programmes are used only when exact provider XMLTV is unavailable.
- The post-event placeholder starts only after the authoritative stop time and ends after the existing 30-minute grace period.
- Programme provenance is persisted in SQLite so startup guide rebuilds do not lose the provider programme metadata.

## Retained fixes

- Games already in progress remain eligible through interval-overlap matching.
- Finished events expire after their explicit or estimated end plus grace.
- Untimed event slots require XMLTV corroboration.
- Same-day doubleheaders remain separate.
- Manual scan cancellation preserves the previously published lineup and guide.
- Combined XMLTV remains filtered to selected manual channels plus generated sports entries.
- EPG Manager rows and controls remain aligned in one fixed-column table.

## Validation

- Unit suite: 63 tests run, 57 passed, 6 Flask/Docker-image-dependent tests skipped in the lightweight environment.
- Python compilation, JavaScript syntax, shell syntax, ZIP integrity, version marker, and release archive cleanliness were checked.
- Docker itself was unavailable in the build environment; the running container remains the final integration test.
