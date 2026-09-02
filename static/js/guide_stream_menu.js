(() => {
  "use strict";

  const menu = document.getElementById("guideStreamMenu");
  if (!menu) return;

  menu.addEventListener("click", event => {
    if (!event.target.closest("#guideRokuBtn, #guideCastBtn")) return;
    window.setTimeout(() => menu.removeAttribute("open"), 0);
  });

  document.addEventListener("click", event => {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") menu.removeAttribute("open");
  });
})();
