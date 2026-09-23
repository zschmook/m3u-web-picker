(() => {
  "use strict";

  const button = document.getElementById("guideCloseBtn");
  if (!button) return;

  button.textContent = "Back";
  button.title = "Return to overview";

  button.addEventListener("click", event => {
    event.preventDefault();
    event.stopImmediatePropagation();

    window.location.assign("/");
  }, true);
})();
