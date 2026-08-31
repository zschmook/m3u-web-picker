(() => {
  "use strict";

  if (typeof updateCastStatus !== "function") return;
  const baseUpdateCastStatus = updateCastStatus;
  const castButton = document.getElementById("guideCastBtn");

  function castReceiverAvailable() {
    try {
      if (!window.cast?.framework || !guideState?.cast?.context) return false;
      const state = guideState.cast.context.getCastState?.();
      return Boolean(state) && state !== cast.framework.CastState.NO_DEVICES_AVAILABLE;
    } catch (_) {
      return false;
    }
  }

  function syncCastButtonEligibility() {
    if (!castButton) return;

    const session = typeof currentCastSession === "function" ? currentCastSession() : null;
    if (session) {
      castButton.disabled = false;
      castButton.title = `Disconnect from ${typeof currentCastDeviceName === "function" ? currentCastDeviceName() : "Cast receiver"}`;
      return;
    }

    const hasChannel = Boolean(guideState?.currentChannel);
    const ready = Boolean(guideState?.cast?.apiReady && guideState?.cast?.context);
    const available = castReceiverAvailable();
    const relayReady = typeof castMediaOrigin === "function" ? Boolean(castMediaOrigin()) : true;

    castButton.disabled = !(hasChannel && ready && available && relayReady);

    if (!hasChannel) {
      castButton.title = "Play a channel before casting";
    } else if (!ready) {
      castButton.title = "Google Cast is still loading";
    } else if (!available) {
      castButton.title = "No Google Cast receiver available";
    } else if (!relayReady) {
      castButton.title = "LAN media relay is not configured";
    } else {
      castButton.title = "Cast the current channel";
    }
  }

  updateCastStatus = function(message = "") {
    let nextMessage = message;
    if (!nextMessage && !window.isSecureContext) {
      nextMessage = "Cast sender needs a secure origin. Open this guide at http://localhost:10000/guide; receiver media still comes from the LAN relay.";
    }
    const result = baseUpdateCastStatus(nextMessage);
    syncCastButtonEligibility();
    return result;
  };

  // Keep Cast eligible on exactly the same user flow as Roku: the top button
  // becomes actionable only after a channel is selected, and an active remote
  // session remains actionable so it can be disconnected.
  if (typeof setCurrentChannel === "function") {
    const baseSetCurrentChannel = setCurrentChannel;
    setCurrentChannel = function(channel) {
      const result = baseSetCurrentChannel(channel);
      syncCastButtonEligibility();
      return result;
    };
  }

  document.getElementById("guideStopBtn")?.addEventListener("click", () => {
    window.setTimeout(syncCastButtonEligibility, 0);
  });

  // Cast discovery/session events already call updateCastStatus; this initial
  // pass also removes the old behavior where an idle guide exposed Cast early.
  updateCastStatus();
})();
