(() => {
  "use strict";

  const meta = document.querySelector(".app-brand-meta");
  if (!meta) return;

  const subtitle = meta.querySelector("span.small-muted");
  if (subtitle) subtitle.textContent = subtitle.textContent.replace(/\.\s*$/, "");

  if (meta.querySelector(".ui-brand-separator")) return;

  [...meta.querySelectorAll("a")].forEach(link => {
    const separator = document.createElement("span");
    separator.className = "small-muted ui-brand-separator";
    separator.setAttribute("aria-hidden", "true");
    separator.textContent = "-";
    meta.insertBefore(separator, link);
  });
})();
