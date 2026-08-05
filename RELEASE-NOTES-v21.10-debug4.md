# M3U Web Picker Sports v21.10-debug4

This debug release fixes a Jellyfin-visible channel-number collision between large manual lineups and generated Sports Automation channels.

## Channel identity correction

- Manual/static channels remain numbered sequentially in their exact saved order.
- Generated sports channels retain unique synthetic `tvg-id` values.
- When the configured sports start falls inside the manual channel range, the entire sports block map moves upward automatically by one or more 1,000-channel blocks.
- The configured offset is preserved; for example, a start of 1000 with 1,021 manual channels becomes an effective sports start of 2000.
- The UI reports the automatic shift instead of telling the user that overlapping numbers are merely a warning.
- The combined XMLTV guide remains the selected primary-provider guide plus generated sports entries.

This prevents Jellyfin from treating a manual channel and a generated event feed as the same numbered tuner channel. Provider loading, Xtream live-only imports, and fallback-provider behavior are unchanged from debug3.
