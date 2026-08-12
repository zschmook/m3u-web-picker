(() => {
  "use strict";

  const toolbar = document.querySelector(".playlist-toolbar");
  const topStatus = document.getElementById("uiStatusStrip");
  const brand = document.querySelector(".app-brand-block");
  if ((!toolbar && !brand) || document.getElementById("hdhrSupportEnabled")) return;

  const panel = document.createElement("div");
  panel.className = "hdhr-support-panel ui-top-hdhr-panel mb-0";
  panel.innerHTML = `
    <div class="d-flex align-items-center justify-content-start gap-3 flex-wrap">
      <div class="form-check form-switch mb-0">
        <input id="hdhrSupportEnabled" class="form-check-input" type="checkbox" role="switch">
        <label class="form-check-label" for="hdhrSupportEnabled">Enable HDHR Support</label>
      </div>
      <span id="hdhrSupportStatus" class="small-muted" role="status" aria-live="polite">Loading…</span>
    </div>`;

  // In experients-ui the HDHR control belongs with the app-wide health/status
  // controls, not down inside the Outputs panel. Fall back to the legacy
  // toolbar location if the refactor shell is not present.
  if (topStatus?.parentNode) {
    topStatus.insertAdjacentElement("afterend", panel);
    document.querySelector(".ui-hdhr-mirror")?.classList.add("d-none");
  } else if (brand) {
    brand.appendChild(panel);
  } else {
    const masterPanel = toolbar.querySelector(".master-update-panel");
    toolbar.insertBefore(panel, masterPanel || null);
  }

  const toggle = document.getElementById("hdhrSupportEnabled");
  const status = document.getElementById("hdhrSupportStatus");
  let busy = false;

  function render(data) {
    const enabled = Boolean(data?.enabled);
    toggle.checked = enabled;
    toggle.disabled = busy;
    status.className = enabled ? "small text-success" : "small-muted";
    status.textContent = enabled
      ? `Enabled · ${data?.tuner_count || 0} tuners · lineup tagged ${data?.guide_name_suffix || "[HDHR]"}`
      : "Disabled · HDHomeRun HTTP and discovery are suppressed";
  }

  async function load() {
    try {
      const response = await fetch(`/api/hdhr/status?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "Could not load HDHR state.");
      render(data);
    } catch (error) {
      toggle.disabled = true;
      status.className = "small text-danger";
      status.textContent = `HDHR state unavailable: ${error?.message || error}`;
    }
  }

  toggle.addEventListener("change", async () => {
    if (busy) return;
    const requested = toggle.checked;
    busy = true;
    toggle.disabled = true;
    status.className = "small-muted";
    status.textContent = requested ? "Enabling HDHomeRun support…" : "Disabling HDHomeRun support…";
    try {
      const response = await fetch("/api/hdhr/settings", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled: requested})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "Could not update HDHR state.");
      render(data);
    } catch (error) {
      toggle.checked = !requested;
      status.className = "small text-danger";
      status.textContent = `HDHR update failed: ${error?.message || error}`;
    } finally {
      busy = false;
      toggle.disabled = false;
    }
  });

  load();
})();
