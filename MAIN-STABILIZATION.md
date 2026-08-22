# Main Stabilization Backlog

`main` is the stabilization track. Experimental media and sports work should build on these foundations rather than introduce another parallel runtime path.

## Immediate hardening

### Priority 1 — Remote playback reliability

1.1. Restore Roku manual IP/hostname entry alongside automatic discovery and saved devices. Distinguish a missing LAN relay configuration from a completed scan that found no Roku devices.

1.2. Windows LAN relay configuration: resolved by writing `M3U_LAN_HOST` to `.env` and recreating the container, with Roku and Google Cast working afterward. The cross-platform Docker bootstrap now automates that path. Follow-up cleanup should correct stale localhost port guidance and keep configuration failures distinct from device-discovery failures.

### Remaining hardening

1. Add CI for Python tests and JavaScript syntax checks on pushes and pull requests.
2. Replace the injected/legacy UI shell with the modern UI as the real application template.
3. Close remaining SQLite connections explicitly, especially in onboarding paths used by Windows.
4. Consolidate persisted application settings into the shared `data/config.json` document.
5. Remove experimental branding and stale compatibility layers only after their callers are migrated and covered.
6. Split oversized modules and tests (`core.py`, `static/js/app.js`, `static/js/ui_sidebar.js`, and `tests/test_sports.py`) along existing domain boundaries.

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
