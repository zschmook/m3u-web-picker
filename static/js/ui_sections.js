(() => {
  "use strict";

  function installChannelSectionShell() {
    const header = document.getElementById("channelManagerHeader");
    const body = document.getElementById("channelManagerBody");
    if (!header || !body || document.getElementById("uiChannelSectionShell")) return;

    const shell = document.createElement("section");
    shell.id = "uiChannelSectionShell";
    shell.className = "ui-channel-section-shell";
    header.parentNode.insertBefore(shell, header);
    shell.append(header, body);

    header.classList.add("ui-channel-section-header");
    body.classList.add("ui-channel-section-body");

    // Channels is now a dedicated sidebar destination, so collapsing the whole
    // manager only creates a dead-looking page. Always show it and remove the
    // legacy Show/Hide Channels control while keeping Manage Order available.
    body.classList.remove("d-none");
    document.getElementById("channelManagerCollapseBtn")?.remove();

    const manage = document.getElementById("manageOrderBtn");
    if (manage) {
      manage.classList.remove("btn-outline-secondary");
      manage.classList.add("btn-outline-light");
    }
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
