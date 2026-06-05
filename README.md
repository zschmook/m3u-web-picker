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


V_18_UI_Cleanup_v9:
- Added `python3 app.py -d` / `--dev` to run on port 9998.
- Added Manage Order button next to custom.m3u URL.
- Added Custom Playlist Order modal.
- Selected channels now keep a persistent `sort_order` in SQLite.
- Saving order rewrites `custom.m3u` in that order.


V_18_UI_Cleanup_v10:
- Fixed Manage Order modal not loading saved channels.
- v9 wrote custom.m3u from DB order first, so newly selected in-memory channels could be missing from the DB/order payload.
- v10 writes from the current selected_ids, preserves existing sort_order, and refreshes the DB before opening the order modal.


V_18_UI_Cleanup_v12:
- Removed the search textbox from the Manage Order popup.
- Popup now focuses only on Move Up / Move Down and Save Order.


V_18_UI_Cleanup_v15:
- Rebuilt from v12.
- Custom Groups UI is hidden using `d-none` instead of being commented out.
- This preserves the DOM elements that the existing JavaScript expects.
- Provider URL loading should work again.


# V19 M3U Proxy

Promoted from the v18 UI cleanup branch.

New in V19:
- Auto-generates `tvg-chno` channel numbers in `custom.m3u`.
- Channel numbers follow the saved custom playlist order.
- Reordering channels in Manage Order rewrites channel numbers on save.
- Existing provider `tvg-chno` values are replaced.
- Missing `tvg-chno` values are added.
- Custom Groups UI remains hidden.


## V19.1

- Added an inline `X` button inside the search box.
- The button appears only when search text exists.
- Clicking it clears search, refreshes the channel list, and keeps focus in the search box.
