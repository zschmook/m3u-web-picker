# Repository instructions

## Restart and deployment approval

- Before restarting, resetting, stopping, or recreating a running service/container, check for active playback or streams on the exact target port when possible.
- Report the target port, whether streams are active (or their status is unknown), and the expected interruption. Ask for explicit approval and wait before performing the restart/reset, even if the port appears idle.
- A request to implement or deploy a change does not by itself authorize an interrupting restart/reset. Approval is specific to the proposed operation; never carry approval from an earlier deployment forward to a new change. If the user already explicitly approved that exact restart/reset, do not ask again.
- Complete preparation and non-interrupting checks before asking. Do not restart any other port or instance.

## README publishing permission

- Changes confined to `README.md` may be committed and pushed to the current branch without requesting additional confirmation.
- This permission does not authorize automatic branch merges or changes to code, configuration, other documentation, or release state.
