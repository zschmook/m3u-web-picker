# Main Stabilization Backlog

`main` is the stabilization track. Experimental media and sports work should build on these foundations rather than introduce another parallel runtime path.

## Immediate hardening

### Completed — Jellyfin settings dependencies

- [x] When **I understand the risks** is unchecked, disable **Clear cache after successful updates** and **Save Settings** so the displayed controls cannot represent an invalid combination.

### Completed — Modern UI template migration

- [x] Replace the injected/legacy UI shell with the modern UI as the real application template.

### Completed — SQLite connection cleanup

- [x] Close remaining SQLite connections explicitly, especially in onboarding paths used by Windows.

### Completed — Compatibility cleanup

- [x] Remove experimental branding and stale onboarding compatibility layers after migrating their callers. Retain active public EPG and playback facades until their callers are replaced.

### Priority 5 — Safe Docker port-change automation

- When the user changes **Settings → Network → Public URL port**, detect whether the requested port differs from Docker's currently published host port.
- Show a confirmation dialog: **“Changing the port requires Docker to restart. Continue?”** with explicit **OK** and **Cancel** actions.
- On confirmation, use a narrowly scoped host-side helper to update both `M3U_HOST_PORT` and `M3U_EXTERNAL_PORT` in `.env`, validate that the new port is available, recreate only the Picker container, wait for application health, and then run a Master Update so generated URLs are republished.
- If validation, restart, or health verification fails, restore the previous `.env` values and attempt to bring the prior configuration back online; show the user a clear recovery result.
- Do not mount the unrestricted Docker socket into the web app. Design the helper with the minimum permissions and command surface necessary, and support a manual fallback when automatic host integration is unavailable.

### Completed — Remote playback reliability

1.2. [x] Windows LAN relay configuration — resolved by writing `M3U_LAN_HOST` to `.env` and recreating the container. Roku and Google Cast were verified working afterward, and the cross-platform Docker bootstrap now automates that path.

### Remaining hardening

1. Investigate the channel-picker issue discovered during FFmpeg/Jellyfin testing; capture exact reproduction steps after the current playback test.
2. Add CI for Python tests and JavaScript syntax checks on pushes and pull requests.
3. Consolidate persisted application settings into the shared `data/config.json` document.
4. Split oversized modules and tests (`core.py`, `static/js/app.js`, `static/js/ui_sidebar.js`, and `tests/test_sports.py`) along existing domain boundaries.

## Deferred device work

- Roku manual IP entry and multiple-device behavior are deferred until a Roku stick is available for end-to-end testing. When resumed, distinguish a missing LAN relay configuration from a completed scan that found no Roku devices.
- Native/non-Docker packaging is deferred. Revisit a Nuitka-style host runtime with a platform-specific hardware discovery layer that functionally tests and exposes selectable FFmpeg device/encoder combinations (NVENC, Intel QSV, AMD AMF/VA-API, and macOS VideoToolbox), persists both encoder and device identity, and safely falls back when saved hardware is unavailable.

## Global FFmpeg foundation

### Scope boundary

- Defer sports alerts and multiview work until the global FFmpeg pipeline is stable on `main`.
- Do not add alert overlays, multiview composition, or feature-specific wrappers during the foundation phase.
- Ship FFmpeg processing disabled by default with its own explicit enable option in Settings.
- Settings must warn that application-wide live transcoding is computationally expensive and that a supported discrete GPU is strongly recommended, especially for multiple simultaneous streams.

1. Keep direct provider playback as the default and make the global media pipeline opt-in.
2. Route every curated manual and generated channel through stable opaque Picker URLs when the pipeline is enabled.
3. Resolve source URLs in one central media pipeline without exposing provider credentials.
4. Start FFmpeg on demand for active playback; never run one permanent process per configured channel.
5. Share input, normalization, filtering, process lifecycle, logging, and concurrency controls across every output adapter.
6. Preserve consumer-specific muxing:
   - Browser: fragmented MP4
   - Jellyfin/HDHomeRun: MPEG-TS
   - Roku/Google Cast: HLS
7. Expose Settings for pipeline enablement, FFmpeg health/path, encoder choice, normalization, concurrency, active sessions, and the last error.
8. After the foundation is stable, treat sports alerts as a future overlay provider plugged into the global filter graph rather than a sports-owned wrapper.

## FFmpeg acceptance coverage

- Pipeline-disabled playlists preserve direct playback.
- Pipeline-enabled playlists route all manual and generated channels through Picker.
- Provider credentials never appear in public URLs or errors.
- Client disconnects terminate FFmpeg reliably.
- Concurrent-session limits are enforced.
- Missing/invalid FFmpeg produces an actionable error.
- Windows executable paths containing spaces work.
- Browser, Jellyfin, Roku, and Cast keep their required output formats.
