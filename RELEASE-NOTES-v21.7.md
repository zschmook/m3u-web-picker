# Sports v21.7 release notes

v21.7 keeps the v21.6 event-lifecycle fixes and cleans up the EPG Manager before wider testing.

## EPG Manager layout

- Replaces the detached built-in guide controls and separate external-source list with one unified table.
- Uses fixed, shared columns for Name, Type, Served URL, Last Updated / Status, and Action.
- Aligns the add-source Name and XMLTV URL inputs with the rows below them.
- Keeps Combined and Sports as non-deletable built-in rows with Copy actions.
- Shows built-in guide file timestamp and size when generated.
- Keeps external provider URLs and credentials hidden after saving.
- Truncates long content instead of allowing it to push columns out of alignment.
- Preserves horizontal alignment on narrow screens through table scrolling.

## Retained from v21.6

- Currently airing games survive scans that begin after kickoff.
- Event eligibility uses interval overlap, XMLTV stop times or sport-specific duration estimates, and a 30-minute grace period.
- Untimed, uncorroborated event slots are not treated as permanently live.
- Same-day doubleheaders and replays remain separate when their start times differ meaningfully.
- Safe manual scan cancellation and filtered combined XMLTV output remain unchanged.
