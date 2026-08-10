# v30-experiments / exp1

Branch target: `sports-experiments`.

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
