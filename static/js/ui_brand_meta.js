(() => {
  "use strict";

  const meta = document.querySelector(".app-brand-meta");
  if (!meta) return;

  const subtitle = meta.querySelector("span.small-muted");
  if (subtitle) subtitle.textContent = subtitle.textContent.replace(/\.\s*$/, "");

  [...meta.querySelectorAll("a")].forEach(link => {
    try {
      if (new URL(link.href, location.href).pathname === "/user-guide") {
        link.textContent = "User Guide";
      }
    } catch {}
  });

  if (meta.querySelector(".ui-brand-separator")) return;

  [...meta.querySelectorAll("a")].forEach(link => {
    const separator = document.createElement("span");
    separator.className = "small-muted ui-brand-separator";
    separator.setAttribute("aria-hidden", "true");
    separator.textContent = "-";
    meta.insertBefore(separator, link);
  });
})();
