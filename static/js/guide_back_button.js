(() => {
  "use strict";

  const button = document.getElementById("guideCloseBtn");
  if (!button) return;

  button.textContent = "Back";
  button.title = "Return to the previous app page";

  button.addEventListener("click", event => {
    event.preventDefault();
    event.stopImmediatePropagation();

    let sameOriginReferrer = false;
    try {
      sameOriginReferrer = Boolean(document.referrer)
        && new URL(document.referrer).origin === window.location.origin;
    } catch (_) {
      sameOriginReferrer = false;
    }

    if (sameOriginReferrer && window.history.length > 1) {
      window.history.back();
      return;
    }

    window.location.assign("/");
  }, true);
})();
