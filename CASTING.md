# Experimental Streaming / Casting / TV Playback

This document covers the experimental local playback, Google Cast, and Roku playback features on the `experiments` branch.

The experimental TV Guide runs at:

```text
http://localhost:1000/guide
```

The browser remains the controller and the Mac remains the media relay. Provider stream URLs, credentials, tokens, and source-specific details stay server-side.

## Current playback architecture

Local browser playback and remote-TV playback deliberately use separate ffmpeg-normalized outputs:

```text
Provider stream
    |
    +--> ffmpeg --> H.264/AAC fragmented MP4 --> local browser player
    |
    +--> ffmpeg --> H.264/AAC HLS ------------> Chromecast / Google TV
    |
    +--> ffmpeg --> H.264/AAC HLS ------------> Roku receiver
```

This separation is intentional. Cast/Roku changes should not destabilize the browser player, and remote devices never need direct access to provider URLs.

The controller should normally stay on `localhost`. Cast and Roku receivers fetch HLS from the Mac over the LAN.

## Experimental Docker/LAN layout

The experimental stack is isolated from the normal M3U Web Picker instance and publishes the application on host port `1000`.

Typical startup:

```bash
docker compose up -d --build
```

Then open:

```text
http://localhost:1000/guide
```

Runtime/debug state lives under:

```text
./debug-data
```

On macOS, the repository includes:

```bash
./scripts/detect-lan-host.sh
```

The current test network uses a `10.x.x.x` LAN. The app's receiver-facing URLs therefore look like:

```text
http://10.x.x.x:1000/guide/cast/<token>/stream.m3u8
```

The exact LAN address is runtime-specific; do not hard-code the example address into future logic.

## Local browser playback

1. Open `http://localhost:1000/guide`.
2. Find a channel in the curated lineup.
3. Press **Play**.
4. The browser uses the ffmpeg-backed H.264/AAC fragmented-MP4 path.
5. Press **Stop** for a true stop.

The guide API exposes the application playback route, not the raw provider URL.

Generated Sports Automation channels use the same guide/player routing as manually selected channels. A generated sports row can therefore be played locally, Cast, or sent to Roku through the same controller.

## Remote-device discovery

The guide attempts discovery automatically when it opens.

### Google Cast discovery

Google's Cast Sender SDK reports receiver availability. The **Cast** button is hidden when no Cast receivers are available and shown when Cast reports at least one receiver.

There is intentionally one Cast button even when multiple Google Cast devices exist. Google's native receiver picker owns device selection.

### Roku discovery

The experimental Roku discovery path scans the local `/24` used by the current `10.x.x.x` test network and probes Roku ECP on TCP port `8060`.

Discovery validates the Roku device identity through its ECP device information. A random host listening on port 8060 is not enough to qualify as a Roku.

When discovery returns devices:

- zero Roku devices: hide the normal Roku button;
- one or more Roku devices: prefer the browser's previously used Roku if it is still present, otherwise use the first discovered Roku;
- the discovered Roku name/IP is written into Diagnostics so it can still be inspected or manually tested.

The current automatic discovery proves that a Roku ECP device exists. It does **not** yet prove that the M3U Web Picker sideloaded receiver app is installed.

## Google Cast playback

Google Cast uses the Cast Application Framework sender plus the default receiver/media path.

### Requirements

- Mac and Cast receiver must be on the same LAN.
- Open the sender/controller at `http://localhost:1000/guide`.
- The receiver-facing HLS URL must be reachable over the Mac's LAN address.

### Starting Cast playback

1. Press **Play** on a channel so the guide has a current channel.
2. Press **Cast**.
3. Google's native receiver picker opens.
4. Choose a Chromecast/Google TV receiver.
5. M3U Web Picker starts the HLS relay and loads that HLS URL into the selected Cast session.
6. The remote device pulls the playlist and MPEG-TS segments from the Mac.

The browser remains the controller. While Cast is active, selecting another guide channel sends the newly selected channel through the Cast path rather than starting duplicate local playback.

### Disconnecting Cast

**Disconnect** is a handoff, not a destructive stop:

1. stop remote media;
2. stop the Cast HLS relay;
3. end the Cast session;
4. wait for the Cast session to actually disappear;
5. resume the same current channel in the local browser if the channel has not changed in the meantime.

The channel identity guard prevents an asynchronous Cast shutdown from resurrecting an old channel after the user has selected something else or pressed **Stop**.

## Roku playback

Roku playback uses a sideloaded experimental Roku receiver application plus the same H.264/AAC HLS relay concept used by Cast.

### Enable Roku developer mode

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

If required, enable Roku local-network control under the Roku setting for **Control by mobile apps**.

### Install the Roku receiver

