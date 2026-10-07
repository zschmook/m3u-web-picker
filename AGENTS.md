# Repository instructions

## Restart and deployment approval

- Before restarting, resetting, stopping, or recreating a running service/container, check for active playback or streams on the exact target port when possible.
- Report the target port, whether streams are active (or their status is unknown), and the expected interruption. Ask for explicit approval and wait before performing the restart/reset, even if the port appears idle.
- A request to implement or deploy a change does not by itself authorize an interrupting restart/reset. Approval is specific to the proposed operation; never carry approval from an earlier deployment forward to a new change. If the user already explicitly approved that exact restart/reset, do not ask again.
- Complete preparation and non-interrupting checks before asking. Do not restart any other port or instance.

## README publishing permission

- Changes confined to `README.md` may be committed and pushed to the current branch without requesting additional confirmation.
- This permission does not authorize automatic branch merges or changes to code, configuration, other documentation, or release state.

## Shared state and browser storage

- SQLite is authoritative for saved channel selections, channel order, and other records already stored in the database. New shared application state should be persisted in SQLite.
- Browser memory contains drafts until the server validates and commits them. Browser storage is for local presentation preferences and temporary playback coordination, not authoritative channel lists or shared settings.
- Identify channel edits with stable channel keys and validate the saved-state revision before committing. Stale tabs must not overwrite newer database records.
- Reading settings, rendering guides, and generating playlists must not save transient browser or server caches back into SQLite.
- Resolve remembered playback and device references against current server records; never resurrect deleted records from a browser snapshot.
