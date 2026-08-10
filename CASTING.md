# Experimental Casting / TV Playback

This document covers the experimental playback and casting features on the `experiments` branch.

The experimental TV Guide runs at:

```text
http://localhost:1000/guide
```

The Mac remains the media relay. Provider stream URLs and credentials stay server-side.

## Playback architecture

Browser playback and TV playback use separate ffmpeg-normalized outputs:

```text
Provider stream
    |
    +--> ffmpeg --> H.264/AAC fragmented MP4 --> browser
    |
    +--> ffmpeg --> H.264/AAC HLS ------------> Chromecast / Roku
```

The browser controller should stay on `localhost`, while Chromecast and Roku must fetch the HLS relay over the Mac's LAN address.

## LAN address

On macOS, determine the active LAN address with:

```bash
./scripts/detect-lan-host.sh
```

For the current test machine, the LAN address is:

```text
10.0.0.22
```

The experimental Docker stack publishes the guide and relay on host port `1000`, so TV devices fetch media from URLs beginning with:

```text
http://10.0.0.22:1000
```

If the Mac moves to another network, use the new LAN address rather than assuming a `192.168.*` or `10.0.0.*` subnet.

## Browser playback

1. Open:

   ```text
   http://localhost:1000/guide
   ```

2. Find a channel in the curated lineup.
3. Press **Play**.
4. The browser player uses the ffmpeg-backed H.264/AAC fragmented-MP4 path.
5. Press **Stop** to stop local playback.

The raw provider stream is not exposed to the browser as a direct playback/download URL.

## Chromecast

Chromecast playback uses Google Cast plus a separate HLS relay.

### Requirements

- Chromecast and Mac must be on the same LAN.
- The experimental guide must remain open in Chrome at `http://localhost:1000/guide`.
- The HLS relay must be reachable from the Chromecast using the Mac's LAN address.

### Send a channel

1. Play a channel in the experimental TV Guide.
2. Press **Cast** beside the player controls.
3. Choose the Chromecast/Google Cast receiver, for example **Office TV**.
4. The guide creates a Cast receiver session and loads the HLS media URL on the receiver.
5. The Chromecast pulls the `.m3u8` playlist and MPEG-TS segments directly from the Mac over the LAN.

The controller remains on:

```text
http://localhost:1000/guide
```

The receiver media URL uses the LAN host, for example:

```text
http://10.0.0.22:1000/guide/cast/<session>/stream.m3u8
```

### Chromecast diagnostics

Expand **Diagnostics** directly below the player to see the current Cast relay address and use **Test LAN**.

If a Cast session opens and channel metadata appears on the TV but video never starts, verify that the relay address shown in Diagnostics matches the Mac's actual active LAN IP.

## Roku

Roku playback currently uses a sideloaded experimental Roku receiver app. This is a developer-mode workflow.

### 1. Enable Roku developer mode

From the Roku home screen, press:

```text
Home
Home
Home
Up
Up
Right
Left
Right
Left
Right
```

Choose **Enable installer and restart**, accept the developer agreement, set a developer password, and allow the Roku to restart.

Note the Roku's LAN IP address.

If required, enable Roku local control under the Roku setting for **Control by mobile apps**.

### 2. Install the Roku receiver

The receiver ZIP is included in the repository at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver-exp1.zip
```

A bundled copy may also be present at the repository root in experimental release ZIPs.

From a computer on the same LAN, open the Roku's IP address in a browser:

```text
http://ROKU-IP
```

Log in with:

```text
Username: rokudev
Password: <your Roku developer-mode password>
```

In the Roku **Development Application Installer**:

1. Choose **Install with zip**.
2. Upload `m3u-web-picker-roku-receiver-exp1.zip`.
3. Do not extract the receiver ZIP first.

Roku permits only one sideloaded developer application at a time.

### 3. Connect the TV Guide to the Roku

1. Open the experimental guide:

   ```text
   http://localhost:1000/guide
   ```

2. Expand **Diagnostics** directly below the player.
3. Enter the Roku's LAN IP in **Roku TV IP**.
4. Press **Test Roku**.
5. A successful test displays the detected Roku device name/model and remembers the IP in that browser.

Example from development testing:

```text
Found 65" TCL Roku TV · 65S435
```

### 4. Send a channel to Roku

1. Press **Play** on a channel so it becomes the current guide channel.
2. Press **Roku** beside the **Cast** and **Stop** controls.
3. The app launches the sideloaded Roku receiver and passes it the current HLS session.
4. The Roku pulls the H.264/AAC HLS stream from the Mac over the LAN.
5. Use **Disconnect Roku** when you want to stop Roku playback and return to the Roku home screen.

The media path is conceptually:

```text
provider --> ffmpeg --> H.264/AAC HLS --> Mac LAN :1000 --> Roku
```

## Diagnostics

The **Diagnostics** section is directly below the TV Guide player. It currently contains the networking/device setup needed for experimental TV playback, including:

- Cast relay address
- **Test LAN**
- **Roku TV IP**
- **Test Roku**
- detected Roku device information

These controls are intentionally experimental and may move as the TV Guide UI is cleaned up.

## Docker

The experimental build is isolated from the normal M3U Web Picker instance and uses host port `1000`.

Typical startup:

```bash
docker compose up -d --build
docker compose ps
```

Then open:

```text
http://localhost:1000/guide
```

Runtime/debug state for this experiment lives under `./debug-data` in the experimental build.

## Current status

Verified during development:

- browser playback through ffmpeg-normalized H.264/AAC
- Chromecast receiver session creation
- Chromecast HLS playback over the LAN
- Roku discovery/test over the LAN
- Roku playback through the sideloaded receiver

These casting features remain experimental and belong on the `experiments` branch until they are intentionally promoted.