The receiver ZIP is included at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver-exp1.zip
```

From a computer on the same LAN, open the Roku's address in a browser:

```text
http://ROKU-IP
```

Log in with:

```text
Username: rokudev
Password: <your Roku developer-mode password>
```

In the Roku **Development Application Installer**:

1. choose **Install with zip**;
2. upload `m3u-web-picker-roku-receiver-exp1.zip`;
3. do not extract the receiver ZIP first.

Roku permits only one sideloaded developer application at a time.

### Starting Roku playback

Normal use no longer requires manually typing the Roku IP before every session. Automatic discovery chooses the remembered/first Roku and fills the Diagnostics field.

1. Open `http://localhost:1000/guide`.
2. Press **Play** on a channel.
3. Press **Roku**.
4. M3U Web Picker launches the sideloaded receiver and passes the current HLS session.
5. The Roku pulls the HLS media from the Mac over the LAN.

Diagnostics still exposes **Roku TV IP** and **Test Roku** for development/troubleshooting.

### Disconnecting Roku

**Disconnect Roku** stops the Roku relay, sends the Roku back to Home, and resumes the same current channel in the local browser when that channel is still current.

As with Cast, **Stop** remains a true stop and clears the current channel so a later asynchronous handoff cannot restart it.

## Cast <-> Roku direct switching

Direct remote-to-remote switching is supported. The user does not need to press Disconnect before choosing the other remote target.

This path is serialized because Cast and Roku are separate asynchronous state machines.

The current transition rules are:

```text
Local -> Cast              allowed
Local -> Roku              allowed
Cast  -> Roku              tear down Cast completely, then start Roku
Roku  -> Cast              tear down Roku, then open/complete Cast selection
Cast  -> Disconnect        return current channel to local
Roku  -> Disconnect        return current channel to local
Stop                       true stop; do not resume anything
```

A shared remote-transition lock disables/ignores additional remote actions while a handoff is already in flight. This prevents rapid button clicking from overlapping two receiver transitions.

For Cast -> Roku specifically, M3U Web Picker waits until the Cast session is actually gone before Roku startup proceeds. Merely requesting Cast teardown is not considered sufficient.

## Known Cast picker edge case

When Roku is active and the user presses **Cast**, Roku is shut down before Google's native receiver picker completes. If the user then cancels the Cast picker, Roku has already been stopped and the browser does not currently auto-resume local playback.

This is a known experimental edge case and is intentionally left alone for now. It does not affect the normal successful Roku -> Cast path.

## Diagnostics

The expandable **Diagnostics** section below the player currently contains development/troubleshooting controls such as:

- current Cast relay address;
- **Test LAN**;
- discovered/selected **Roku TV IP**;
- **Test Roku**;
- detected Roku name/model/status;
- Cast SDK/session status.

Normal device discovery is automatic; Diagnostics is no longer intended to be the primary day-to-day device-selection workflow.

## Current verified behavior

Manually exercised on the experimental branch:

- local ffmpeg-normalized browser playback;
- Cast availability discovery;
- Google native Cast receiver picker;
- Cast HLS playback over the LAN;
- automatic Roku discovery on the current LAN;
- Roku HLS playback through the sideloaded receiver;
- channel changes while remote playback is active;
- Cast Disconnect -> same channel resumes locally;
- Roku Disconnect -> same channel resumes locally;
- direct Cast -> Roku handoff;
- direct Roku -> Cast handoff;
- rapid/repeated remote-button clicking is blocked while a handoff is in flight;
- **Stop** remains a true stop and prevents stale async handoffs from reviving old playback.

These are manual experimental tests, not a claim of complete browser/device compatibility or full automated integration coverage.

## Streaming/casting backlog

### Multiple Roku support

Current behavior chooses the remembered Roku when possible, otherwise the first discovered Roku. That is sufficient for a one-Roku test environment but is not the finished UX.

Required future behavior:

- **0 Rokus:** hide the Roku control;
- **1 Roku:** the Roku button sends directly to that device;
- **2+ Rokus:** provide a small selector/dropdown rather than silently choosing the first device;
- remember the last-used Roku and preselect it when it is still available;
- show a useful device name/model and enough network identity to distinguish similar TVs;
- keep discovery restricted to verified Roku devices, not arbitrary hosts;
- consider verifying that the M3U Web Picker sideloaded receiver is installed before presenting a Roku as fully playback-ready.

### Other follow-up items

- Revisit the Roku -> Cast picker-cancel behavior if it becomes annoying in real use.
- Generalize Roku discovery beyond the current `10.x.x.x /24` experimental-network assumption before treating it as portable production behavior.
- Continue testing teardown/reconnect behavior across real Cast and Roku firmware variants.

## Scope

All streaming/casting work described here remains experimental and belongs on the `experiments` branch until it is intentionally promoted to another release branch.
