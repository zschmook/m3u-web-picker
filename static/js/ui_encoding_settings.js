(() => {
  "use strict";
  const el = id => document.getElementById(id);
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function busy(value) { ["uiEncodingTest", "uiEncodingSave"].forEach(id => { if (el(id)) el(id).disabled = value; }); }
  function status(message, kind = "") { const node = el("uiEncodingStatus"); if (node) { node.textContent = message; node.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`; } }
  function syncEnabledState() {
    const enabled = Boolean(el("uiEncodingEnabled")?.checked);
    if (enabled && el("uiEncodingAdvanced")) el("uiEncodingAdvanced").open = true;
  }
  function render(data) {
    const settings = data.settings || {};
    const test = data.capability || {};
    el("uiEncodingEnabled").checked = Boolean(settings.enabled);
    el("uiEncodingAcknowledge").checked = Boolean(settings.warning_acknowledged);
    el("uiEncodingEncoder").value = settings.encoder || "auto";
    el("uiEncodingMaxSessions").value = settings.max_sessions || 2;
    el("uiEncodingBadge").textContent = settings.enabled ? "Encoding on" : "Direct";
    el("uiEncodingBadge").classList.toggle("is-enabled", Boolean(settings.enabled));
    syncEnabledState();
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
  async function save() { busy(true); status("Saving encoding settings…"); try { const data = await api("/api/media-pipeline", {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: el("uiEncodingEnabled").checked, warning_acknowledged: el("uiEncodingAcknowledge").checked, encoder: el("uiEncodingEncoder").value, max_sessions: Number(el("uiEncodingMaxSessions").value)})}); render(data); status(data.settings?.enabled ? "FFmpeg encoding enabled for the normal M3U and all playback adapters." : "FFmpeg encoding disabled; normal playback remains direct where supported.", "success"); } catch (error) { status(error.message, "error"); } finally { busy(false); } }
  el("uiEncodingEnabled")?.addEventListener("change", syncEnabledState);
  el("uiEncodingTest")?.addEventListener("click", test);
  el("uiEncodingSave")?.addEventListener("click", save);
  void load();
})();
