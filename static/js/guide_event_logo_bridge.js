(() => {
  "use strict";

  const EVENT_LOGO_RE = /\/api\/event-logo\/[0-9a-f]{64}\.png(?:$|[?#])/i;

  function normalizedEventLogoUrl(channel) {
    const value = String(channel?.logo || "").trim();
    return EVENT_LOGO_RE.test(value) ? value : "";
  }

  function applyNormalizedMatchupLogos() {
    if (typeof filteredGuideChannels !== "function") return;
    const channels = filteredGuideChannels();
    const rows = document.querySelectorAll(".guide-timeline-body .guide-grid-row");

    rows.forEach((row, index) => {
      const channel = channels[index];
      const eventLogo = normalizedEventLogoUrl(channel);
      if (!channel?.generated || !eventLogo) return;

      // All generated feeds for a matchup use the same Away @ Home artwork.
      // Older guide rendering may have emitted a single-team image for home
      // or away feeds, so replace either representation with the normalized
      // server-side event composite. Feed identity stays in the subtitle.
      const renderedLogo = row.querySelector(
        ".guide-matchup-logos, .guide-logo, .guide-event-composite-logo"
      );
      if (!renderedLogo) return;

      const image = document.createElement("img");
      image.className = "guide-event-composite-logo";
      image.src = eventLogo;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.dataset.uiLogoLoader = "true";
      image.dataset.eventLogoNormalized = "true";
      renderedLogo.replaceWith(image);
    });
  }

  if (typeof renderGuide === "function") {
    const baseRenderGuide = renderGuide;
    renderGuide = function(...args) {
      const result = baseRenderGuide(...args);
      applyNormalizedMatchupLogos();
      return result;
    };
  }

  applyNormalizedMatchupLogos();
})();
