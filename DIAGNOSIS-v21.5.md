# EPG regression diagnosis

The uploaded `combined.xml` returned HTTP 200 but contained only an empty self-closing `<tv>` element. Jellyfin therefore received no `<channel>` or `<programme>` records and had no game start times to display.

Two separate problems produced the symptom:

1. The v21.3 sports debug Compose stack kept SQLite, caches, and exports only inside the disposable container layer. Running with `--force-recreate` replaced that layer and erased the generated sports rows and guide cache. Startup then created a syntactically valid but empty XMLTV file.
2. The v21.3 `/epg/sports.xml` and `/epg/combined.xml` routes rebuilt only when the files were missing. They did not detect an existing but stale/empty guide while generated rows were available.

The EPG Manager was a separate regression: its HTML, JavaScript, persistence, and `/api/epg` routes were absent from the sports build.

v21.5 addresses all three issues:

- restores the EPG Manager without displaying stored source URLs or credentials;
- automatically repairs stale or empty sports guide exports;
- persists debug runtime state in `./debug-data` and production state in a named Docker volume.

Additional v21.5 fixes:

- `combined.xml` is streamed and filtered to exact XMLTV ids used by selected manual channels before generated sports data is appended.
- explicitly timed events are removed after their stop time (or estimated duration) plus a 30-minute grace period.
- manual scans can be cancelled at safe checkpoints without replacing the published lineup or guides.
