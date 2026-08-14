(() => {
  "use strict";

  const SELECTOR = "img.guide-logo, img.guide-matchup-logo, img.sports-selection-logo";
  const LEAGUE_PREFIX = /^(NFL|MLB|NHL|NBA|WNBA|NCAAF|NCAA|MLS)\b/i;

  function fallbackLabelFor(img) {
    const explicit = String(img.dataset.logoFallback || "").trim();
    if (explicit) return explicit.slice(0, 4).toUpperCase();
    const sportsName = img.closest(".sports-selection-result")?.querySelector(".sports-selection-copy strong")?.textContent;
    const stationName = img.closest(".guide-station-cell")?.querySelector(".guide-station-name")?.textContent;
    const legacyGuideName = img.closest(".guide-channel-main")?.querySelector(".guide-channel-name")?.textContent;
    const text = String(sportsName || stationName || legacyGuideName || "").trim();
    const league = text.match(LEAGUE_PREFIX);
    if (league) return league[1].toUpperCase();
    const match = text.match(/[A-Za-z0-9]/);
    return (match?.[0] || "TV").toUpperCase();
  }

  function replaceWithFallback(img) {
    if (!img?.isConnected) return;
    const fallback = document.createElement("span");
    const sports = img.classList.contains("sports-selection-logo");
    const matchup = img.classList.contains("guide-matchup-logo");
    fallback.className = sports
      ? "sports-selection-logo sports-selection-logo-fallback ui-logo-fallback"
      : matchup
        ? "guide-matchup-logo ui-guide-logo-fallback ui-logo-fallback"
        : "guide-logo ui-guide-logo-fallback ui-logo-fallback";
    fallback.setAttribute("aria-hidden", "true");
    fallback.textContent = fallbackLabelFor(img);
    img.replaceWith(fallback);
  }

  function absoluteRemoteUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
      const parsed = new URL(raw, window.location.href);
      if (!/^https?:$/.test(parsed.protocol)) return "";
      if (parsed.origin === window.location.origin) return "";
      return parsed.href;
    } catch {
      return "";
    }
  }

  function isLocalRegistryUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) return false;
    try {
      const parsed = new URL(raw, window.location.href);
      return parsed.origin === window.location.origin && parsed.pathname === "/api/logo" && parsed.searchParams.has("key");
    } catch {
      return false;
    }
  }

  function install(img) {
    if (!(img instanceof HTMLImageElement) || img.dataset.uiLogoLoader === "true") return;

    const rawSource = String(img.getAttribute("src") || "").trim();
    const original = absoluteRemoteUrl(rawSource);
    const registrySource = isLocalRegistryUrl(rawSource);
    if (!original && !registrySource) return;

    img.dataset.uiLogoLoader = "true";
    img.referrerPolicy = "no-referrer";
    img.decoding = "async";

    if (registrySource) {
      img.addEventListener("error", () => replaceWithFallback(img), {once: true});
      return;
    }

    img.dataset.uiLogoOriginal = original;
    const identityKey = String(img.dataset.logoKey || "").trim();
    const sourceKind = String(img.dataset.logoSource || "").trim();
    let triedDirect = false;
    img.addEventListener("error", () => {
      if (!triedDirect && img.isConnected) {
        triedDirect = true;
        img.src = original;
        return;
      }
      replaceWithFallback(img);
    });

    const params = new URLSearchParams({url: original});
    if (identityKey) params.set("key", identityKey);
    if (sourceKind) params.set("source", sourceKind);
    img.src = `/api/logo?${params.toString()}`;
  }

  function scan(root = document) {
    if (root instanceof HTMLImageElement && root.matches(SELECTOR)) install(root);
    root.querySelectorAll?.(SELECTOR).forEach(install);
  }

  scan();
  new MutationObserver(mutations => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach(node => {
        if (node instanceof Element) scan(node);
      });
    }
  }).observe(document.documentElement, {childList: true, subtree: true});
})();
