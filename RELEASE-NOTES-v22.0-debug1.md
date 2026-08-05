# M3U Web Picker Sports v22.0-debug1

## Feed policy for overlapping team and league rules

- A broad league, conference, sport, or Everything Mode match now generates one best feed per logical game.
- If either participant has an explicit enabled team rule, that game receives the expanded feed set: home, away, national, and provider event feeds as available.
- The explicit team rule controls feed ranking and preference when it overlaps a league rule.
- Matching the same game through a team and its league does not duplicate the event or allocate another channel block.
- Games involving no explicitly selected team remain compact even if the broad rule's stored preference was previously `all`.

## Primary-provider form lock

- Wraps every add-primary input in one disabled fieldset whenever a URL or file primary exists.
- Locks primary name, provider URL, Xtream username, Xtream password, M3U file chooser, Load Primary, and Use File as Primary together.
- Keeps the individual JavaScript disabled state as a second layer and clears password-manager autofill from locked credential fields.
- Removing the primary unlocks the complete fieldset.

## Xtream account status

- Reads provider-reported `user_info.status` and `user_info.exp_date` from `player_api.php`.
- Displays status and expiration in the existing **Last Updated / Status** column.
- Does not display active/max connection counts.
- Does not expose usernames, passwords, credential-bearing URLs, or raw player API responses.
- Refreshes the informational account metadata during provider refreshes without allowing a metadata failure to block playlist updates.

## Preserved event/channel behavior

- Retains debug10 broadcast-day logical-event grouping and cross-midnight replay suppression.
- Replays disabled: later airings do not allocate new channel blocks.
- Replays enabled: later XMLTV airings attach to the original generated channels as Replay programmes.
- Retains the 90-minute postgame grace period.
- Retains `No Event(s) Today` and `Signing Off` filtering while preserving legitimate programming.
- Retains manual/generated namespace isolation and unique local sports redirect URLs for Jellyfin.
- Retains daily/every-X-hours scheduling and scan-local team/feed/rule indexes.

## Testing workflow

Fresh `debug-data` is the recommended v22 debug workflow. The migration helper is retained only for explicit migration testing.
