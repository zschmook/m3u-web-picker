(() => {
  "use strict";

  const POLL_MS = 1200;
  let polling = false;
  let starting = false;

  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      const error = new Error(data.error || data.message || `Request failed (${response.status}).`);
      error.payload = data;
      throw error;
    }
    return data;
  }

  function gateRequired(payload) {
    const state = payload?.state || {};
    const answers = state.answers || {};
    return Boolean(
      (payload?.enabled ?? payload?.dev_enabled)
      && state.required
      && state.completed
      && answers.initial_refresh_required
      && !answers.initial_refresh_completed_at
    );
  }

  function ensureOverlay() {
    let overlay = document.getElementById("devOnboardingOverlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "devOnboardingOverlay";
      overlay.innerHTML = `
        <div class="dev-onboarding-shell" role="dialog" aria-modal="true" aria-labelledby="devOnboardingTitle">
          <div class="dev-onboarding-header">
            <div>
              <div class="dev-onboarding-kicker">First-run setup</div>
              <div class="dev-onboarding-title" id="devOnboardingTitle">M3U Web Picker</div>
            </div>
            <div class="dev-onboarding-step-count">Finishing setup</div>
          </div>
          <div class="dev-onboarding-body" id="devOnboardingBody"></div>
        </div>`;
      document.body.appendChild(overlay);
    }
    document.documentElement.classList.remove("onboarding-pending", "onboarding-initial-refresh-pending");
    document.documentElement.classList.add("onboarding-active");
    return overlay;
  }

  function renderGate(message = "Starting the first Master Update…", error = "") {
    ensureOverlay();
    const body = document.getElementById("devOnboardingBody");
    if (!body) return;
    body.innerHTML = `
      <h2>Building Your First Guide</h2>
      <div class="dev-onboarding-help">
        M3U Web Picker is running the first automatic update now. It will cache the enabled public EPG data and publish the Combined XMLTV guide before setup opens the main application.
      </div>
      <div class="dev-onboarding-summary">
        <strong>TV Guide is temporarily locked.</strong><br>
        <span class="dev-onboarding-muted">This prevents the first guide view from opening against an empty public-EPG cache.</span>
      </div>
      ${error ? `<div class="dev-onboarding-warning">${escapeHtml(error)}</div>` : ""}
      <div class="dev-onboarding-actions">
        <span></span>
        <div class="dev-onboarding-actions-right">
          ${error ? '<button class="dev-onboarding-btn primary" id="devInitialRefreshRetry" type="button">Retry First Update</button>' : ""}
        </div>
      </div>
      <div class="dev-onboarding-status" id="devOnboardingStatus" role="status" aria-live="polite">${escapeHtml(message)}</div>
    `;
    document.getElementById("devInitialRefreshRetry")?.addEventListener("click", () => {
      void startRefresh(true);
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
  }

  function finishAndReload() {
    document.documentElement.classList.remove(
      "onboarding-active",
      "onboarding-pending",
      "onboarding-initial-refresh-pending",
    );
    document.getElementById("devOnboardingOverlay")?.remove();
    location.reload();
  }

  async function pollUntilReady() {
    if (polling) return;
    polling = true;
    try {
      while (true) {
        const payload = await api("/api/onboarding");
        const state = payload.state || {};
        const answers = state.answers || {};

        if (answers.initial_refresh_completed_at) {
          setStatus("Public EPG cached and Combined guide published.", "success");
          setTimeout(finishAndReload, 250);
          return;
        }

        if (answers.initial_refresh_error && !answers.initial_refresh_in_progress) {
          renderGate("The first guide update needs another try.", answers.initial_refresh_error);
          return;
        }

        if (answers.initial_refresh_in_progress) {
          try {
            const master = await api("/api/master-update");
            const update = master.master_update || master;
            const elapsed = Number(update.elapsed_seconds || 0);
            setStatus(
              elapsed > 0
                ? `Running first Master Update… ${elapsed}s elapsed.`
                : "Running first Master Update… downloading guide data and publishing outputs.",
            );
          } catch {
            setStatus("Running first Master Update… downloading guide data and publishing outputs.");
          }
        } else if (answers.initial_refresh_pending) {
          await startRefresh(false);
        }

        await new Promise(resolve => setTimeout(resolve, POLL_MS));
      }
    } catch (error) {
      renderGate("Could not verify the first guide update.", error.message || "Unknown onboarding refresh error.");
    } finally {
      polling = false;
    }
  }

  async function startRefresh(forceRetry = false) {
    if (starting) return;
    starting = true;
    renderGate(forceRetry ? "Retrying the first Master Update…" : "Starting the first Master Update…");
    try {
      const data = await api("/api/onboarding/initial-refresh", {method: "POST"});
      if (data.ready) {
        finishAndReload();
        return;
      }
      if (data.already_running && !data.in_progress) {
        setStatus("Waiting for the current Master Update to finish before building the first guide…");
      } else {
        setStatus("Running first Master Update… downloading guide data and publishing outputs.");
      }
      setTimeout(() => void pollUntilReady(), 100);
    } catch (error) {
      renderGate("Could not start the first guide update.", error.message || "Unknown onboarding refresh error.");
    } finally {
      starting = false;
    }
  }

  async function start() {
    const serverGate = document.documentElement.classList.contains("onboarding-initial-refresh-pending");
    try {
      const payload = await api("/api/onboarding");
      if (!gateRequired(payload)) {
        document.documentElement.classList.remove("onboarding-initial-refresh-pending");
        return;
      }
      renderGate();
      const answers = payload.state?.answers || {};
      if (answers.initial_refresh_error && !answers.initial_refresh_in_progress) {
        renderGate("The first guide update needs another try.", answers.initial_refresh_error);
        return;
      }
      if (answers.initial_refresh_in_progress) {
        void pollUntilReady();
      } else {
        void startRefresh(false);
      }
    } catch (error) {
      if (serverGate) {
        renderGate("Could not initialize the first guide update.", error.message || "Unknown onboarding refresh error.");
      } else {
        console.error("Could not initialize onboarding guide gate:", error);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    void start();
  }
})();
