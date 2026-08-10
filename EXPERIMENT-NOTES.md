# v30-experiments / exp1

Branch target: `experiments`.

This is a full standalone experimental build, not a patch and not part of the
main release line.

Isolation / safety:

- host URL: `http://localhost:1000`
- Compose project: `m3u-picker-v30-experiments`
- container: `m3u-picker-v30-experiments`
- runtime state: local `./debug-data`
- nightly backups: disabled
- no main `m3u-picker-data` named volume is mounted
- starts with clean experimental state

TV Guide experiment:

- top-level TV Guide pop-out
- current curated manual + generated sports lineup
- search/filter
- Play/Stop controls
- provider URLs remain hidden from the guide API
- ffmpeg is installed inside the Docker image
- selected streams are transcoded to H.264/AAC fragmented MP4 for browser playback
- direct-stream fallback remains available for diagnostics

Start it with:

```bash
docker compose up -d --build
```

Then open `http://localhost:1000`.

exp2 UI fix:
- fixed the TV Guide sticky table header offset that could cover the first/only curated channel row; the guide list now owns its scroll area and the header sticks at top: 0.

- changed the TV Guide launcher from `popup=yes` to a normal browser-window request with standard browser chrome so it can be moved between displays more predictably on macOS/Chrome.


exp3 Cast experiment:
- adds a Cast button inside the TV Guide/player pop-out window.
- uses Google's Web Sender SDK + Default Media Receiver.
- when connected, Play sends the selected curated ffmpeg MP4 stream to the Cast receiver and keeps the guide window as the controller.
- Cast media responses add permissive CORS headers; provider source URLs remain server-side.
- because the receiver cannot reach localhost, the guide has a Cast media host field. Enter the Mac LAN address (for example 192.168.1.25:1000) when the guide itself was opened at localhost.
- disconnecting Cast does not silently restart local playback; press Play to resume locally.

## exp4

- Removed the raw/direct stream link from the TV Guide player UI.
- The guide API no longer advertises raw provider-facing stream URLs.
- Browser player requests `controlsList="nodownload"`.
- ffmpeg playback responses explicitly use `Content-Disposition: inline` and `X-Content-Type-Options: nosniff`.
- Google Cast experiment remains intact for Chromecast/Google TV testing.
- Still isolated on port 1000 with debug-data state and no nightly backups.


## exp5

- LAN-aware Chromecast test build. Docker now explicitly publishes port 1000 on all host interfaces.
- Default LAN relay is `http://10.0.0.22:1000` via `M3U_LAN_HOST` / `M3U_EXTERNAL_PORT`.
- Cast relay is server-configured and shown read-only in the guide; no manual IP typing.
- Adds a `Test LAN` button and `/api/guide/ping` endpoint so the Mac browser can verify the LAN path before asking Chromecast to fetch media.
- The app is reachable at `http://10.0.0.22:1000`, but Google Cast sender discovery should be initiated from `http://localhost:1000/guide` because localhost is the secure/trustworthy local sender origin while plain HTTP LAN IP is not.
- Local browser playback remains fragmented MP4; Cast media path is unchanged from exp4 so the Chromecast test isolates networking/sender behavior rather than changing two things at once.

## exp6

- Disables the browser Remote Playback API on the local `<video>` element (`disableRemotePlayback`) so Chrome's media controls cannot masquerade as the app's Cast path.
- The player button is now labeled `Cast to Chromecast` to make the intended path unambiguous.
- Cast session flow is explicit: `CastContext.requestSession()` opens the receiver picker, the selected `CastSession` is captured, and exactly one `CastSession.loadMedia()` call sends the LAN URL (`http://10.0.0.22:1000/...`) to the Default Media Receiver.
- Removes the exp5 double-load race where both `SESSION_STARTED` and the `requestSession()` continuation could attempt to load the same channel.
- Adds receiver/session/media diagnostics in the guide status so the UI says which receiver was selected and which LAN media URL was handed to it.
- Local browser playback and ffmpeg output are otherwise unchanged.


## exp7 — dual playback pipeline

- Keeps the working browser player unchanged: provider -> ffmpeg -> H.264/AAC fragmented MP4 -> Chrome.
- Adds a Cast-only pipeline: provider -> ffmpeg -> rolling HLS playlist + MPEG-TS segments -> LAN -> Chromecast/Google TV.
- The sender starts the HLS relay first, waits for the playlist/first segment, then explicitly calls CAF `loadMedia()` with `application/x-mpegurl`.
- Cast HLS files are ephemeral under `/tmp/m3u-web-picker-cast-hls`; starting a new Cast channel stops the previous Cast ffmpeg process.
- Stop/disconnect also tears down the Cast HLS process.
- HLS playlist/segments include receiver-facing CORS and Range-related response headers.
- Browser and Cast playback remain separate so changing Cast behavior cannot regress the already-working browser player.

