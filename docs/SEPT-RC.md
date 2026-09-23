# September release candidate

Base: local `main` at `17cd474` (includes the existing channel 0.2 phone remote).

## Included

- Audio-only Listen in the TV Guide, with FFmpeg removing video before delivery.
- Phone media controls, playback diagnostics, and Listen/Stop controls.
- Independent Guide PWA installation and restoration of recent Listen sessions.
- Transfer playback between Guide windows only after the replacement starts playing.
- Release FFmpeg processes and session slots when browser responses close early.
- Let the local video player use the full guide width without a viewport-height cap.
- Make the Guide Back button return to the overview consistently.

The seven Listen commits were cherry-picked from the existing
`feature/audio-only-listen-with-remote` history, ending at `6a1583f`.
The player sizing and Back-button changes were extracted separately from
`experimental-network_funssies`.

## Deferred

No new sports stats channels, score overlays, experimental multiview, phone
notification enhancements, LAN inventory, location inference, viewing tracker,
ACR, or microphone diagnostics were brought over. Existing features inherited
from local main remain part of this base. No installer changes were imported.

## Validation

On September 22, 2026, 60 focused tests passed across the Guide Listen, guide
EPG, guide remote, browser bridge, media pipeline, remote, and refactor suites.
Node syntax checks passed for the changed Guide scripts and service worker.
Git whitespace validation passed. Real-device audio playback and mobile PWA
handoff have not been re-tested for this release candidate.

This branch is local only; no deployment or publication was performed.
