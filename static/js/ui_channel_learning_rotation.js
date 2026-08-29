(() => {
  "use strict";

  const el = id => document.getElementById(id);
  let state = {running: false, channel_seconds: 1200, database: {}};
  let busy = false;

  function formatDuration(seconds) {
    const safe = Math.max(0, Math.floor(Number(seconds || 0)));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const remainder = safe % 60;
    return hours
      ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
      : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }

  function formatBytes(bytes) {
    const value = Math.max(0, Number(bytes || 0));
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function render() {
    const button = el("uiChannelRotationToggle");
    const status = el("uiChannelRotationStatus");
    const detail = el("uiChannelRotationDetail");
    const minutesInput = el("uiChannelRotationMinutes");
    if (!button || !status || !detail) return;

    const channel = state.current_channel || {};
    const index = Number(state.current_index || 0);
    const total = Number(state.total_channels || 0);
    const elapsed = Number(state.elapsed_seconds || 0);
    const duration = Math.max(1, Number(state.channel_seconds || 1200));
    const progress = Math.max(0, Math.min(100, elapsed * 100 / duration));
    const database = state.database || {};

    button.disabled = busy || state.phase === "stopping";
    button.textContent = state.running ? "Stop Channel Rotation" : "Start Channel Rotation";
    button.classList.toggle("ui-btn-danger", Boolean(state.running));
    button.classList.toggle("ui-btn-primary", !state.running);
    if (minutesInput) {
      minutesInput.disabled = busy || state.running || state.phase === "stopping";
      if (document.activeElement !== minutesInput) {
        minutesInput.value = String(Math.max(5, Math.round(duration / 60)));
      }
    }

    if (state.running && channel.name) {
      status.textContent = `Pass ${Number(state.pass_number || 1)} · Channel ${index} of ${total} · ${channel.name}`;
      detail.textContent = state.message || "Collecting commercial-learning statistics";
    } else if (state.phase === "stopped" || state.phase === "stopping") {
      status.textContent = state.phase === "stopping" ? "Stopping…" : "Rotation stopped";
      detail.textContent = state.message || "The channel rotator is not running.";
    } else {
      status.textContent = "Ready";
      detail.textContent = "Press Start when you want continuous channel rotation to begin.";
    }

    if (el("uiChannelRotationProgressBar")) {
      el("uiChannelRotationProgressBar").style.width = `${progress.toFixed(1)}%`;
    }
    if (el("uiChannelRotationCurrent")) {
      el("uiChannelRotationCurrent").textContent = channel.name
        ? `${channel.number} · ${channel.name}` : "—";
    }
    if (el("uiChannelRotationTime")) {
      el("uiChannelRotationTime").textContent = `${formatDuration(elapsed)} / ${formatDuration(duration)}`;
    }
    if (el("uiChannelRotationCommercials")) {
      el("uiChannelRotationCommercials").textContent = String(database.probable_commercials || 0);
    }
    if (el("uiChannelRotationLearned")) {
      el("uiChannelRotationLearned").textContent = String(database.channels_with_data || 0);
    }
    const note = el("uiChannelRotationNote");
    if (note) {
      const parts = [];
      if (state.running) {
        parts.push(`${Number(state.total_channel_slots_completed || 0)} channel slots completed`);
        parts.push(`${Number(state.passes_completed || 0)} full passes completed`);
        parts.push(`${formatBytes(state.bytes_received)} received on this channel`);
        parts.push(`${Number(state.snapshots_saved || 0)} image snapshots saved`);
        if (Number(state.channels_skipped_inactive || 0)) {
          parts.push(`${Number(state.channels_skipped_inactive)} inactive event feeds skipped`);
        }
      }
      if (Number(database.commercial_samples || 0)) {
        parts.push(`${Number(database.commercial_samples)} commercial samples in the learning database`);
      }
      if (state.last_error) parts.push(`Last reconnect: ${state.last_error}`);
      if (state.run_directory) parts.push(`Saved to ${state.run_directory}`);
      note.textContent = parts.join(" · ") || "The Overview page can be closed while this runs.";
    }
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }

  async function refresh() {
    if (!el("uiChannelRotationToggle")) return;
    try {
      state = await request(`/api/channel-learning-rotation?_=${Date.now()}`);
      render();
    } catch (error) {
      el("uiChannelRotationDetail").textContent = error.message;
    }
  }

  async function toggle() {
    if (busy) return;
    const minutesInput = el("uiChannelRotationMinutes");
    const channelMinutes = Math.round(Number(minutesInput?.value || 20));
    if (!state.running && (!Number.isFinite(channelMinutes) || channelMinutes < 5 || channelMinutes > 120)) {
      el("uiChannelRotationDetail").textContent = "Minutes per channel must be between 5 and 120.";
      return;
    }
    busy = true;
    render();
    try {
      state = await request("/api/channel-learning-rotation", {
        method: state.running ? "DELETE" : "POST",
        headers: state.running ? undefined : {"Content-Type": "application/json"},
        body: state.running ? undefined : JSON.stringify({channel_minutes: channelMinutes}),
      });
    } catch (error) {
      el("uiChannelRotationDetail").textContent = error.message;
    } finally {
      busy = false;
      render();
    }
  }

  document.addEventListener("click", event => {
    if (event.target.closest("#uiChannelRotationToggle")) void toggle();
  });
  setInterval(refresh, 5000);
  void refresh();
})();
