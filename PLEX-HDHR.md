# Plex / HDHomeRun Compatibility Experiment

Status: **experimental, `experiments` branch only**.

The goal is not to replace the normal M3U/XMLTV outputs. M3U Web Picker remains the source of truth for the curated lineup and final XMLTV guide. The HDHomeRun facade exists only to present that same lineup to Plex in the tuner-shaped form Plex expects.

## The two normal served outputs — do not forget these

These are the same URLs shown at the top of the M3U Web Picker UI:

```text
Channels: http://<server>:10000/playlist/channels.m3u
EPG:      http://<server>:10000/epg/epg.xml
```

On the experimental Mac used during development, the browser UI may display them as:

```text
http://localhost:10000/playlist/channels.m3u
http://localhost:10000/epg/epg.xml
```

For another device on the LAN, replace `localhost` with the Mac/server LAN address, for example:

```text
http://10.0.0.22:10000/playlist/channels.m3u
http://10.0.0.22:10000/epg/epg.xml
```

### Plex-specific rule

For normal M3U clients, use the Channels M3U plus the EPG XMLTV URL.

For Plex, **do not use the M3U as the tuner source**. The experimental HDHomeRun facade replaces the M3U side of Plex setup. Plex still uses the same final `epg.xml` as the guide source.

```text
M3U Web Picker curated lineup
           |
           +--> /playlist/channels.m3u   normal IPTV clients
           |
           +--> HDHomeRun HTTP facade    Plex tuner source
           |
           +--> /epg/epg.xml             XMLTV guide for both
```

## First-pass HDHomeRun HTTP endpoints

The experimental server now exposes:

```text
http://<server>:10000/discover.json
http://<server>:10000/lineup_status.json
http://<server>:10000/lineup.json
http://<server>:10000/device.xml
```

Compatibility alias:

```text
http://<server>:10000/capability
```

The advertised tuner count is currently **2**.

`lineup.json` is generated from `core.curated_channels_for_guide()`, so Plex sees the same manual + generated-sports lineup and channel numbering as the built-in TV Guide rather than a separately parsed copy of the M3U.

Each lineup entry points back to a server-owned stream URL:

```text
http://<server>:10000/hdhr/stream/<channel-number>
```

There is also an HDHomeRun-style compatibility alias:

```text
http://<server>:10000/auto/v<channel-number>
```

Provider URLs and credentials remain server-side.

## Plex stream format

The Plex/HDHomeRun stream path is intentionally separate from browser playback.

```text
provider stream
    -> ffmpeg normalization
    -> H.264 video + AAC audio
    -> MPEG-TS
    -> Plex
```

This uses the same common ffmpeg normalization settings as the browser/Cast/Roku experiments, but with an MPEG-TS muxer for the tuner facade.

The existing playback paths remain unchanged:

```text
Browser: provider -> ffmpeg -> fragmented MP4
Cast:    provider -> ffmpeg -> HLS/MPEG-TS segments
Roku:    provider -> ffmpeg -> HLS/MPEG-TS segments
Plex:    provider -> ffmpeg -> continuous MPEG-TS
```

## First manual Plex test after restart

UDP tuner discovery is deliberately **not part of the first pass**. Prove the HTTP tuner and stream path first.

1. Start/restart the experimental stack.
2. Verify the app loads at `http://localhost:10000`.
3. From the Plex server machine, verify this opens and returns JSON:

   ```text
   http://<M3U-WEB-PICKER-LAN-IP>:10000/discover.json
   ```

4. Verify the lineup returns the expected curated channels:

   ```text
   http://<M3U-WEB-PICKER-LAN-IP>:10000/lineup.json
   ```

5. In Plex **Live TV & DVR**, add a tuner manually if Plex does not discover it automatically.
6. Enter the M3U Web Picker LAN address/port as the tuner location:

   ```text
   <M3U-WEB-PICKER-LAN-IP>:10000
   ```

   Example from the current experimental network:

   ```text
   10.0.0.22:10000
   ```

7. Confirm Plex sees the expected channel count/names.
8. When Plex asks for guide data, use the existing final XMLTV URL:

   ```text
   http://<M3U-WEB-PICKER-LAN-IP>:10000/epg/epg.xml
   ```

9. Map/confirm the channels in Plex.
10. Test one ordinary manual channel and one generated sports channel.
11. If tuning fails, check the M3U Web Picker container log before changing lineup or EPG generation.

## Quick curl sanity check

From a machine that can reach the experimental server:

```bash
curl -sS http://10.0.0.22:10000/discover.json | python3 -m json.tool
curl -sS http://10.0.0.22:10000/lineup_status.json | python3 -m json.tool
curl -sS http://10.0.0.22:10000/lineup.json | python3 -m json.tool
curl -sS http://10.0.0.22:10000/device.xml
```

For one known channel number:

```bash
curl -I http://10.0.0.22:10000/hdhr/stream/1
```

A `200` with `Content-Type: video/mp2t` proves the route resolves without starting a full GET stream for the HEAD request.

## Plex / HDHomeRun experiment backlog

- [x] Preserve the normal Channels M3U endpoint.
- [x] Preserve the normal final EPG endpoint.
- [x] Document the Channels and EPG URLs shown at the top of the app UI.
- [x] Add `/discover.json`.
- [x] Add `/lineup_status.json`.
- [x] Add `/lineup.json` from the exact curated lineup.
- [x] Add `/device.xml` / `/capability`.
- [x] Advertise two tuners for the first experiment.
- [x] Add server-side manual/generated channel resolution.
- [x] Add dedicated H.264/AAC MPEG-TS output for Plex.
- [x] Add `/hdhr/stream/<channel-number>`.
- [x] Add `/auto/v<channel-number>` compatibility alias.
- [x] Add regression tests for tuner identity, lineup, and manual/sports resolution.
- [ ] Run the full local test suite after pulling the changes.
- [ ] Verify the four HTTP identity/lineup endpoints from the LAN after reboot.
- [ ] Manually add the tuner in Plex by LAN IP/port.
- [ ] Verify Plex accepts the two-tuner identity.
- [ ] Verify Plex accepts the exact curated lineup/channel numbering.
- [ ] Verify one manual-channel MPEG-TS tune in Plex.
- [ ] Verify one generated-sports MPEG-TS tune in Plex.
- [ ] Verify `epg.xml` maps correctly after tuner setup.
- [ ] Inspect Plex logs for any requests/endpoints it expects that the first facade does not yet implement.
- [ ] Add UDP HDHomeRun discovery on port 65001 only after the manual HTTP path works.
- [ ] Enforce the advertised two-stream tuner limit if Plex behavior makes that necessary.
- [ ] Decide whether HDHomeRun emulation needs a UI enable/disable switch before promotion outside `experiments`.
- [ ] Keep the stable `sports` branch untouched until this experiment has been proven independently.

## Why UDP discovery is deferred

The first test intentionally separates concerns. If Plex cannot use a manually specified HTTP tuner at `http://<server>:10000`, adding UDP discovery only creates another failure surface.

Once manual tuner setup, lineup ingestion, MPEG-TS tuning, and XMLTV mapping are proven, the next step is HDHomeRun discovery on UDP port `65001` so Plex can find M3U Web Picker automatically.
