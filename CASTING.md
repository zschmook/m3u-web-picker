# Experimental Casting / TV Playback

This document covers the experimental TV Guide playback paths currently available on the `experiments` branch.

The experimental guide runs at:

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

The HLS relay must be reachable