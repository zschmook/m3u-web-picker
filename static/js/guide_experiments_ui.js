(() => {
  "use strict";

  if (typeof updateCastStatus !== "function") return;
  const baseUpdateCastStatus = updateCastStatus;

  updateCastStatus = function(message = "") {
    let nextMessage = message;
    if (!nextMessage && !window.isSecureContext) {
      nextMessage = "Cast sender needs a secure origin. Open this guide at http://localhost:10000/guide; receiver media still comes from the LAN relay.";
    }
    return baseUpdateCastStatus(nextMessage);
  };

  // Re-render once so a guide originally opened through the LAN does not retain
  // the stale exp-era :1000 recovery hint produced before this override loaded.
  updateCastStatus();
})();
