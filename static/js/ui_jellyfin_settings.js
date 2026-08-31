(() => {
  "use strict";

  const el = id => document.getElementById(id);
  let current = null;
  let mountConfigured = false;
  let busy = false;

  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status}).`);
    return data;
  }

  function setStatus(message, kind = "") {
    const target = el("uiJellyfinStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`;
  }

  function syncDependencies() {
    const usingJellyfin = Boolean(el("uiJellyfinUsing")?.checked);
    const acknowledged = Boolean(el("uiJellyfinAcknowledge")?.checked);
    const cleanup = el("uiJellyfinCleanupEnabled");
    if (cleanup && (!usingJellyfin || !acknowledged)) cleanup.checked = false;
    if (cleanup) cleanup.disabled = busy || !mountConfigured || !usingJellyfin || !acknowledged;
    const saveButton = el("uiJellyfinSave");
    if (saveButton) saveButton.disabled = busy || !mountConfigured || !acknowledged;
  }

  function setBusy(value) {
    busy = Boolean(value);
    const validateButton = el("uiJellyfinValidate");
    if (validateButton) validateButton.disabled = busy;
    syncDependencies();
  }

  function render(settings) {
    current = settings || {};
    const runtime = current.runtime || {};
    mountConfigured = Boolean(runtime.mount_configured);
    el("uiJellyfinUsing").checked = mountConfigured && Boolean(current.using_jellyfin);
    el("uiJellyfinUsing").disabled = !mountConfigured;
    el("uiJellyfinUsing").title = mountConfigured
      ? ""
      : "Configure M3U_JELLYFIN_CACHE_DIR and restart the container to use Jellyfin cache cleanup.";
    el("uiJellyfinCachePath").value = current.host_path || runtime.configured_host_path || "";
    el("uiJellyfinAcknowledge").checked = Boolean(current.acknowledged);
    el("uiJellyfinCleanupEnabled").checked = Boolean(
      current.cleanup_enabled && current.acknowledged && current.using_jellyfin
    );
    const mountAvailable = runtime.container_exists && runtime.container_is_dir && runtime.container_writable;
    const cleanupAvailable = Boolean(current.cleanup_enabled && mountConfigured && mountAvailable);
    syncDependencies();
    el("uiJellyfinSettingsBadge").textContent = cleanupAvailable
      ? "Enabled"
      : current.cleanup_enabled ? "Unavailable" : "Disabled";
    el("uiJellyfinSettingsBadge").classList.toggle("is-enabled", cleanupAvailable);
    el("uiJellyfinRuntime").innerHTML = runtime.mount_configured
      ? `<strong>Container mount configured.</strong> ${mountAvailable ? "The mounted directory is available and writable." : "Restart the container after confirming the host path."}`
      : "<strong>Container mount not configured.</strong> Set M3U_JELLYFIN_CACHE_DIR and rebuild the container before enabling cleanup.";
  }

  async function load() {
    if (!el("uiJellyfinSave")) return;
    setBusy(true);
    try {
      const data = await api("/api/jellyfin-cache");
      render(data.jellyfin);
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    const hostPath = el("uiJellyfinCachePath").value.trim();
    if (!hostPath) {
      setStatus("Enter the local Jellyfin cache directory.", "error");
      return;
    }
    setBusy(true);
    setStatus("Validating the mounted cache directory…");
    try {
      const data = await api("/api/jellyfin-cache/validate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({host_path: hostPath}),
      });
      setStatus(data.message || "Jellyfin cache path is valid.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const usingJellyfin = el("uiJellyfinUsing").checked;
    const cleanupEnabled = usingJellyfin && el("uiJellyfinCleanupEnabled").checked;
    const acknowledged = el("uiJellyfinAcknowledge").checked;
    const hostPath = el("uiJellyfinCachePath").value.trim();
    if (!acknowledged) {
      setStatus("Turn on ‘I understand the risks’ before saving Jellyfin cache settings.", "error");
      syncDependencies();
      return;
    }
    if (cleanupEnabled && !acknowledged) {
      setStatus("Turn on ‘I understand the risks’ before enabling cache cleanup.", "error");
      return;
    }
    setBusy(true);
    setStatus("Saving Jellyfin settings…");
    try {
      const data = await api("/api/jellyfin-cache", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          using_jellyfin: usingJellyfin,
          cleanup_enabled: cleanupEnabled,
          acknowledged,
          host_path: hostPath,
        }),
      });
      render(data.jellyfin);
      setStatus(cleanupEnabled ? "Jellyfin cache cleanup enabled." : "Jellyfin settings saved; cache cleanup is disabled.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  el("uiJellyfinValidate")?.addEventListener("click", validate);
  el("uiJellyfinSave")?.addEventListener("click", save);
  el("uiJellyfinUsing")?.addEventListener("change", event => {
    if (!event.target.checked) el("uiJellyfinCleanupEnabled").checked = false;
    syncDependencies();
  });
  el("uiJellyfinAcknowledge")?.addEventListener("change", syncDependencies);
  document.querySelector('[data-settings-panel="jellyfin"]')?.addEventListener("click", load);
  window.addEventListener("pageshow", load);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void load();
  });
  void load();
})();
