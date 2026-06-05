# M3U Web Picker v18

Refactor release based on v17.

Changes:
- Moved embedded HTML from `app.py` into `templates/index.html`.
- Moved API routes into `api/routes.py`.
- Moved shared state/helpers into `core.py`.
- Kept the v17 behavior:
  - M3U URL/file loading
  - cached startup restore
  - SQLite selection persistence
  - search-driven `Add all X` / `Remove all X`
  - playlist endpoints
  - EPG removed

Run:
```bash
pip install -r requirements.txt
python app.py
or 
python3 app.py
```

Open:
```text
http://localhost:9999
```

Note:
This is a structural cleanup before Docker. Keep running v17 while testing this separately.


v18 UI tweak:
- Added a `Saved X channels` button next to `Add all` / `Remove all`.
- Clicking it filters the table to only currently selected/saved channels.
- Clicking again returns to all channels.


v18 UI tweak:
- When `Saved X channels` mode is active, `Add all` and `Remove all` are disabled.
- The saved button turns solid green while active.


v18 UI layout:
- Search and Provider Group intended to sit on the same toolbar row.
- Auto-Save status moved to the far right of that toolbar.
- Layout-only cleanup before Docker work.


V_18_UI_Cleanup_v3:
- Moved inline CSS out of `templates/index.html` into `static/css/app.css`.
- Moved inline JavaScript out of `templates/index.html` into `static/js/app.js`.
- `index.html` now loads assets through Flask `url_for`.
- This is still a cleanup branch; keep v17 as the known-good running version while testing.


V_18_UI_Cleanup_v4:
- Reworked the filter toolbar so Search, Provider Group, and Auto-Save live on the same horizontal row.
- Kept Add all / Remove all / Saved controls directly under the search input.
- No backend changes.


V_18_UI_Cleanup_v5:
- Added `Don't show SD channels` checkbox above the search bar.
- When enabled, channels from provider group `LOW BANDWIDTH` are hidden from the working list.
- Search, Add all, and Remove all operate only on the filtered list.


V_18_UI_Cleanup_v6:
- Fixed SD filter label visibility/readability.
- Renamed SD filter to `Hide SD / LOW BANDWIDTH channels`.
- Tightened toolbar alignment so Provider Group and Auto-save sit cleanly on the same row as Search.


V_18_UI_Cleanup_v7:
- Reworked filter area into three clean rows:
  1. Hide SD / LOW BANDWIDTH checkbox
  2. Search, Provider Group, and Auto-save aligned vertically
  3. Add all / Remove all / Saved buttons under Search
- Fixes the vertical alignment issue from v6.


V_18_UI_Cleanup_v8:
- Fixed empty `custom.m3u` generation after the v18 API/core refactor.
- API routes now reference shared state through the `core` module instead of copied globals.
- This keeps selected channel state and loaded channel catalog in sync when saving.
- Removed the stale `/epg.xml` route from the cleanup branch.
