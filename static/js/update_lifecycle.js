(() => {
  "use strict";

  const state = {
    running: false,
    starting: false,
    startedAt: null,
    elapsedSeconds: 0,
    pollTimer: null,
    pollInFlight: false,
    initialized: false,
  };

  const el = id => document.getElementById(id);

  function formatElapsed(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function elapsedFromMaster(master = {}) {
    const serverElapsed = Number(master.elapsed_seconds);
    if (Number.isFinite(serverElapsed) && serverElapsed >= 0) return Math.floor(serverElapsed);
    const started = Date.parse(master.started_at || state.startedAt || "");
    if (Number.isFinite(started)) return Math.max(0, Math.floor((Date.now() - started) / 1000));
    return Math.max(0, Math.floor(state.elapsedSeconds || 0));
  }

  function isGuideLink(node) {
    if (!node || node.tagName !== "A") return false;
    try {
      return new URL(node.href, location.href).pathname === "/guide";
    } catch {
      return node.getAttribute("href") === "/guide";
    }
  }

  function setGuideLocked(locked) {
    document.querySelectorAll('a[href]').forEach(link => {
      if (!isGuideLink(link)) return;
      link.classList.toggle("ui-guide-disabled", locked);
      if (locked) {
        if (!link.dataset.updateLifecycleTabindexSaved) {
          link.dataset.updateLifecycleTabindexSaved = "true";
          link.dataset.updateLifecycleTabindex = link.getAttribute("tabindex") ?? "";
        }
        if (!link.dataset.updateLifecycleTitleSaved) {
          link.dataset.updateLifecycleTitleSaved = "true";
          link.dataset.updateLifecycleTitle = link.getAttribute("title") ?? "";
        }
        link.setAttribute("aria-disabled", "true");
        link.setAttribute("tabindex", "-1");
        link.setAttribute("title", "TV Guide is unavailable while the Master Update is running.");
      } else {
        link.removeAttribute("aria-disabled");
        if (link.dataset.updateLifecycleTabindexSaved === "true") {
          const oldTabindex = link.dataset.updateLifecycleTabindex || "";
          if (oldTabindex) link.setAttribute("tabindex", oldTabindex);
          else link.removeAttribute("tabindex");
          delete link.dataset.updateLifecycleTabindexSaved;
          delete link.dataset.updateLifecycleTabindex;
        }
        if (link.dataset.updateLifecycleTitleSaved === "true") {
          const oldTitle = link.dataset.updateLifecycleTitle || "";
          if (oldTitle) link.setAttribute("title", oldTitle);
          else link.removeAttribute("title");
          delete link.dataset.updateLifecycleTitleSaved;
          delete link.dataset.updateLifecycleTitle;
        }
      }
    });
  }

  function setStateClass(node, status) {
    if (!node) return;
    node.classList.remove("is-success", "is-warning", "is-failed", "is-running", "is-setup", "is-loading");
    const mapped = status === "success" ? "is-success"
      : status === "warning" ? "is-warning"
      : status === "failed" || status === "error" ? "is-failed"
      : status === "running" ? "is-running"
      : status === "setup" ? "is-setup" : "is-loading";
    node.classList.add(mapped);
  }

  function renderSidebarRunning(master = {}) {
    const elapsed = formatElapsed(elapsedFromMaster(master));
    const health = el("uiSystemHealth");
    if (health) health.textContent = `Updating · ${elapsed}`;
    setStateClass(el("uiSystemHealthDot"), "running");

    const result = el("uiUpdateResult");
    setStateClass(result, "running");
    const resultText = el("uiUpdateResultText");
    if (resultText) resultText.textContent = `Update in progress · ${elapsed}`;
    el("uiUpdateDetailsBtn")?.classList.add("d-none");

    const updateButton = el("uiUpdateNowBtn");
    if (updateButton) {
      updateButton.disabled = true;
      updateButton.textContent = `Updating · ${elapsed}`;
    }
  }

  function renderSidebarFinal(data = {}) {
    const master = data.master_update || {};
    const update = data.update || {};
    const provider = data.provider || {};
    const status = update.status || (provider.status === "setup" ? "setup" : "success");
    const healthText = provider.status === "setup"
      ? "Setup needed"
      : status === "failed" || status === "error"
        ? "Attention needed"
        : status === "warning"
          ? "Needs review"
          : "Ready";

    const health = el("uiSystemHealth");
    if (health) health.textContent = healthText;
    setStateClass(el("uiSystemHealthDot"), status);

    const result = el("uiUpdateResult");
    setStateClass(result, status);
    const resultText = el("uiUpdateResultText");
    if (resultText) resultText.textContent = update.label || "—";

    const detailsButton = el("uiUpdateDetailsBtn");
    const issueCount = Number(update.error_count || 0) + Number(update.warning_count || 0);
    if (detailsButton) {
      detailsButton.classList.toggle(
        "d-none",
        issueCount === 0 && status !== "failed" && status !== "warning"
      );
    }

    const updateButton = el("uiUpdateNowBtn");
    if (updateButton) {
      updateButton.disabled = Boolean(master.running);
      updateButton.textContent = master.running
        ? `Updating · ${formatElapsed(elapsedFromMaster(master))}`
        : "Update Now";
    }
  }

  function syncLegacyMaster(master = {}) {
    try {
      if (typeof masterUpdateState !== "undefined") {
        masterUpdateState = {...masterUpdateState, ...master};
      }
      // The server/worker is authoritative as soon as it acknowledges the job.
      // Do not let the initiating tab's local busy flag outlive server state.
      if (master.running && typeof masterUpdateBusy !== "undefined") masterUpdateBusy = false;
      if (master.running && typeof masterUpdateLocalStartedAt !== "undefined") masterUpdateLocalStartedAt = 0;
      if (typeof renderMasterUpdate === "function") renderMasterUpdate();
      if (typeof renderSportsScanStatus === "function") renderSportsScanStatus();
    } catch (error) {
      console.debug("Could not mirror live Master Update state into legacy controls:", error);
    }
  }

  async function fetchFinalUiStatus() {
    try {
      const response = await fetch(`/api/ui/status?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "Status request failed.");
      if (data.master_update?.running) {
        renderSidebarRunning(data.master_update);
        return data;
      }
      renderSidebarFinal(data);
      return data;
    } catch (error) {
      const health = el("uiSystemHealth");
      if (health) health.textContent = "Status unavailable";
      setStateClass(el("uiSystemHealthDot"), "failed");
      const result = el("uiUpdateResult");
      setStateClass(result, "failed");
      const resultText = el("uiUpdateResultText");
      if (resultText) resultText.textContent = error?.message || "Could not load status";
      return null;
    }
  }

  async function refreshApplicationData() {
    const jobs = [];
    try {
      if (typeof loadInitialChannels === "function") jobs.push(loadInitialChannels({quiet: true}));
    } catch {}
    try {
      if (typeof loadEpgSources === "function") jobs.push(loadEpgSources());
    } catch {}
    try {
      if (typeof loadSports === "function") jobs.push(loadSports());
    } catch {}
    if (jobs.length) await Promise.allSettled(jobs);
    try {
      if (typeof renderMasterUpdate === "function") renderMasterUpdate();
      if (typeof renderSportsScanStatus === "function") renderSportsScanStatus();
    } catch {}
  }

  async function handleCompletion() {
    try {
      if (typeof masterUpdateBusy !== "undefined") masterUpdateBusy = false;
      if (typeof masterUpdateLocalStartedAt !== "undefined") masterUpdateLocalStartedAt = 0;
    } catch {}
    await fetchFinalUiStatus();
    await refreshApplicationData();
    // Data refreshes can touch legacy status widgets, so finish by reapplying
    // the authoritative report once all post-update UI work has settled.
    await fetchFinalUiStatus();
  }

  function applyMaster(master = {}) {
    const wasRunning = state.running || state.starting;
    const running = Boolean(master.running);
    state.running = running;
    state.starting = false;
    state.startedAt = master.started_at || state.startedAt;
    state.elapsedSeconds = elapsedFromMaster(master);

    syncLegacyMaster(master);
    setGuideLocked(running);
    if (running) renderSidebarRunning(master);

    if (wasRunning && !running) {
      state.startedAt = null;
      state.elapsedSeconds = 0;
      void handleCompletion();
    }
  }

  function schedulePoll(delay) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(() => void pollMaster(), Math.max(100, delay));
  }

  async function pollMaster({reschedule = true} = {}) {
    if (state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const response = await fetch(`/api/master-update?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "Master Update status request failed.");
      applyMaster(data.master_update || {});
      state.initialized = true;
    } catch (error) {
      // Do not unlock guide controls because a poll failed while we already know
      // an update was active. A subsequent successful poll is the authority.
      if (!state.running && !state.starting) setGuideLocked(false);
      console.debug("Master Update status poll failed:", error);
    } finally {
      state.pollInFlight = false;
      if (reschedule) schedulePoll(state.running || state.starting ? 1000 : 5000);
    }
  }

  async function startManualUpdate() {
    if (state.running || state.starting) return;
    state.starting = true;
    state.startedAt = new Date().toISOString();
    state.elapsedSeconds = 0;
    setGuideLocked(true);
    renderSidebarRunning({running: true, started_at: state.startedAt, elapsed_seconds: 0});

    try {
      if (typeof masterUpdateBusy !== "undefined") masterUpdateBusy = true;
      if (typeof masterUpdateLocalStartedAt !== "undefined") masterUpdateLocalStartedAt = Date.now();
      if (typeof renderMasterUpdate === "function") renderMasterUpdate();
    } catch {}

    try {
      const response = await fetch("/api/master-update/run", {
        method: "POST",
        cache: "no-store",
        headers: {"Cache-Control": "no-cache"},
      });
      const data = await response.json();
      if (!response.ok && response.status !== 409) {
        throw new Error(data?.error || "Could not start Master Update.");
      }
      applyMaster(data.master_update || {running: response.ok});
      schedulePoll(250);
    } catch (error) {
      state.starting = false;
      state.running = false;
      setGuideLocked(false);
      try {
        if (typeof masterUpdateBusy !== "undefined") masterUpdateBusy = false;
        if (typeof masterUpdateLocalStartedAt !== "undefined") masterUpdateLocalStartedAt = 0;
        if (typeof renderMasterUpdate === "function") renderMasterUpdate();
        if (typeof setStatus === "function") setStatus(error?.message || "Could not start Master Update.");
      } catch {}
      await fetchFinalUiStatus();
    }
  }

  // Own manual Master Update clicks before the older long-request handlers see
  // them. Both the legacy button and the sidebar button now launch the same
  // asynchronous server worker and then follow live server state.
  document.addEventListener("click", event => {
    const button = event.target.closest("#masterUpdateNowBtn, #uiUpdateNowBtn");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!button.disabled) void startManualUpdate();
  }, true);

  // TV Guide is intentionally unavailable while outputs are being rewritten.
  // Capture-phase blocking also protects links added dynamically after startup.
  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!isGuideLink(link) || !(state.running || state.starting)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  window.addEventListener("pageshow", () => void pollMaster({reschedule: true}));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void pollMaster({reschedule: true});
  });

  // Poll immediately on every fresh/reloaded main UI. This is what reconstructs
  // the current live state after navigation instead of trusting browser memory.
  void pollMaster({reschedule: true});
})();
