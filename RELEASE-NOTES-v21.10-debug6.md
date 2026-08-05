# M3U Web Picker Sports v21.10-debug6

This debug release narrows Sports Automation placeholder filtering before the hourly scheduling work is added.

## Off-air placeholder cleanup

- Sports Automation now rejects clear provider filler titles matching `No Event Today` / `No Events Today`.
- Sports Automation now rejects `Signing Off` titles.
- Matching is case-insensitive and tolerates punctuation, separators, and minor spacing variations.
- Filtering is deliberately narrow: legitimate programming such as `Golf Channel Podcast With Rex & Lav`, studio shows, and other real programs are not broadly suppressed.

All provider handling, manual/generated playback separation, combined XMLTV behavior, channel numbering, and the 90-minute postgame grace period are unchanged from debug5.

The proposed `Every X hours` sports schedule is intentionally not included in this build; it remains queued after placeholder behavior is verified.
