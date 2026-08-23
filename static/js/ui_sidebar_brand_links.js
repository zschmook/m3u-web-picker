(() => {
  "use strict";

  function openOverview() {
    const overview = document.querySelector('[data-ui-page-target="overview"]');
    if (overview) overview.click();
    document.body.classList.remove("ui-sidebar-open");
  }

  function install() {
    const brand = document.querySelector(".ui-sidebar-brand");

    if (brand && !brand.dataset.uiHomeBound) {
      brand.dataset.uiHomeBound = "true";
      brand.dataset.uiHomeLink = "true";
      brand.setAttribute("role", "button");
      brand.setAttribute("tabindex", "0");
      brand.setAttribute("aria-label", "Open Overview");
      brand.setAttribute("title", "Overview");
      brand.addEventListener("click", openOverview);
      brand.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openOverview();
      });
    }

    return Boolean(brand);
  }

  if (!install()) {
    const observer = new MutationObserver(() => {
      if (install()) observer.disconnect();
    });
    observer.observe(document.body, {childList: true, subtree: true});
    window.setTimeout(() => observer.disconnect(), 5000);
  }
})();
