# HDHomeRun compatibility experiment

This branch makes the curated M3U Web Picker lineup behave like a local HDHomeRun-style tuner without changing the stable `experiments-refacor` branch.

The experiment has two halves:

1. Flask serves the HDHomeRun HTTP surface and direct MPEG-TS tuner streams.
2. A small host-side Python daemon owns UDP/TCP port 65001 for HDHomeRun discovery/control.

The discovery daemon intentionally runs on the Mac rather than inside Docker Desktop. LAN broadcast/multicast discovery is host-network behavior, while the media/API surface remains in the existing container on port 1000.

## What is implemented

- Binary HDHomeRun discovery request parsing and reply framing on UDP 65001.
- Legacy single-device-type and current multi-device-type discovery request handling.
- SiliconDust-compatible frame CRC handling and TLV encoding.
- A device ID that passes the published HDHomeRun device-ID self-check (`10500009` by default).
- `/discover.json`.
- `/lineup_status.json`.
- `/device.xml` and `/capability` compatibility metadata.
- `/lineup.json`, `/lineup.xml`, and `/lineup.m3u`.
- `/auto/v<GuideNumber>` automatic tuner allocation.
- `/tunerN/v<GuideNumber>` explicit tuner allocation.
- Direct H.264/AAC MPEG-TS output rather than redirecting the app to the IPTV provider.
- HEAD probing of tuner URLs without consuming a tuner lease.
- A four-tuner pool by default.
- Minimal TCP 65001 GET/SET compatibility for basic `hdhomerun_config` queries.
- Discovery/control request logging in the host helper so a failed phone test tells us which protocol boundary it reached.
- No fabricated `DeviceAuth`. `M3U_HDHR_DEVICE_AUTH` is blank unless a legitimate value is explicitly supplied.

## Rebuild the experimental branch

No Docker volumes are removed by these commands.

```bash
git switch experiments-refacor-hdhr-roku && \
git pull --ff-only origin experiments-refacor-hdhr-roku && \
docker compose down && \
docker compose up -d --build --force-recreate && \
docker compose ps
```

## Start host-side HDHomeRun discovery

Run this in a second Terminal and leave it in the foreground for the first tests so incoming discovery/control activity is visible:

```bash
python3 tools/hdhr_discovery_host.py --base-url http://10.0.0.22:1000
```

If the Mac's LAN IP changes, substitute the current LAN IP and set `M3U_LAN_HOST` to the same address before rebuilding the container.

The helper owns both UDP and TCP port 65001. If startup reports that the address is already in use, find the existing process before retrying rather than starting multiple helpers. If macOS asks whether Python may accept incoming network connections, allow it for this LAN test.

## Test ladder

### 1. HTTP identity

```bash
curl -s http://10.0.0.22:1000/discover.json | python3 -m json.tool
```

Expected basics include:

- `DeviceID` = `10500009` unless overridden.
- `BaseURL` = `http://10.0.0.22:1000`.
- `LineupURL` = `http://10.0.0.22:1000/lineup.json`.
- `TunerCount` = `4` unless overridden.

Also check the compatibility metadata:

```bash
curl -s http://10.0.0.22:1000/lineup_status.json | python3 -m json.tool
curl -s http://10.0.0.22:1000/device.xml
```

### 2. Lineup

```bash
curl -s http://10.0.0.22:1000/lineup.json | python3 -m json.tool
```

Every served curated channel should have `GuideNumber`, `GuideName`, and a local `/auto/v...` URL. Provider credentials/URLs should not be exposed here.

### 3. MPEG-TS tuner stream

Pick one actual `GuideNumber` from the lineup and record five seconds:

```bash
curl --max-time 5 http://10.0.0.22:1000/auto/v1 -o /tmp/m3u-hdhr-test.ts
```

Use the real guide number if the first channel is not `1`. A non-empty MPEG-TS file proves the HTTP tuner path is reaching ffmpeg and the provider stream.

If `ffprobe` is installed on the host:

```bash
ffprobe /tmp/m3u-hdhr-test.ts
```

The experiment normalizes output to H.264 video and AAC audio in MPEG-TS.

### 4. SiliconDust reference discovery client

If `hdhomerun_config` is already installed:

```bash
hdhomerun_config discover
```

The compatibility device should appear as `10500009` at the Mac's LAN address. The host helper should simultaneously print the incoming discovery request and the device types requested by the client.

### 5. SiliconDust control protocol

If discovery succeeds, query the minimal TCP control surface:

```bash
hdhomerun_config 10500009 get /sys/model
hdhomerun_config 10500009 get /sys/hwmodel
hdhomerun_config 10500009 get /sys/version
```

The helper logs these GETs too. This gate is deliberately before the phone app. If the reference client cannot discover/query the device, the mobile app is not a useful debugging target yet.

### 6. Official HDHomeRun phone app

Only after the first five checks work, open the phone app on the same LAN. On iOS, make sure the app has Local Network permission.

If it still refuses the device, leave the host helper visible and note what it logs. Useful distinctions are:

- no UDP request reaches the host helper;
- discovery request arrives but the device is not accepted;
- the phone requests `/discover.json` but not `/lineup.json`;
- it requests the lineup but never opens `/auto/v...`;
- it opens the tuner URL but playback fails.

Those boundaries tell us whether the remaining problem is LAN discovery, device validation/cloud enrollment, lineup handling, or stream transport.

## HTTP diagnostics

The app also exposes:

```text
/api/hdhomerun/status
```

It reports the advertised device metadata, lineup count, configured tuner count, active tuner indexes, and available tuner slots.

## DeviceAuth / cloud limitation

The local compatibility layer does not invent a SiliconDust `DeviceAuth` value. Local discovery, lineup, and tuner transport can be implemented from the published protocol, but full behavior of the consumer HDHomeRun application may additionally depend on SiliconDust cloud/guide services and legitimate device enrollment.

If the phone app gets through local discovery and HTTP lineup calls but then rejects the device before opening a stream, that is the next boundary to investigate. The correct response is to inspect the app's actual network behavior, not to fabricate credentials.
