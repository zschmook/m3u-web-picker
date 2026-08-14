(() => {
  "use strict";

  const CHECKBOX_ID = "masterUpdateLogos";
  const STATUS_ID = "masterIconUpdateStatus";
  const POLL_MS = 500;
  let pollTimer = null;
  let sawActiveRun = false;
  let lastCompletionSignature = "";

  function masterButton() {
    return document.getElementById("masterUpdateNowBtn");
  }

  function logoCheckbox() {
    return document.getElementById(CHECKBOX_ID);
  }

  function statusElement() {
    return document.getElementById(STATUS_ID);
  }

  function installControls() {
    const button = masterButton();
    if (!button || logoCheckbox()) return;

    const wrapper = document.createElement("div");
    wrapper.className = "form-check mb-0 d-flex align-items-center gap-1";
    wrapper.title = "Temporary: eagerly cache every known provider/sports icon after a manual update.";
    wrapper.innerHTML = `
      <input id="${CHECKBOX_ID}" class="form-check-input mt-0" type="checkbox">
      <label class="form-check-label small" for="${CHECKBOX_ID}">Logos?</label>
    `;
    button.parentElement?.insertBefore(wrapper, button);

    const masterStatus = document.getElementById("masterUpdateStatus");
    if (masterStatus && !statusElement()) {
      const status = document.createElement("div");
      status.id = STATUS_ID;
      status.className = "master-update-status small-muted mt-1 d-none";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      masterStatus.insertAdjacentElement("afterend", status);
    }

    button.addEventListener("click", () => {
      if (!logoCheckbox()?.checked) {
        stopPolling();
        hideStatus();
        return;
      }
      sawActiveRun = false;
      showQueued();
      startPolling();
    }, true);
  }

  function showQueued() {
    const status = statusElement();
    if (!status) return;
    status.classList.remove("d-none", "text-danger", "text-warning", "text-success");
    status.textContent = "Icon update • queued with manual update";
  }

  function hideStatus() {
    statusElement()?.classList.add("d-none");
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
    const processed = formatCount(state.processed);
    const downloaded = formatCount(state.downloaded);
    const cached = formatCount(state.cached);
    const failed = formatCount(state.failed);
    return `${processed} checked • ${downloaded} downloaded • ${cached} cached • ${failed} failed`;
  }

  function renderIconUpdate(state) {
    if (!state || !state.requested) return;
    const status = statusElement();
    if (!status) return;

    const phase = String(state.status || "idle").toLowerCase();
    if (["waiting", "running"].includes(phase)) sawActiveRun = true;
    status.classList.remove("d-none", "text-danger", "text-warning", "text-success");

    if (phase === "waiting") {
      status.textContent = `Icon update • waiting for provider/sports refresh${state.detail ? ` • ${state.detail}` : ""}`;
      return;
    }

    if (phase === "running") {
      const processed = formatCount(state.processed);
      const total = formatCount(state.total);
      const downloaded = formatCount(state.downloaded);
      const cached = formatCount(state.cached);
      const failed = formatCount(state.failed);
      status.textContent = `Icon update • ${processed}/${total} • ${downloaded} downloaded • ${cached} cached • ${failed} failed`;
      return;
    }

    if (phase === "complete") {
      status.classList.add(Number(state.failed || 0) ? "text-warning" : "text-success");
      status.textContent = `Icon update complete • ${summaryFor(state)}`;
      const signature = completionSignature(state);
      if (sawActiveRun && signature && signature !== lastCompletionSignature) {
        lastCompletionSignature = signature;
        showIconToast("Icon update", summaryFor(state));
      }
      stopPolling();
      return;
    }

    if (phase === "failed" || phase === "skipped") {
      status.classList.add(phase === "failed" ? "text-danger" : "text-warning");
      status.textContent = `Icon update ${phase} • ${state.detail || "No icons were cached."}`;
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
      // Keep the inline queued/running state; the next poll can recover.
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

  const nativeFetch = window.fetch.bind(window);
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
        body.logos = Boolean(logoCheckbox()?.checked);
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
