(() => {
  "use strict";

  const el = id => document.getElementById(id);
  const DVR_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const state = {
    data: {settings: {}, storage: {}, recordings: [], series_rules: [], counts: {}},
    selected: null,
    selectedSeriesId: null,
    selectedDvrWeekday: new Date().getDay(),
    panelOpen: false,
    busy: false,
  };

  const escape = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

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

  function dateText(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString([], {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
  }

  function rangeText(programme) {
    const start = new Date(programme?.start || programme?.start_at || "");
    const stop = new Date(programme?.stop || programme?.stop_at || "");
    if (Number.isNaN(start.getTime())) return "";
    const startText = dateText(start);
    if (Number.isNaN(stop.getTime())) return startText;
    return `${startText}–${stop.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}`;
  }

  function durationText(value) {
    const seconds = Math.max(0, Math.round(Number(value || 0)));
    if (seconds < 60) return `${seconds} sec`;
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return remainder ? `${minutes}m ${remainder}s` : `${minutes} min`;
  }

  function instantKey(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.getTime()) ? String(value || "").trim() : date.toISOString();
  }

  function programmeIdentity(channel, programme) {
    return [channel?.tvg_id, programme?.title, instantKey(programme?.start)]
      .map(value => String(value || "").trim().toLowerCase())
      .join("|");
  }

  function recordingIdentity(item) {
    return [item?.tvg_id, item?.title, instantKey(item?.start_at)]
      .map(value => String(value || "").trim().toLowerCase())
      .join("|");
  }

  function matchingRecording(channel, programme) {
    const key = programmeIdentity(channel, programme);
    return (state.data.recordings || []).find(item => recordingIdentity(item) === key) || null;
  }

  window.m3uDvrProgrammeClass = (channel, programme) => {
    const item = matchingRecording(channel, programme);
    if (!item) return "";
    if (item.status === "recording" || item.status === "processing") return "is-dvr-recording";
    if (item.status === "scheduled") return "is-dvr-scheduled";
    return "";
  };

  function dvrReady() {
    const settings = state.data.settings || {};
    const storage = state.data.storage || {};
    return Boolean(settings.enabled && storage.mount_configured && storage.available && storage.writable);
  }

  function setMessage(message, kind = "") {
    const target = el("guideDvrMessage");
    if (!target) return;
    target.textContent = message || "";
    target.className = `guide-dvr-message small-muted${kind ? ` is-${kind}` : ""}`;
  }

  function empty(message) {
    return `<div class="guide-dvr-empty">${escape(message)}</div>`;
  }

  function statusDescriptor(item) {
    const status = String(item.status || "").toLowerCase();
    const conversion = String(item.conversion_status || "").toLowerCase();
    const commercials = String(item.commercial_status || "").toLowerCase();
    if (status === "recording") return {key: "recording", label: "Recording"};
    if (status === "processing" || conversion === "processing" || commercials === "processing") {
      return {key: "processing", label: "Processing"};
    }
    if (status === "scheduled") return {key: "scheduled", label: "Scheduled"};
    if (status === "completed" && (conversion === "pending" || commercials === "pending")) {
      return {key: "queued", label: "Queued"};
    }
    if (status === "completed" && item.playback_url) return {key: "ready", label: "Ready"};
    if (status === "failed") return {key: "failed", label: "Failed"};
    if (status === "missed") return {key: "failed", label: "Missed"};
    if (status === "cancelled") return {key: "cancelled", label: "Cancelled"};
    return {key: "completed", label: status ? status.replaceAll("_", " ") : "Completed"};
  }

  function queueTime(item, status) {
    const range = rangeText(item);
    if (status.key === "scheduled") return range ? `Next showing: ${range}` : "Scheduled";
    if (status.key === "recording") return range ? `Recording now: ${range}` : "Recording now";
    return range ? `Recorded: ${range}` : "";
  }

  function queueSort(left, right) {
    const rank = {recording: 0, processing: 1, queued: 2, scheduled: 3, ready: 4, failed: 5, cancelled: 6, completed: 7};
    const leftStatus = statusDescriptor(left);
    const rightStatus = statusDescriptor(right);
    const rankDifference = (rank[leftStatus.key] ?? 99) - (rank[rightStatus.key] ?? 99);
    if (rankDifference) return rankDifference;
    const leftTime = new Date(left.start_at || left.completed_at || 0).getTime() || 0;
    const rightTime = new Date(right.start_at || right.completed_at || 0).getTime() || 0;
    return leftStatus.key === "scheduled" ? leftTime - rightTime : rightTime - leftTime;
  }

  function selectedGuideWindow() {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - start.getDay() + state.selectedDvrWeekday);
    return {start, end: new Date(start.getTime() + 24 * 60 * 60 * 1000)};
  }

  function itemIsInGuideWindow(item, bounds) {
    const start = new Date(item.start_at || item.completed_at || "");
    if (Number.isNaN(start.getTime())) return false;
    const stop = new Date(item.stop_at || item.completed_at || item.start_at || "");
    const stopTime = Number.isNaN(stop.getTime()) ? start.getTime() : stop.getTime();
    return start.getTime() < bounds.end.getTime() && stopTime >= bounds.start.getTime();
  }

  function syncDvrDayNav() {
    const target = el("guideDvrDayNav");
    if (!target) return;
    target.innerHTML = DVR_WEEKDAYS.map((label, weekday) => `<button type="button"
      class="guide-day-button"
      data-dvr-weekday="${weekday}"
      aria-pressed="${weekday === state.selectedDvrWeekday ? "true" : "false"}">${label}</button>`).join("");
  }

  function itemMarkup(item) {
    const active = ["scheduled", "recording", "processing"].includes(item.status);
    const status = statusDescriptor(item);
    const completed = status.key === "ready" && item.playback_url;
    const canDelete = !active && status.key !== "processing";
    const time = queueTime(item, status);
    return `<article class="guide-dvr-item">
      <div class="guide-dvr-item-header">
        <div class="guide-dvr-item-title">${escape(item.title)}</div>
        <span class="guide-dvr-status is-${status.key}">${escape(status.label)}</span>
      </div>
      ${time ? `<div class="guide-dvr-item-time">${escape(time)}</div>` : ""}
      ${item.channel_name ? `<div class="guide-dvr-item-meta">${escape(item.channel_name)}</div>` : ""}
      ${item.subtitle ? `<div class="guide-dvr-item-meta">${escape(item.subtitle)}</div>` : ""}
      ${item.error ? `<div class="guide-dvr-item-error">${escape(item.error)}</div>` : ""}
      ${item.conversion_status === "pending" ? '<div class="guide-dvr-item-meta">H.265 conversion queued for the next nightly or manual update.</div>' : ""}
      ${item.commercial_status === "processing" ? '<div class="guide-dvr-item-meta">Detecting and removing commercials…</div>' : ""}
      ${item.commercial_status === "removed" ? `<div class="guide-dvr-item-meta">Removed ${Number(item.commercial_count || 0)} commercial break${Number(item.commercial_count || 0) === 1 ? "" : "s"} (${durationText(item.commercial_seconds)}).</div>` : ""}
      ${item.commercial_status === "none" ? '<div class="guide-dvr-item-meta">No commercial breaks were detected.</div>' : ""}
      ${item.commercial_error ? `<div class="guide-dvr-item-error">${escape(item.commercial_error)}</div>` : ""}
      ${item.conversion_error ? `<div class="guide-dvr-item-error">${escape(item.conversion_error)}</div>` : ""}
      <div class="guide-dvr-item-actions">
        ${completed ? `<button class="btn btn-success btn-sm" type="button" data-dvr-play="${item.id}">Play</button>` : ""}
        ${active ? `<button class="btn btn-outline-light btn-sm" type="button" data-dvr-cancel="${item.id}">Cancel</button>` : ""}
        ${canDelete ? `<button class="btn btn-outline-danger btn-sm" type="button" data-dvr-delete="${item.id}">Delete</button>` : ""}
      </div>
    </article>`;
  }

  function seriesMarkup(rule, items) {
    const next = items
      .filter(item => Number(item.rule_id) === Number(rule.id) && ["scheduled", "recording"].includes(item.status))
      .sort((left, right) => new Date(left.start_at || 0) - new Date(right.start_at || 0))[0];
    const nextText = next ? `Next showing: ${rangeText(next)}` : "No upcoming showing found.";
    const selected = Number(state.selectedSeriesId) === Number(rule.id);
    return `<article class="guide-dvr-item guide-dvr-series-item${selected ? " is-selected" : ""}">
      <button class="guide-dvr-series-select" type="button" data-dvr-select-series="${rule.id}">
        <span class="guide-dvr-item-header">
          <span class="guide-dvr-item-title">${escape(rule.title)}</span>
          <span class="guide-dvr-status is-enabled">Enabled</span>
        </span>
        <span class="guide-dvr-item-time">${escape(nextText)}</span>
        <span class="guide-dvr-item-meta">${escape(rule.channel_name || rule.tvg_id)}</span>
      </button>
      <div class="guide-dvr-item-actions"><button class="btn btn-outline-danger btn-sm" type="button" data-dvr-remove-series="${rule.id}">Cancel series</button></div>
    </article>`;
  }

  function render() {
    const data = state.data || {};
    const settings = data.settings || {};
    const storage = data.storage || {};
    const maintenance = data.maintenance || {};
    const items = Array.isArray(data.recordings) ? data.recordings : [];
    const active = items.filter(item => ["recording", "processing"].includes(item.status));
    const rules = Array.isArray(data.series_rules) ? data.series_rules : [];
    if (state.selectedSeriesId && !rules.some(rule => Number(rule.id) === Number(state.selectedSeriesId))) {
      state.selectedSeriesId = null;
    }
    const bounds = selectedGuideWindow();
    const selectedRule = rules.find(rule => Number(rule.id) === Number(state.selectedSeriesId)) || null;
    const visibleItems = items.filter(item => {
      const status = statusDescriptor(item);
      if (status.key === "cancelled") return false;
      if (selectedRule && Number(item.rule_id) !== Number(selectedRule.id)) return false;
      if (["recording", "processing"].includes(status.key)) return true;
      return itemIsInGuideWindow(item, bounds);
    });
    const badgeCount = active.length;
    el("guideDvrBadge").textContent = String(badgeCount);
    el("guideDvrBadge").classList.toggle("d-none", badgeCount === 0);
    syncDvrDayNav();
    el("guideDvrQueueTitle").textContent = selectedRule ? selectedRule.title : "Recordings";
    el("guideDvrShowAll").classList.toggle("d-none", !selectedRule);
    el("guideDvrQueue").innerHTML = visibleItems.length
      ? visibleItems.slice().sort(queueSort).map(itemMarkup).join("")
      : empty(selectedRule ? `No ${selectedRule.title} recordings in this guide window.` : "No recordings in this guide window.");
    el("guideDvrSeriesCount").textContent = String(rules.length);
    el("guideDvrSeries").innerHTML = rules.length
      ? rules.map(rule => seriesMarkup(rule, items)).join("")
      : empty("No series recording rules.");

    const host = settings.host_path || storage.configured_host_path || "No host folder configured";
    const free = Number(storage.free_bytes || 0);
    const freeText = free > 0 ? ` • ${(free / 1073741824).toFixed(1)} GB free` : "";
    el("guideDvrStorage").textContent = `${settings.enabled ? "Enabled" : "Disabled"} • ${host}${freeText}`;
    if (maintenance.running) {
      setMessage("Detecting commercials and converting completed recordings. You can leave this panel open or come back later.");
    } else if (maintenance.error) {
      setMessage(maintenance.error, "error");
    } else if (maintenance.finished_at && maintenance.result) {
      const result = maintenance.result;
      const converted = Number(result.converted || 0);
      const removed = Number(result.commercials_removed || 0);
      const failed = Number(result.failed || 0);
      setMessage(
        failed
          ? `DVR processing finished: ${converted} converted, ${removed} commercial breaks removed, ${failed} failed.`
          : converted
            ? `DVR processing finished: ${converted} converted and ${removed} commercial breaks removed.`
            : "DVR processing finished; no completed recordings were ready.",
        failed ? "error" : "success",
      );
    } else if (!settings.enabled) {
      setMessage("Enable DVR and choose its host folder under Settings → DVR before scheduling recordings.");
    } else if (!dvrReady()) {
      setMessage("The configured DVR host folder is not mounted and writable. Check Settings → DVR.", "error");
    } else if (!state.busy) {
      setMessage(settings.transcode_hevc
        ? `Completed captures are queued for H.265/MKV conversion${settings.remove_commercials ? " with commercial removal" : ""} during the next nightly or manual update.`
        : "Recordings are kept as transport streams.");
    }
    if (typeof renderGuide === "function") renderGuide();
  }

  async function load({silent = false} = {}) {
    try {
      state.data = await api(`/api/dvr?_=${Date.now()}`);
      render();
    } catch (error) {
      if (!silent) setMessage(error.message, "error");
    }
  }

  function programmePayload() {
    const selected = state.selected || {};
    const channel = selected.channel || {};
    const programme = selected.programme || {};
    return {
      play_url: channel.play_url,
      tvg_id: channel.tvg_id,
      title: programme.title,
      subtitle: programme.subtitle || "",
      description: programme.description || "",
      start: programme.start,
      stop: programme.stop,
    };
  }

  function showProgramme(detail) {
    state.selected = detail || null;
    const channel = detail?.channel || {};
    const programme = detail?.programme || {};
    el("guideProgrammeTitle").textContent = programme.title || "Program";
    el("guideProgrammeMeta").textContent = [channel.name, rangeText(programme), programme.subtitle].filter(Boolean).join(" • ");
    el("guideProgrammeDescription").textContent = programme.description || "No description is available.";
    el("guideProgrammeMessage").textContent = dvrReady()
      ? "Choose one episode or every matching airing on this channel."
      : "DVR must be enabled under Settings → DVR before recording.";
    const now = Date.now();
    const start = new Date(programme.start || "").getTime();
    const stop = new Date(programme.stop || "").getTime();
    const current = Number.isFinite(start) && Number.isFinite(stop) && start <= now && now < stop;
    el("guideProgrammePlay").classList.toggle("d-none", !current);
    const existing = matchingRecording(channel, programme);
    el("guideRecordOnce").disabled = !dvrReady() || Boolean(existing && ["scheduled", "recording", "processing", "completed"].includes(existing.status));
    el("guideRecordSeries").disabled = !dvrReady();
    el("guideProgrammeDialog").showModal();
  }

  async function schedule(kind) {
    if (!state.selected || state.busy) return;
    state.busy = true;
    for (const id of ["guideRecordOnce", "guideRecordSeries"]) el(id).disabled = true;
    el("guideProgrammeMessage").textContent = kind === "series" ? "Creating series rule…" : "Scheduling recording…";
    try {
      const result = await api(kind === "series" ? "/api/dvr/series" : "/api/dvr/recordings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(programmePayload()),
      });
      state.data = result.dvr || state.data;
      el("guideProgrammeDialog").close();
      state.panelOpen = true;
      el("guideDvrPanel").classList.remove("d-none");
      setMessage(kind === "series" ? "Series scheduled." : "Recording scheduled.", "success");
      render();
    } catch (error) {
      el("guideProgrammeMessage").textContent = error.message;
    } finally {
      state.busy = false;
    }
  }

  async function mutate(path, options = {}) {
    state.busy = true;
    try {
      const result = await api(path, options);
      state.data = result.dvr || state.data;
      render();
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      state.busy = false;
    }
  }

  async function playRecording(id) {
    const item = (state.data.recordings || []).find(recording => Number(recording.id) === Number(id));
    if (!item?.playback_url) return;
    await stopPlayback();
    guideEls.playerPanel.classList.remove("d-none");
    showLocalPlayer();
    guideEls.nowPlayingLabel.textContent = "DVR recording";
    guideEls.playerTitle.textContent = item.title || "Recording";
    guideEls.playerMeta.textContent = [item.channel_name, dateText(item.start_at)].filter(Boolean).join(" • ");
    guideEls.playerMessage.textContent = "Playing recorded program.";
    guideEls.player.src = `${item.playback_url}?_=${Date.now()}`;
    guideEls.player.play().catch(() => {
      guideEls.playerMessage.textContent = "Press Play to start this recording.";
    });
    guideEls.playerPanel.scrollIntoView({behavior: "smooth", block: "start"});
  }

  el("guideDvrBtn")?.addEventListener("click", () => {
    state.panelOpen = !state.panelOpen;
    el("guideDvrPanel").classList.toggle("d-none", !state.panelOpen);
    if (state.panelOpen) load();
  });
  el("guideDvrCloseBtn")?.addEventListener("click", () => {
    state.panelOpen = false;
    el("guideDvrPanel").classList.add("d-none");
  });
  el("guideProgrammeClose")?.addEventListener("click", () => el("guideProgrammeDialog").close());
  el("guideRecordOnce")?.addEventListener("click", () => schedule("once"));
  el("guideRecordSeries")?.addEventListener("click", () => schedule("series"));
  el("guideProgrammePlay")?.addEventListener("click", () => {
    const channel = state.selected?.channel;
    el("guideProgrammeDialog").close();
    if (channel) playChannel(channel);
  });
  el("guideDvrPanel")?.addEventListener("click", event => {
    const day = event.target.closest("[data-dvr-weekday]");
    if (day) {
      state.selectedDvrWeekday = Math.max(0, Math.min(6, Number(day.dataset.dvrWeekday) || 0));
      render();
      return;
    }
    const selectSeries = event.target.closest("[data-dvr-select-series]");
    if (selectSeries) {
      state.selectedSeriesId = Number(selectSeries.dataset.dvrSelectSeries);
      render();
      return;
    }
    const play = event.target.closest("[data-dvr-play]");
    if (play) return void playRecording(play.dataset.dvrPlay);
    const cancel = event.target.closest("[data-dvr-cancel]");
    if (cancel) return void mutate(`/api/dvr/recordings/${cancel.dataset.dvrCancel}/cancel`, {method: "POST"});
    const removeSeries = event.target.closest("[data-dvr-remove-series]");
    if (removeSeries && window.confirm("Cancel this series? Existing recordings will be kept.")) {
      return void mutate(`/api/dvr/series/${removeSeries.dataset.dvrRemoveSeries}`, {method: "DELETE"});
    }
    const remove = event.target.closest("[data-dvr-delete]");
    if (remove && window.confirm("Delete this recording and its file? This cannot be undone.")) {
      void mutate(`/api/dvr/recordings/${remove.dataset.dvrDelete}`, {method: "DELETE"});
    }
  });
  el("guideDvrShowAll")?.addEventListener("click", () => {
    state.selectedSeriesId = null;
    render();
  });
  el("guideDayNav")?.addEventListener("click", () => {
    if (!state.panelOpen) return;
    window.setTimeout(() => {
      const pressed = document.querySelector('#guideDayNav [data-guide-day][aria-pressed="true"]');
      const selected = String(pressed?.dataset.guideDay || "now");
      const dayOffset = selected === "now" ? 0 : Math.max(0, Number(selected) || 0);
      const selectedDate = new Date();
      selectedDate.setDate(selectedDate.getDate() + dayOffset);
      state.selectedDvrWeekday = selectedDate.getDay();
      render();
    }, 0);
  });
  window.addEventListener("m3u-guide-programme", event => showProgramme(event.detail));
  window.setInterval(() => load({silent: true}), 15_000);
  if (new URLSearchParams(window.location.search).get("dvr") === "1") {
    state.panelOpen = true;
    el("guideDvrPanel")?.classList.remove("d-none");
  }
  load({silent: true});
})();
