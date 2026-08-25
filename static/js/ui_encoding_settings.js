(() => {
  "use strict";
  const el = id => document.getElementById(id);
  let currentSettings = {};
  let currentCapability = {};
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function busy(value) { ["uiEncodingTest", "uiEncodingSave"].forEach(id => { if (el(id)) el(id).disabled = value; }); }
  function status(message, kind = "") { const node = el("uiEncodingStatus"); if (node) { node.textContent = message; node.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`; } }
  function updateCommercialLock() {
    const enabled = Boolean(el("uiEncodingEnabled")?.checked);
    const acknowledged = Boolean(el("uiEncodingAcknowledge")?.checked);
    const encoder = el("uiEncodingEncoder")?.value || "auto";
    const checked = Boolean(currentCapability.ok);
    const hardware = checked && Boolean(currentCapability.hardware_available);
    const explicitCpu = checked
      && currentCapability.active_encoder === "libx264"
      && encoder === "libx264";
    const available = enabled && acknowledged && (hardware || explicitCpu);
    const toggle = el("uiCommercialDetectionEnabled");
    if (toggle) {
      toggle.disabled = !available;
      if (!available) toggle.checked = false;
    }
    const lock = el("uiCommercialDetectionLock");
    if (lock) {
      lock.textContent = !enabled
        ? "Locked until FFmpeg encoding is enabled."
        : !acknowledged
          ? "Locked until the performance warning is acknowledged."
          : !checked
            ? "Locked until the hardware check passes."
            : hardware
              ? "Available: a hardware encoder passed its functional test."
              : explicitCpu
                ? "Available: CPU (libx264) was selected explicitly."
                : "Locked: no hardware encoder passed. Select CPU (libx264) explicitly to continue.";
    }
  }
  function render(data) {
    const settings = data.settings || {};
    currentSettings = {...settings};
    const test = data.capability || {};
    currentCapability = {...test};
    el("uiEncodingEnabled").checked = Boolean(settings.enabled);
    el("uiEncodingAcknowledge").checked = Boolean(settings.warning_acknowledged);
    el("uiEncodingEncoder").value = settings.encoder || "auto";
    el("uiEncodingMaxSessions").value = settings.max_sessions || 2;
    el("uiCommercialDetectionEnabled").checked = Boolean(settings.commercial_detection_enabled);
    el("uiEncodingBadge").textContent = settings.enabled ? "Enabled" : "Disabled";
    el("uiEncodingBadge").classList.toggle("is-enabled", Boolean(settings.enabled));
    updateCommercialLock();
    if (!test.tested_at) return;
    const version = test.ffmpeg_version || "FFmpeg unavailable";
    const encoder = test.active_encoder || "none";
    el("uiEncodingRuntime").innerHTML = `<strong>${version}</strong><br>Functional encoder: <code>${encoder}</code> · Test: ${test.ok ? "passed" : "failed"}`;
    el("uiEncodingWarning").textContent = test.hardware_available
      ? `Hardware-accelerated encoding passed using ${encoder}. Multiple simultaneous streams may still exceed this system's encoding capacity.`
      : "GPU acceleration was not detected or failed its test. FFmpeg will use CPU encoding. This may cause buffering or playback failures, especially with multiple clients.";
  }
  async function load() { if (!el("uiEncodingSave")) return; busy(true); try { render(await api("/api/media-pipeline")); } catch (error) { status(error.message, "error"); } finally { busy(false); } }
  async function test() { busy(true); status("Running a functional FFmpeg encoding test…"); try { const data = await api("/api/media-pipeline/test", {method: "POST"}); render(data); status(data.capability?.ok ? "Encoding test passed." : "Encoding test failed.", data.capability?.ok ? "success" : "error"); } catch (error) { status(error.message, "error"); } finally { busy(false); } }
  async function save() {
    const detectionEnabled = el("uiCommercialDetectionEnabled").checked;
    const detectionChanged = detectionEnabled !== Boolean(currentSettings.commercial_detection_enabled);
    busy(true);
    status("Saving encoding settings…");
    try {
      const data = await api("/api/media-pipeline", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          enabled: el("uiEncodingEnabled").checked,
          warning_acknowledged: el("uiEncodingAcknowledge").checked,
          encoder: el("uiEncodingEncoder").value,
          max_sessions: Number(el("uiEncodingMaxSessions").value),
          commercial_detection_enabled: detectionEnabled,
        }),
      });
      render(data);
      const filteringFailures = (data.filtering_update?.results || []).filter(result => !result.ok);
      status(
        filteringFailures.length
          ? `Setting saved, but ${filteringFailures.length} live stream${filteringFailures.length === 1 ? "" : "s"} could not be updated immediately. Try saving again.`
          : detectionChanged
          ? `Settings saved. Automatic commercial filtering is ${detectionEnabled ? "on" : "off"}; channel analysis and learning remain active.`
          : data.settings?.enabled
            ? "FFmpeg encoding settings saved."
            : "FFmpeg encoding disabled; commercial analysis is unavailable.",
        filteringFailures.length ? "error" : "success",
      );
    } catch (error) {
      status(error.message, "error");
    } finally {
      busy(false);
    }
  }
  el("uiEncodingEnabled")?.addEventListener("change", () => {
    updateCommercialLock();
  });
  el("uiEncodingAcknowledge")?.addEventListener("change", updateCommercialLock);
  el("uiEncodingEncoder")?.addEventListener("change", updateCommercialLock);
  el("uiEncodingTest")?.addEventListener("click", test);
  el("uiEncodingSave")?.addEventListener("click", save);
  void load();
})();
