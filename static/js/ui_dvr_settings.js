(() => {
  "use strict";

  const el = id => document.getElementById(id);
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
    const target = el("uiDvrStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`;
  }

  function setBusy(value) {
    busy = Boolean(value);
    for (const id of ["uiDvrValidate", "uiDvrSave"]) {
      if (el(id)) el(id).disabled = busy;
    }
  }

  function render(data) {
    const current = data?.settings || {};
    const storage = data?.storage || {};
    el("uiDvrEnabled").checked = Boolean(current.enabled);
    el("uiDvrPath").value = current.host_path || storage.configured_host_path || "";
    el("uiDvrPlexPath").value = current.plex_path || "";
    el("uiDvrHevc").checked = Boolean(current.transcode_hevc);
    el("uiDvrRemoveCommercials").checked = Boolean(current.remove_commercials);
    el("uiDvrRemoveCommercials").disabled = !current.transcode_hevc;
    el("uiDvrPaddingBefore").value = Math.round(Number(current.padding_before_seconds || 0) / 60);
    el("uiDvrPaddingAfter").value = Math.round(Number(current.padding_after_seconds || 0) / 60);
    el("uiDvrMaxConcurrent").value = Number(current.max_concurrent_recordings || 2);
    const ready = Boolean(storage.mount_configured && storage.available && storage.writable);
    el("uiDvrBadge").textContent = current.enabled && ready ? "Enabled" : current.enabled ? "Unavailable" : "Disabled";
    el("uiDvrBadge").classList.toggle("is-enabled", Boolean(current.enabled && ready));
    const configured = storage.configured_host_path
      ? `Mounted host folder: ${storage.configured_host_path}.`
      : "No host recording folder is mounted.";
    const capacity = Number(storage.free_bytes || 0);
    const free = capacity > 0 ? ` ${(capacity / 1073741824).toFixed(1)} GB free.` : "";
    el("uiDvrRuntime").textContent = `${configured}${ready ? " The mount is writable." : ""}${free}`;
  }

  async function load() {
    if (!el("uiDvrSave")) return;
    setBusy(true);
    try {
      render(await api("/api/dvr"));
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    const hostPath = el("uiDvrPath").value.trim();
    if (!hostPath) {
      setStatus("Enter the local recording folder.", "error");
      return;
    }
    setBusy(true);
    setStatus("Validating the mounted recording folder…");
    try {
      const data = await api("/api/dvr/storage/validate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({host_path: hostPath}),
      });
      setStatus(data.message || "DVR folder is ready.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const values = {
      enabled: el("uiDvrEnabled").checked,
      host_path: el("uiDvrPath").value.trim(),
      plex_path: el("uiDvrPlexPath").value.trim(),
      transcode_hevc: el("uiDvrHevc").checked,
      remove_commercials: el("uiDvrRemoveCommercials").checked,
      padding_before_seconds: Math.max(0, Number(el("uiDvrPaddingBefore").value || 0)) * 60,
      padding_after_seconds: Math.max(0, Number(el("uiDvrPaddingAfter").value || 0)) * 60,
      max_concurrent_recordings: Number(el("uiDvrMaxConcurrent").value || 2),
    };
    setBusy(true);
    setStatus("Saving DVR settings…");
    try {
      const data = await api("/api/dvr/settings", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(values),
      });
      render(data);
      setStatus(values.enabled ? "In-app DVR enabled." : "DVR settings saved; recording is disabled.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  el("uiDvrValidate")?.addEventListener("click", validate);
  el("uiDvrSave")?.addEventListener("click", save);
  el("uiDvrHevc")?.addEventListener("change", () => {
    el("uiDvrRemoveCommercials").disabled = !el("uiDvrHevc").checked;
  });
  document.querySelector('[data-settings-panel="dvr"]')?.addEventListener("click", load);
  window.addEventListener("pageshow", load);
  void load();
})();
