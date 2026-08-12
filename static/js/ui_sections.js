(() => {
  "use strict";

  function installChannelSectionShell() {
    const header = document.getElementById("channelManagerHeader");
    const body = document.getElementById("channelManagerBody");
    const button = document.getElementById("channelManagerCollapseBtn");
    if (!header || !body || !button || document.getElementById("uiChannelSectionShell")) return;

    const shell = document.createElement("section");
    shell.id = "uiChannelSectionShell";
    shell.className = "ui-channel-section-shell";
    header.parentNode.insertBefore(shell, header);
    shell.append(header, body);

    header.classList.add("ui-channel-section-header");
    body.classList.add("ui-channel-section-body");
    button.setAttribute("aria-controls", "channelManagerBody");

    const manage = document.getElementById("manageOrderBtn");
    if (manage) {
      manage.classList.remove("btn-outline-secondary");
      manage.classList.add("btn-outline-light");
    }

    const sync = () => {
      const collapsed = body.classList.contains("d-none");
      shell.classList.toggle("is-collapsed", collapsed);
      button.textContent = collapsed ? "Show channels" : "Hide channels";
      button.setAttribute("aria-expanded", String(!collapsed));
    };

    new MutationObserver(sync).observe(body, {
      attributes: true,
      attributeFilter: ["class"]
    });
    sync();
  }

  function installSportsSectionBoundary() {
    const sports = document.getElementById("sportsSectionTitle")?.closest(".sports-card");
    if (!sports || sports.dataset.uiSectionBoundary === "true") return;
    sports.dataset.uiSectionBoundary = "true";
    sports.classList.add("ui-independent-sports-section");

    const marker = document.createElement("div");
    marker.className = "ui-section-separator";
    marker.setAttribute("aria-hidden", "true");
    marker.innerHTML = "<span></span>";
    sports.parentNode.insertBefore(marker, sports);
  }

  function install() {
    installChannelSectionShell();
    installSportsSectionBoundary();
  }

  install();
})();
