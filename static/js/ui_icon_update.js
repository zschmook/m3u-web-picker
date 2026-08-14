(() => {
  "use strict";

  const CHECKBOX_SELECTOR = "[data-icon-update-checkbox]";
  const POLL_MS = 500;
  let pollTimer = null;
  let sawActiveRun = false;
  let lastCompletionSignature = "";
  let logosEnabled = false;

  const nativeFetch = window.fetch.bind(window);

  function masterButton() {
    return document.getElementById("masterUpdateNowBtn");
  }

  function logoCheckboxes() {
    return [...document.querySelectorAll(CHECKBOX_SELECTOR)];
  }

  function statusElements() {
    return [
      document.getElementById("masterIconUpdateStatus"),
      document.getElementById("uiIconUpdateStatus"),
    ].filter(Boolean);
  }

  function syncCheckboxes(source = null) {
    if (source) logosEnabled = Boolean(source.checked);
    for (const checkbox of logoCheckboxes()) checkbox.checked = logosEnabled;
  }

  function buildCheckbox(id, extraClass = "") {
    const wrapper = document.createElement("div");
    wrapper.className = `form-check mb-0 d-flex align-items-center gap-1 ${extraClass}`.trim();
    wrapper.title = "Temporary: eagerly cache every known provider/sports icon after a manual Update Now. No extra sports API requests.";
    wrapper.innerHTML = `
      <input id="${id}" data-icon-update-checkbox class="form-check-input mt-0" type="checkbox">
      <label class="form-check-label small" for="${id}">Logos?</label>
    `;
    const checkbox = wrapper.querySelector("input");
    checkbox.checked = logosEnabled;
    checkbox.addEventListener("change", () => syncCheckboxes(checkbox));
    return wrapper;
  }

  function ensureOverviewControls() {
    const button = masterButton();
    if (!button) return;
    const controls = button.closest(".master-update-controls") || button.parentElement;
    if (!controls) return;

    if (!document.getElementById("masterUpdateLogosOverview")) {
      controls.insertBefore(buildCheckbox("masterUpdateLogosOverview"), button);
    }

    const masterStatus = document.getElementById("masterUpdateStatus");
    if (masterStatus && !document.getElementById("masterIconUpdateStatus")) {
      const status = document.createElement("div");
      status.id = "masterIconUpdateStatus";
      status.className = "master-update-status small-muted mt-1 d-none";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      masterStatus.insertAdjacentElement("afterend", status);
    }

    if (button.dataset.iconUpdateBound !== "true") {
      button.dataset.iconUpdateBound = "true";
      button.addEventListener("click", () => {
        if (!logosEnabled) {
          stopPolling();
          hideStatus();
          return;
        }
        sawActiveRun = false;
        showQueued();
        startPolling();
      }, true);
    }
  }

  function ensureSidebarControls() {
    const visibleButton = document.getElementById("uiUpdateNowBtn");
    const actions = visibleButton?.closest(".ui-system-actions");
    if (!visibleButton || !actions) return;

    if (!document.getElementById("masterUpdateLogosSidebar")) {
      const option = buildCheckbox("masterUpdateLogosSidebar", "ui-icon-update-option");
      option.style.marginTop = "10px";
      option.style.color = "var(--ui-text-muted, #9ca3af)";
      actions.insertAdjacentElement("beforebegin", option);
    }

    if (!document.getElementById("uiIconUpdateStatus")) {
      const status = document.createElement("div");
      status.id = "uiIconUpdateStatus";
      status.className = "small-muted mt-2 d-none";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      actions.insertAdjacentElement("afterend", status);
    }
  }

  function installControls() {
    ensureOverviewControls();
    ensureSidebarControls();
    syncCheckboxes();
  }

  function setStatusText(text, tone = "") {
    for (const status of statusElements()) {
      status.classList.remove("d-none", "text-danger", "text-warning", "text-success");
      if (tone) status.classList.add(tone);
      status.textContent = text;
    }
  }

  function showQueued() {
    setStatusText("Icon update • queued with manual update");
  }

  function hideStatus() {
    for (const status of statusElements()) status.classList.add("d-none");
  }

  function formatCount(value) {
    return Number(value || 0).toLocaleString();
  }

  function completionSignature(state) {
    return [
      state.finished_at || "",
      state.status || "",
      state.processed || 0,
      state.downloaded || 0,
      state.cached || 0,
      state.failed || 0,
    ].join("|");
  }

  function summaryFor(state) {
    return `${formatCount(state.processed)} checked • ${formatCount(state.downloaded)} downloaded • ${formatCount(state.cached)} cached • ${formatCount(state.failed)} failed`;
  }

  function renderIconUpdate(state) {
    if (!state || !state.requested) return;

    const phase = String(state.status || "idle").toLowerCase();
    if (["waiting", "running"].includes(phase)) sawActiveRun = true;

    if (phase === "waiting") {
      setStatusText(`Icon update • waiting for provider/sports refresh${state.detail ? ` • ${state.detail}` : ""}`);
      return;
    }

    if (phase === "running") {
      setStatusText(`Icon update • ${formatCount(state.processed)}/${formatCount(state.total)} • ${formatCount(state.downloaded)} downloaded • ${formatCount(state.cached)} cached • ${formatCount(state.failed)} failed`);
      return;
    }

    if (phase === "complete") {
      const tone = Number(state.failed || 0) ? "text-warning" : "text-success";
      setStatusText(`Icon update complete • ${summaryFor(state)}`, tone);
      const signature = completionSignature(state);
      if (sawActiveRun && signature && signature !== lastCompletionSignature) {
        lastCompletionSignature = signature;
        showIconToast("Icon update", summaryFor(state));
      }
      stopPolling();
      return;
    }

    if (phase === "failed" || phase === "skipped") {
      setStatusText(
        `Icon update ${phase} • ${state.detail || "No icons were cached."}`,
        phase === "failed" ? "text-danger" : "text-warning",
      );
      const signature = completionSignature(state);
      if (sawActiveRun && signature && signature !== lastCompletionSignature) {
        lastCompletionSignature = signature;
        showIconToast("Icon update", state.detail || `Icon update ${phase}.`);
      }
      stopPolling();
    }
  }

  function showIconToast(title, message) {
    let container = document.getElementById("iconUpdateToastContainer");
    if (!container) {
      container = document.createElement("div");
      container.id = "iconUpdateToastContainer";
      container.className = "toast-container position-fixed bottom-0 end-0 p-3";
      container.style.zIndex = "1100";
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = "toast text-bg-dark border-secondary";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.setAttribute("aria-atomic", "true");
    toast.innerHTML = `
      <div class="toast-header">
        <strong class="me-auto">${escapeHtml(title)}</strong>
        <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
      <div class="toast-body">${escapeHtml(message)}</div>
    `;
    container.appendChild(toast);

    if (window.bootstrap?.Toast) {
      const instance = new bootstrap.Toast(toast, {delay: 7000});
      toast.addEventListener("hidden.bs.toast", () => toast.remove(), {once: true});
      instance.show();
    } else {
      window.setTimeout(() => toast.remove(), 7000);
    }
  }

  async function pollIconUpdate() {
    try {
      const response = await nativeFetch(`/api/master-update?_=${Date.now()}`, {cache: "no-store"});
      if (!response.ok) return;
      const data = await response.json();
      renderIconUpdate(data.icon_update || {});
    } catch {
      // The next poll can recover; keep the current inline status visible.
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(pollIconUpdate, POLL_MS);
    window.setTimeout(pollIconUpdate, 300);
  }

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  window.fetch = function(input, init = {}) {
    let url = "";
    try {
      url = typeof input === "string" ? input : input?.url || "";
      const parsed = new URL(url, window.location.href);
      const method = String(init?.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
      if (parsed.pathname === "/api/master-update/run" && method === "POST") {
        const headers = new Headers(init.headers || {});
        headers.set("Content-Type", "application/json");
        let body = {};
        if (typeof init.body === "string" && init.body.trim()) {
          try { body = JSON.parse(init.body); } catch { body = {}; }
        }
        body.logos = Boolean(logosEnabled);
        init = {...init, headers, body: JSON.stringify(body)};
      }
    } catch {
      // Fall through to the original fetch unchanged.
    }
    return nativeFetch(input, init);
  };

  installControls();
  new MutationObserver(() => installControls()).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
