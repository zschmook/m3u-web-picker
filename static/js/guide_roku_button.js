(() => {
  "use strict";

  if (typeof updateRokuControls !== "function" || typeof configuredRokuHost !== "function") return;

  // Multi-Roku owns the actual enabled/disabled state. This late-loaded shim is
  // intentionally presentation-only; the older version forced the button on
  // whenever a host existed, even with no current channel, which created a
  // clickable button whose only feedback landed in hidden Diagnostics.
  const baseUpdateRokuControls = updateRokuControls;
  updateRokuControls = function(message = "") {
    baseUpdateRokuControls(message);

    const button = guideEls?.rokuBtn;
    if (!button) return;

    const host = configuredRokuHost();
    const currentChannel = guideState?.currentChannel;
    const active = Boolean(guideState?.roku?.active);
    const deviceName = guideState?.roku?.deviceName || "Roku";

    if (active) {
      button.title = `Disconnect ${deviceName}`;
    } else if (!host) {
      button.title = "Choose a discovered Roku device in Diagnostics";
    } else if (!currentChannel) {
      button.title = "Play a channel first, then send it to Roku";
    } else {
      button.title = `Play ${currentChannel.name || "current channel"} on ${deviceName}`;
    }
  };

  updateRokuControls();
})();
