(() => {
  "use strict";

  const details = document.getElementById("publicEpgDetails");
  if (details) {
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
  }

  let advertisedOriginPromise = null;

  function advertisedOrigin() {
    if (!advertisedOriginPromise) {
      advertisedOriginPromise = fetch("/api/guide/config", {cache: "no-store"})
        .then(response => response.ok ? response.json() : {})
        .then(data => String(data?.media_origin || "").replace(/\/+$/, "") || location.origin)
        .catch(() => location.origin);
    }
    return advertisedOriginPromise;
  }

  function currentPath(input, fallback) {
    const value = String(input?.value || "").trim();
    if (!value) return fallback;
    try {
      const parsed = new URL(value, location.origin);
      return `${parsed.pathname}${parsed.search}${parsed.hash}` || fallback;
    } catch {
      return fallback;
    }
  }

  function applyAdvertisedOutputUrls(origin) {
    const base = String(origin || location.origin).replace(/\/+$/, "");

    const modernM3u = document.getElementById("uiM3uOutputUrl");
    if (modernM3u) modernM3u.value = `${base}/playlist/channels.m3u`;

    const directM3u = document.getElementById("uiDirectM3uOutputUrl");
    if (directM3u) directM3u.value = `${base}/playlist/channels.direct.m3u`;

    const modernEpg = document.getElementById("uiEpgOutputUrl");
    if (modernEpg) modernEpg.value = `${base}/epg/epg.xml`;

    const playlist = document.getElementById("playlistUrl");
    if (playlist) playlist.value = `${base}/playlist/channels.m3u`;

    const epg = document.getElementById("epgOutputUrl");
    if (epg) epg.value = `${base}/epg/epg.xml`;

    const group = document.getElementById("groupPlaylistUrl");
    if (group) group.value = `${base}${currentPath(group, "/playlist/all.m3u")}`;
  }

  function refreshAdvertisedOutputUrls() {
    advertisedOrigin().then(applyAdvertisedOutputUrls);
  }

  // Preload the configured LAN origin so the Outputs modal is already correct
  // when the user opens it from either the sidebar or Overview page.
  refreshAdvertisedOutputUrls();

  document.addEventListener("show.bs.modal", event => {
    if (event.target?.id === "uiOutputsModal") refreshAdvertisedOutputUrls();
  });
})();
