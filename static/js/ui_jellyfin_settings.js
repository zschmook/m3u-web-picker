(() => {
  "use strict";

  const el = id => document.getElementById(id);
  let current = null;

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

  function setBusy(busy) {
    ["uiJellyfinValidate", "uiJellyfinSave"].forEach(id => {
      const button = el(id);
      if (button) button.disabled = busy;
    });
  }

  function render(settings) {
    current = settings || {};
    const runtime = current.runtime || {};
    el("uiJellyfinUsing").checked = Boolean(current.using_jellyfin);
    el("uiJellyfinCachePath").value = current.host_path || runtime.configured_host_path || "";
    el("uiJellyfinAcknowledge").checked = Boolean(current.acknowledged);
    el("uiJellyfinCleanupEnabled").checked = Boolean(current.cleanup_enabled);
    el("uiJellyfinSettingsBadge").textContent = current.cleanup_enabled ? "Enabled" : "Disabled";
    el("uiJellyfinSettingsBadge").classList.toggle("is-enabled", Boolean(current.cleanup_enabled));
    const mountAvailable = runtime.container_exists && runtime.container_is_dir && runtime.container_writable;
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
  });
  void load();
})();
