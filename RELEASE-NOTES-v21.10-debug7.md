# M3U Web Picker Sports v21.10-debug7

## Sports update scheduling

- Keeps the existing **Once daily** schedule and configured refresh time.
- Adds **Every X hours** scheduling with a validated 1–24 hour interval.
- Persists schedule mode and interval in SQLite.
- Recalculates and displays the next update immediately after settings change.
- Anchors interval mode to the most recently completed scan attempt. Manual updates, successful scheduled scans, failed scans, and cancelled scans all reset the interval.
- Prevents failed interval scans from retrying on every 30-second scheduler wake-up.
- Keeps **Update now** available when automatic updates are disabled.

## Preserved debug6 behavior

- Filters only clear off-air placeholders matching **No Event Today**, **No Events Today**, and **Signing Off**, with case/punctuation normalization.
- Keeps legitimate programming such as **Golf Channel Podcast With Rex & Lav**.
- Retains the 90-minute postgame grace period.
- Retains generated-channel stream URL isolation for Jellyfin.
- Retains primary/fallback provider support, live-only Xtream imports, and provider safety limits.
- Does not change the working M3U/XMLTV channel mapping behavior.