## exp8
- Corrected the test Mac LAN relay from the invalid `192.168.1.22` assumption to the actual active Wi-Fi address `10.0.0.22` (`en0`, `/24`).
- Chromecast media URLs are now built as `http://10.0.0.22:1000/guide/cast/<token>/stream.m3u8` by default.
- Browser sender/controller remains `http://localhost:1000/guide` so Cast sender initialization keeps the localhost trustworthy-origin behavior.
- Added `scripts/detect-lan-host.sh` for future network changes; it reports the IPv4 address on the macOS default-route interface instead of assuming a `192.168.*` subnet.
- Cleaned stale `exp2` cache-buster references in the main app template/tests so browser assets identify this build consistently as exp8.

## Current consolidated streaming/casting state — Aug. 10, 2026

The experiment has moved beyond the early Cast-only stages above. The current `experiments` branch now treats the TV Guide as one controller with three playback targets:

```text
local browser
Google Cast
Roku
```

Current behavior:

- Local playback remains its own ffmpeg-normalized H.264/AAC fragmented-MP4 path.
- Cast and Roku use H.264/AAC HLS relays that are reachable from the LAN while provider URLs and credentials stay server-side.
- Cast receiver availability is automatic through the Google Cast Sender SDK; one Cast button opens Google's native receiver picker even when multiple Cast devices exist.
- Roku discovery runs automatically when the guide opens. The current experiment scans the local `10.x.x.x /24`, probes Roku ECP on port 8060, and validates Roku identity before exposing the normal Roku control.
- The previously used Roku is preferred when it is still discovered; otherwise the first discovered Roku is selected and written into Diagnostics.
- Roku playback still requires the sideloaded M3U Web Picker Roku receiver app.
- Diagnostics remains available for `Test LAN`, Roku IP/device details, and `Test Roku`, but normal playback no longer requires manual Roku IP entry every time.
- Generated sports channels and manual channels share the same TV Guide playback routes, so either type can be played locally, Cast, or sent to Roku.

### Disconnect and remote handoff semantics

Disconnect is now a handoff back to local playback rather than simply leaving the guide stopped:

- Cast Disconnect stops Cast media/HLS, ends the Cast session, then resumes the same current channel locally once teardown is confirmed.
- Roku Disconnect stops the Roku relay, returns Roku to Home, then resumes the same current channel locally.
- Both handoffs guard channel identity so a delayed async completion cannot resurrect an old channel after a channel change.
- **Stop** is still a true stop and clears the current channel so nothing later auto-resumes it.

Direct remote switching is supported without requiring the user to press Disconnect first:

- Cast -> Roku tears Cast down completely before Roku starts.
- Roku -> Cast tears Roku down before opening/finishing the Cast path.
- A shared remote-transition lock serializes these operations and ignores additional remote-button clicks while a handoff is in flight.
- Cast -> Roku explicitly waits until the Cast session is actually gone before Roku startup proceeds.

This direct-switch locking was added after manual stress testing found that rapidly changing remote targets without first disconnecting could overlap the two asynchronous state machines.

### Known edge case

If Roku is active, the user presses Cast, and then cancels Google's native Cast picker, Roku has already been stopped. The browser does not currently auto-resume local playback in that cancellation path. This is known and intentionally deferred unless it becomes annoying in normal use.

### Streaming/casting backlog

- **Multiple Roku support is required.** Current behavior selects the remembered Roku or the first discovered Roku. Desired UX:
  - 0 Rokus: hide Roku control.
  - 1 Roku: direct Roku button.
  - 2+ Rokus: small selector/dropdown.
  - remember/preselect the last-used Roku when still available.
  - show enough name/model/network identity to distinguish similar TVs.
  - continue filtering discovery to verified Roku devices.
  - consider verifying that the sideloaded M3U Web Picker receiver is actually installed before treating a discovered Roku as fully playback-ready.
- Generalize Roku discovery beyond the current `10.x.x.x /24` experimental-network assumption before production promotion.
- Revisit Roku -> Cast picker-cancel recovery only if real use makes it worth fixing.
- Keep testing remote teardown/reconnect behavior against additional Roku and Cast firmware/device combinations.

The detailed current architecture and setup instructions live in `CASTING.md`.
