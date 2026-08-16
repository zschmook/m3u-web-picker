(() => {
  "use strict";

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || data.message || `Request failed (${response.status}).`);
    }
    return data;
  }

  function wizardBody() {
    return document.getElementById("devOnboardingBody");
  }

  function heading() {
    return wizardBody()?.querySelector("h2")?.textContent?.trim() || "";
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
  }

  async function previousStepForCurrentPage() {
    const current = heading();
    if (current === "Enable Sports Automation?") return 1;
    if (current === "Pick Teams / Leagues") return 2;
    if (current === "Use Sports API Integration?") return 3;
    if (current === "Sports API Information") return 4;
    if (current === "Jellyfin Cache Directory") return 6;

    if (current === "Are You Using Jellyfin?") {
      const payload = await api("/api/onboarding");
      const sportsEnabled = Boolean(payload?.sports?.settings?.enabled);
      if (!sportsEnabled) return 2;
      return payload?.sports?.schedule_api?.enabled ? 5 : 4;
    }

    return null;
  }

  async function goBack() {
    const step = await previousStepForCurrentPage();
    if (!step) return;
    setStatus("Going back…");
    await api("/api/onboarding", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({current_step: step}),
    });
    location.reload();
  }

  async function finishWithoutJellyfin() {
    setStatus("Leaving Jellyfin integration disabled…");
    await api("/api/jellyfin-cache", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        using_jellyfin: false,
        cleanup_enabled: false,
        acknowledged: false,
        host_path: "",
      }),
    });
    await api("/api/onboarding", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        current_step: 7,
        answers: {
          using_jellyfin: false,
          jellyfin_cache_cleanup: false,
        },
      }),
    });
    await api("/api/onboarding/complete", {method: "POST"});
    location.reload();
  }

  function enhanceJellyfinCachePage() {
    if (heading() !== "Jellyfin Cache Directory") return;
    if (document.getElementById("devJellyfinNotHere")) return;

    const primary = document.getElementById("devOnboardingNext");
    const actionsRight = primary?.closest(".dev-onboarding-actions-right");
    if (!primary || !actionsRight) return;

    const skip = document.createElement("button");
    skip.id = "devJellyfinNotHere";
    skip.type = "button";
    skip.className = "dev-onboarding-btn";
    skip.textContent = "I don't use Jellyfin on this computer";
    actionsRight.insertBefore(skip, primary);

    skip.addEventListener("click", async event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      skip.disabled = true;
      primary.disabled = true;
      try {
        await finishWithoutJellyfin();
      } catch (error) {
        skip.disabled = false;
        primary.disabled = false;
        setStatus(error.message, "error");
      }
    });
  }

  document.addEventListener("click", event => {
    const back = event.target.closest("#devOnboardingBack");
    if (!back) return;

    // The base wizard can leave its private busy flag set after a successful
    // choice transition. Capture Back before the base handler so navigation
    // always remains available.
    event.preventDefault();
    event.stopImmediatePropagation();
    back.disabled = true;
    void goBack().catch(error => {
      back.disabled = false;
      setStatus(error.message, "error");
    });
  }, true);

  const observer = new MutationObserver(() => enhanceJellyfinCachePage());
  observer.observe(document.body, {childList: true, subtree: true});
  enhanceJellyfinCachePage();
})();
