(() => {
  "use strict";

  const details = document.getElementById("publicEpgDetails");
  if (!details) return;

  const keepOpen = () => {
    if (!details.open) details.open = true;
  };

  details.open = true;
  details.addEventListener("toggle", keepOpen);

  const summary = details.querySelector(":scope > summary");
  if (summary) {
    summary.setAttribute("aria-disabled", "true");
    summary.addEventListener("click", event => event.preventDefault());
  }
})();
