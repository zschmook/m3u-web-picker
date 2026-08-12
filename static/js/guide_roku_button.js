(() => {
  "use strict";

  if (typeof updateRokuControls !== "function" || typeof configuredRokuHost !== "function") return;

  const baseUpdateRokuControls = updateRokuControls;
  updateRokuControls = function(message = "") {
    baseUpdateRokuControls(message);
    const host = configuredRokuHost();
    if (guideEls?.rokuBtn) {
      guideEls.rokuBtn.disabled = !host;
    }
  };

  updateRokuControls();
})();
