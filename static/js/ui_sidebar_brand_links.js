(() => {
  "use strict";

  const REPO_URL = "https://github.com/zschmook/m3u-web-picker";
  const externalIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M19 13v6H5V5h6"/></svg>';

  function openOverview() {
    const overview = document.querySelector('[data-ui-page-target="overview"]');
    if (overview) overview.click();
    document.body.classList.remove("ui-sidebar-open");
  }

  function install() {
    const brand = document.querySelector(".ui-sidebar-brand");
    const links = document.querySelector(".ui-side-links");

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

    if (links && !document.getElementById("uiGithubLink")) {
      const github = document.createElement("a");
      github.id = "uiGithubLink";
      github.href = REPO_URL;
      github.target = "_blank";
      github.rel = "noopener noreferrer";
      github.innerHTML = `${externalIcon}<span>GitHub</span>`;
      links.appendChild(github);
    }

    return Boolean(brand && links);
  }

  if (!install()) {
    const observer = new MutationObserver(() => {
      if (install()) observer.disconnect();
    });
    observer.observe(document.body, {childList: true, subtree: true});
    window.setTimeout(() => observer.disconnect(), 5000);
  }
})();
