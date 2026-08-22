let channels = [];
let selected = new Set();
let saveTimer = null;
let customGroups = [];
let activeGroupSlug = "";
let activeGroupMembers = new Set();
let showGroupOnly = false;
let epgSources = [];
let epgBuiltins = [];
let publicEpgState = {countries: [], enabled_codes: ["US"], enabled_count: 1, summary: "United States"};
let masterUpdateState = {enabled: true, time: "03:00", timezone: "America/New_York", next_update: null, last_update: null, last_duration_seconds: null, last_trigger: null, running: false, started_at: null, trigger: null, elapsed_seconds: null};
let masterUpdateBusy = false;
let masterUpdateLocalStartedAt = 0;
let masterUpdateElapsedTimer = null;
let providerSources = [];
let currentSourceMode = "";
let providerOperationBusy = false;
let providerOperationStartedAt = 0;
let providerProgressTimer = null;
let providerProgressPollTick = 0;

const els = {
  table: document.getElementById("channelTable"),
  search: document.getElementById("search"),
  groupFilter: document.getElementById("groupFilter"),
  selectedOnly: document.getElementById("selectedOnly"),
  excludeSdChannels: document.getElementById("excludeSdChannels"),
  selectedCount: document.getElementById("selectedCount"),
  visibleCount: document.getElementById("visibleCount"),
  totalCount: document.getElementById("totalCount"),
  status: document.getElementById("status"),
  playlistUrl: document.getElementById("playlistUrl"),
  activeGroup: document.getElementById("activeGroup"),
  groupPlaylistUrl: document.getElementById("groupPlaylistUrl")
};

els.playlistUrl.value = `${location.origin}/playlist/channels.m3u`;
els.groupPlaylistUrl.value = `${location.origin}/playlist/all.m3u`;
const epgOutputUrlInput = document.getElementById("epgOutputUrl");
if (epgOutputUrlInput) epgOutputUrlInput.value = `${location.origin}/epg/epg.xml`;

const CHANNEL_MANAGER_COLLAPSE_KEY = "m3u-picker.channel-manager-collapsed";
let channelManagerCollapsed = localStorage.getItem(CHANNEL_MANAGER_COLLAPSE_KEY) === "true";

function applyChannelManagerCollapse() {
  const body = document.getElementById("channelManagerBody");
  const button = document.getElementById("channelManagerCollapseBtn");
  if (!body || !button) return;
  body.classList.toggle("d-none", channelManagerCollapsed);
  button.textContent = channelManagerCollapsed ? "Expand" : "Collapse";
  button.setAttribute("aria-expanded", String(!channelManagerCollapsed));
}

document.getElementById("channelManagerCollapseBtn")?.addEventListener("click", () => {
  channelManagerCollapsed = !channelManagerCollapsed;
  localStorage.setItem(CHANNEL_MANAGER_COLLAPSE_KEY, String(channelManagerCollapsed));
  applyChannelManagerCollapse();
});
applyChannelManagerCollapse();

function setStatus(message) {
  els.status.textContent = message || "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function channelKey(channel) {
  return String(channel.key || channel.url || "").trim();
}

function isGeneratedSportsChannel(channel) {
  return Boolean(channel.is_sports_generated || Number(channel.id) < 0);
}

const PROVIDER_SOURCE_LABELS = [
  {pattern: /(^|\.)astranettv\./i, label: "AstraNet"},
  {pattern: /(^|\.)astranet\./i, label: "AstraNet"},
];

function titleCaseSource(value) {
  return String(value || "")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, letter => letter.toUpperCase())
    .replace(/\s+/g, " ")
    .trim();
}

function providerSourceLabel(channel) {
  if (isGeneratedSportsChannel(channel)) return "Sports Automation";

  const raw = String(channel.url || "").trim();
  try {
    const hostname = new URL(raw).hostname.toLowerCase().replace(/^www\./, "");
    if (!hostname || /^[\d.:]+$/.test(hostname)) return "Provider";

    const known = PROVIDER_SOURCE_LABELS.find(entry => entry.pattern.test(hostname));
    if (known) return known.label;

    const parts = hostname.split(".").filter(Boolean);
    const commonSubdomains = new Set(["api", "cdn", "edge", "live", "media", "stream", "streams", "tv"]);
    while (parts.length > 2 && commonSubdomains.has(parts[0])) parts.shift();

    const candidate = parts.length >= 2 ? parts.at(-2) : parts[0];
    return titleCaseSource(candidate) || "Provider";
  } catch {
    return "Provider";
  }
}

function updateClearSearchButton() {
  const button = document.getElementById("clearSearchBtn");
  if (!button) return;
  button.classList.toggle("d-none", els.search.value.length === 0);
}

async function copyInputValue(inputId, buttonId) {
  const input = document.getElementById(inputId);
  const button = document.getElementById(buttonId);
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    input.select();
    document.execCommand("copy");
  }
  button.textContent = "Copied!";
  setTimeout(() => { button.textContent = "Copy"; }, 1500);
}

function setSourceMode(mode) {
  currentSourceMode = mode || "";
  const urlInput = document.getElementById("m3uUrl");
  const nameInput = document.getElementById("primaryName");
  const usernameInput = document.getElementById("m3uUsername");
  const passwordInput = document.getElementById("m3uPassword");
  const urlButton = document.getElementById("loadUrlBtn");
  const fileInput = document.getElementById("m3uFile");
  const fileButton = document.getElementById("uploadBtn");
  const fieldset = document.getElementById("primaryProviderFieldset");
  const label = document.getElementById("sourceModeLabel");
  const fileActions = document.getElementById("filePrimaryActions");
  const locked = Boolean(currentSourceMode) || providerSources.some(source => source.role === "primary") || providerOperationBusy;

  if (fieldset) {
    fieldset.disabled = locked;
    fieldset.setAttribute("aria-disabled", String(locked));
  }
  urlInput.disabled = locked;
  nameInput.disabled = locked;
  usernameInput.disabled = locked;
  passwordInput.disabled = locked;
  urlButton.disabled = locked;
  fileInput.disabled = locked;
  fileButton.disabled = locked;
  if (locked) {
    // Browsers/password managers may repopulate credential fields after the
    // provider list loads. A saved primary is managed from the table below;
    // these inputs are only for adding a replacement after removal.
    usernameInput.value = "";
    passwordInput.value = "";
  }
  if (fileActions) fileActions.classList.toggle("d-none", currentSourceMode !== "file");
  if (label) label.textContent = currentSourceMode === "file" ? "File primary loaded." : "";
}

function showUrlModal() {
  const modalElement = document.getElementById("urlModal");
  const input = document.getElementById("modalUrlInput");
  input.value = "";
  document.getElementById("modalUsernameInput").value = "";
  document.getElementById("modalPasswordInput").value = "";
  const modal = new bootstrap.Modal(modalElement);
  modal.show();
  modalElement.addEventListener("shown.bs.modal", () => input.focus(), {once: true});
}

function acceptModalUrl() {
  const modalElement = document.getElementById("urlModal");
  const input = document.getElementById("modalUrlInput");
  const value = input.value.trim();
  if (!value) {
    input.focus();
    return;
  }
  document.getElementById("m3uUrl").value = value;
  document.getElementById("m3uUsername").value = document.getElementById("modalUsernameInput").value;
  document.getElementById("m3uPassword").value = document.getElementById("modalPasswordInput").value;
  bootstrap.Modal.getInstance(modalElement)?.hide();
  loadFromUrl();
}

function filteredChannels() {
  const query = els.search.value.trim().toLowerCase();
  const group = els.groupFilter.value;
  const selectedOnly = els.selectedOnly.checked;
  const excludeSd = Boolean(els.excludeSdChannels?.checked);

  return channels.filter(channel => {
    const isSaved = selected.has(Number(channel.id));
    // Discovery filters must never make a saved channel impossible to remove.
    // In Saved Channels mode, retain selected SD/low-bandwidth entries even
    // when the normal catalog view hides them.
    if (excludeSd && !(selectedOnly && isSaved) && String(channel.group || "").trim().toUpperCase() === "LOW BANDWIDTH") return false;
    if (group && channel.group !== group) return false;
    if (selectedOnly && !isSaved) return false;
    if (showGroupOnly && activeGroupSlug && !activeGroupMembers.has(channelKey(channel))) return false;
    if (query) {
      const haystack = `${channel.name} ${channel.group} ${channel.url} ${channel.sports_subtitle || ""}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
}

function removableVisibleChannels() {
  return filteredChannels().filter(channel =>
    !isGeneratedSportsChannel(channel) && selected.has(Number(channel.id))
  );
}

function rebuildProviderGroupFilter() {
  const current = els.groupFilter.value;
  const groups = [...new Set(channels.map(channel => channel.group).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
  els.groupFilter.innerHTML = `<option value="">All provider groups</option>` +
    groups.map(group => `<option value="${escapeHtml(group)}">${escapeHtml(group)}</option>`).join("");
  if (groups.includes(current)) els.groupFilter.value = current;
}

function render() {
  const visible = filteredChannels();
  els.table.innerHTML = visible.map(channel => {
    const generated = isGeneratedSportsChannel(channel);
    const id = Number(channel.id);
    const subtitle = generated && channel.sports_subtitle
      ? `<div class="sports-channel-subtitle">${escapeHtml(channel.sports_subtitle)}</div>`
      : "";
    const badge = generated ? `<span class="badge text-bg-primary ms-2">Auto sports</span>` : "";
    const sourceLabel = providerSourceLabel(channel);
    return `
      <tr data-id="${id}" class="${generated ? "sports-generated-row" : ""}">
        <td>
          <input class="form-check-input channel-check" type="checkbox" data-id="${id}"
            ${selected.has(id) || generated ? "checked" : ""} ${generated ? "disabled" : ""}>
        </td>
        <td>
          <div>${escapeHtml(channel.name)}${badge}</div>
          ${subtitle}
        </td>
        <td>${escapeHtml(channel.group)}</td>
        <td class="source-cell"><span class="source-badge">${escapeHtml(sourceLabel)}</span></td>
      </tr>`;
  }).join("");

  const generatedIds = channels.filter(isGeneratedSportsChannel).map(channel => Number(channel.id));
  for (const id of generatedIds) selected.add(id);

  els.selectedCount.textContent = selected.size;
  els.visibleCount.textContent = visible.length;
  els.totalCount.textContent = channels.length;

  const selectButton = document.getElementById("selectVisibleBtn");
  const clearButton = document.getElementById("clearVisibleBtn");
  const showSelectedButton = document.getElementById("showSelectedBtn");
  const manualVisible = visible.filter(channel => !isGeneratedSportsChannel(channel));
  const removableVisible = manualVisible.filter(channel => selected.has(Number(channel.id)));

  if (selectButton) selectButton.textContent = `Add all ${manualVisible.length}`;
  if (clearButton) clearButton.textContent = `Remove all ${removableVisible.length}`;
  if (showSelectedButton) {
    showSelectedButton.textContent = els.selectedOnly.checked
      ? `Show all (${selected.size} saved)`
      : `Saved ${selected.size} channels`;
  }

  const savedMode = els.selectedOnly.checked;
  if (selectButton) selectButton.disabled = savedMode || manualVisible.length === 0;
  if (clearButton) {
    const canRemove = removableVisible.length > 0;
    clearButton.disabled = !canRemove;
    clearButton.classList.toggle("btn-outline-danger", canRemove);
    clearButton.classList.toggle("btn-outline-secondary", !canRemove);
  }
  if (showSelectedButton) {
    showSelectedButton.disabled = selected.size === 0 && !savedMode;
    showSelectedButton.classList.toggle("btn-success", savedMode);
    showSelectedButton.classList.toggle("btn-outline-success", !savedMode);
  }
}

function renderGroups() {
  const pills = document.getElementById("groupPills");
  pills.innerHTML = customGroups.map(group => `
    <span class="badge rounded-pill text-bg-secondary group-pill ${group.slug === activeGroupSlug ? "active" : ""}"
      data-slug="${escapeHtml(group.slug)}">${escapeHtml(group.name)}</span>
  `).join("");

  els.activeGroup.innerHTML = `<option value="">No group selected</option>` +
    customGroups.map(group => `<option value="${escapeHtml(group.slug)}">${escapeHtml(group.name)}</option>`).join("");
  if (activeGroupSlug) els.activeGroup.value = activeGroupSlug;
  updateGroupUrl();
}

function updateGroupUrl() {
  els.groupPlaylistUrl.value = activeGroupSlug
    ? `${location.origin}/playlist/group/${activeGroupSlug}.m3u`
    : `${location.origin}/playlist/all.m3u`;
}

async function loadGroups() {
  try {
    const response = await fetch("/api/groups");
    const data = await response.json();
    customGroups = data.groups || [];
    renderGroups();
  } catch {
    customGroups = [];
  }
}

function formatEpgTimestamp(value) {
  if (!value) return "Not generated";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  });
}

function formatProviderExpiry(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString([], {
    year: "numeric",
    month: "short",
    day: "numeric"
  });
}

function formatEpgSize(bytes) {
  const size = Number(bytes || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(size >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
  if (size >= 1024) return `${Math.round(size / 1024)} KB`;
  return `${size} B`;
}

function renderBuiltInEpgStatus() {
  const byId = new Map(epgBuiltins.map(item => [String(item.id || ""), item]));
  for (const [id, elementId] of [["epg", "epgOutputStatus"]]) {
    const target = document.getElementById(elementId);
    if (!target) continue;
    const guide = byId.get(id);
    if (!guide || !guide.cached) {
      target.textContent = "Generated on first request";
      target.title = "";
      continue;
    }
    const updated = formatEpgTimestamp(guide.last_refresh);
    const size = formatEpgSize(guide.size_bytes);
    target.innerHTML = `<span class="epg-status-primary">Updated ${escapeHtml(updated)}</span>${size ? `<span class="epg-status-secondary">${escapeHtml(size)}</span>` : ""}`;
    target.title = [updated, size].filter(Boolean).join(" · ");
  }
}

function renderEpgSources() {
  const list = document.getElementById("epgSources");
  if (!list) return;
  renderBuiltInEpgStatus();
  if (!epgSources.length) {
    list.innerHTML = `<tr class="epg-empty-row"><td colspan="5" class="small-muted">No additional XMLTV sources.</td></tr>`;
    return;
  }
  list.innerHTML = epgSources.map(source => {
    const publicUrl = `${location.origin}${source.url_path || `/epg/${source.id}.xml`}`;
    const status = source.last_error
      ? `<span class="text-warning epg-status-primary">Refresh failed</span>`
      : source.last_refresh
        ? `<span class="epg-status-primary">Updated ${escapeHtml(formatEpgTimestamp(source.last_refresh))}</span>`
        : `<span class="epg-status-primary">Never updated</span>`;
    const title = source.last_error ? escapeHtml(source.last_error) : escapeHtml(formatEpgTimestamp(source.last_refresh));
    return `
      <tr class="epg-source-row" data-id="${escapeHtml(source.id)}">
        <td class="epg-name-cell" title="${escapeHtml(source.name || source.id)}"><strong>${escapeHtml(source.name || source.id)}</strong></td>
        <td><span class="badge rounded-pill epg-type-badge epg-type-external">${escapeHtml(source.source_label || "External")}</span></td>
        <td class="epg-served-cell">
          <div class="input-group input-group-sm">
            <input class="form-control" value="${escapeHtml(publicUrl)}" readonly aria-label="Served XMLTV URL for ${escapeHtml(source.name || source.id)}">
            <button class="btn btn-success epg-copy-btn" type="button">Copy</button>
          </div>
        </td>
        <td class="epg-status-cell small-muted" title="${title}">${status}</td>
        <td class="text-end"><button class="btn btn-outline-danger btn-sm epg-delete-btn epg-action-btn" type="button">Delete</button></td>
      </tr>`;
  }).join("");
}

function renderPublicEpg() {
  const target = document.getElementById("publicEpgCountries");
  const summary = document.getElementById("publicEpgSummary");
  const status = document.getElementById("publicEpgStatus");
  if (summary) summary.textContent = publicEpgState.summary || "No countries enabled";
  if (!target) return;
  const countries = publicEpgState.countries || [];
  target.innerHTML = countries.map(country => {
    const id = `publicEpg-${country.code}`;
    const cached = country.cached && country.compressed_bytes
      ? ` • ${formatEpgSize(country.compressed_bytes)} cached`
      : "";
    return `
      <div class="form-check public-epg-country-item">
        <input class="form-check-input public-epg-country-check" type="checkbox" id="${escapeHtml(id)}" data-code="${escapeHtml(country.code)}" ${country.enabled ? "checked" : ""}>
        <label class="form-check-label" for="${escapeHtml(id)}">${escapeHtml(country.name)}<span class="small-muted">${escapeHtml(cached)}</span></label>
      </div>`;
  }).join("");
  if (status) {
    const enabled = countries.filter(country => country.enabled);
    const failures = enabled.filter(country => country.last_error);
    status.textContent = failures.length
      ? `${failures.length} enabled public guide${failures.length === 1 ? " has" : "s have"} a refresh warning; cached data remains available when present.`
      : "Enabled guides refresh once per day during the master update and fill uncovered guide windows for any selected channel.";
  }
}


function formatElapsedSeconds(value) {
  const total = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatUpdateDuration(value) {
  const total = Math.max(0, Math.round(Number(value) || 0));
  if (!total && Number(value) !== 0) return "";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts = [];
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  if (seconds || !parts.length) parts.push(`${seconds}s`);
  return parts.join(" ");
}

function masterUpdateElapsedSeconds() {
  if (masterUpdateBusy && masterUpdateLocalStartedAt) {
    return Math.max(0, Math.floor((Date.now() - masterUpdateLocalStartedAt) / 1000));
  }
  if (masterUpdateState.running && masterUpdateState.started_at) {
    const started = Date.parse(masterUpdateState.started_at);
    if (Number.isFinite(started)) return Math.max(0, Math.floor((Date.now() - started) / 1000));
  }
  return Math.max(0, Number(masterUpdateState.elapsed_seconds) || 0);
}

function updateMasterUpdateTicker() {
  const shouldRun = Boolean(masterUpdateBusy || masterUpdateState.running || sportsState?.scan?.running);
  if (shouldRun && !masterUpdateElapsedTimer) {
    masterUpdateElapsedTimer = setInterval(() => {
      renderMasterUpdate();
      renderSportsScanStatus();
    }, 1000);
  } else if (!shouldRun && masterUpdateElapsedTimer) {
    clearInterval(masterUpdateElapsedTimer);
    masterUpdateElapsedTimer = null;
  }
}

function renderMasterUpdate() {
  const enabled = document.getElementById("masterUpdateEnabled");
  const time = document.getElementById("masterUpdateTime");
  const status = document.getElementById("masterUpdateStatus");
  const button = document.getElementById("masterUpdateNowBtn");
  const runningPanel = document.getElementById("masterUpdateRunning");
  const runningText = document.getElementById("masterUpdateRunningText");
  if (enabled) enabled.checked = Boolean(masterUpdateState.enabled);
  if (time) {
    time.value = masterUpdateState.time || "03:00";
    time.disabled = !masterUpdateState.enabled;
  }

  const scanRunning = Boolean(sportsState?.scan?.running);
  const masterRunning = Boolean(masterUpdateBusy || masterUpdateState.running || scanRunning);
  const scanTrigger = String(sportsState?.scan?.trigger || "").trim();
  const effectiveTrigger = masterUpdateBusy
    ? "manual"
    : String(masterUpdateState.trigger || scanTrigger || "scheduled").trim();
  if (button) {
    // Never allow a second master update (or a cancel-via-double-click) while
    // the current update pipeline is active.
    button.disabled = masterRunning;
    button.textContent = masterRunning ? "Updating…" : "Update Now";
    button.classList.remove("btn-danger");
    button.classList.add("btn-primary");
  }

  if (runningPanel && runningText) {
    runningPanel.classList.toggle("d-none", !masterRunning);
    if (masterRunning) {
      const label = effectiveTrigger === "scheduled" ? "Automatic update" : "Manual update";
      runningText.textContent = `${label} • ${formatElapsedSeconds(masterUpdateElapsedSeconds())}`;
    }
  }

  if (status) {
    const next = masterUpdateState.enabled ? formatNextUpdate(masterUpdateState.next_update) : "automatic updates disabled";
    const last = masterUpdateState.last_update ? formatNextUpdate(masterUpdateState.last_update) : "never";
    const duration = masterUpdateState.last_duration_seconds == null
      ? ""
      : formatUpdateDuration(masterUpdateState.last_duration_seconds);
    const zone = masterUpdateState.timezone || "local time";
    status.textContent = [
      `Next: ${next}`,
      `Last: ${last}`,
      duration ? `Took ${duration}` : "",
      zone
    ].filter(Boolean).join(" • ");
  }
  updateMasterUpdateTicker();
}

async function saveMasterUpdate(changes) {
  try {
    const response = await fetch("/api/master-update", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(changes)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save master update settings.");
    masterUpdateState = data.master_update || masterUpdateState;
    renderMasterUpdate();
  } catch (error) {
    alert(error.message);
    await loadMasterUpdate();
  }
}

async function loadMasterUpdate() {
  try {
    const response = await fetch("/api/master-update");
    const data = await response.json();
    if (response.ok) masterUpdateState = data.master_update || masterUpdateState;
  } catch {}
  renderMasterUpdate();
}

async function runMasterUpdate() {
  const button = document.getElementById("masterUpdateNowBtn");
  const scanRunning = Boolean(sportsState?.scan?.running);
  if (masterUpdateBusy || masterUpdateState.running || scanRunning) return;
  masterUpdateBusy = true;
  masterUpdateLocalStartedAt = Date.now();
  if (button) {
    button.disabled = true;
    button.textContent = "Updating…";
  }
  renderMasterUpdate();
  setStatus("Running master update…");
  try {
    const response = await fetch("/api/master-update/run", {method: "POST"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Master update failed.");
    masterUpdateState = data.master_update || masterUpdateState;
    if (data.sports) sportsState = data.sports;
    if (data.channels) channels = data.channels;
    if (data.selected_ids) selected = new Set(data.selected_ids.map(Number));
    applySportsState();
    render();
    await loadEpgSources();
    setStatus(data.result?.message || "Master update complete.");
  } catch (error) {
    setStatus("");
    alert(error.message);
  } finally {
    masterUpdateBusy = false;
    masterUpdateLocalStartedAt = 0;
    await loadMasterUpdate();
  }
}

async function savePublicEpgSelections() {
  const enabledCodes = [...document.querySelectorAll(".public-epg-country-check:checked")]
    .map(input => input.dataset.code);
  try {
    const response = await fetch("/api/public-epg", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled_codes: enabledCodes})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save public EPG selections.");
    publicEpgState = data.public_epg || publicEpgState;
    renderPublicEpg();
  } catch (error) {
    alert(error.message);
    await loadEpgSources();
  }
}

async function loadEpgSources() {
  try {
    const response = await fetch("/api/epg");
    const data = await response.json();
    epgSources = data.sources || [];
    epgBuiltins = data.builtins || [];
    publicEpgState = data.public_epg || publicEpgState;
    masterUpdateState = data.master_update || masterUpdateState;
  } catch {
    epgSources = [];
    epgBuiltins = [];
  }
  renderEpgSources();
  renderPublicEpg();
  renderMasterUpdate();
}

async function addEpgSource() {
  const nameInput = document.getElementById("epgName");
  const urlInput = document.getElementById("epgUrl");
  const addStatus = document.getElementById("epgAddStatus");
  const name = nameInput?.value.trim() || "";
  const url = urlInput?.value.trim() || "";
  if (!name) {
    nameInput?.focus();
    return;
  }
  if (!url) {
    urlInput?.focus();
    return;
  }
  if (addStatus) addStatus.textContent = "Validating…";
  setStatus("Adding EPG source...");
  const response = await fetch("/api/epg", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, url})
  });
  const data = await response.json();
  if (!response.ok) {
    if (addStatus) addStatus.textContent = data.error || "Could not add source";
    setStatus("");
    return alert(data.error || "Could not add EPG source.");
  }
  nameInput.value = "";
  urlInput.value = "";
  if (addStatus) addStatus.textContent = data.refreshed ? "Added" : "Saved; refresh pending";
  await loadEpgSources();
  window.setTimeout(() => { if (addStatus) addStatus.textContent = ""; }, 3500);
  setStatus(data.refreshed ? `Added and refreshed EPG source: ${name}` : `Saved EPG source: ${name}. Refresh failed; it will retry later.`);
}

async function deleteEpgSource(sourceId) {
  const response = await fetch(`/api/epg/${encodeURIComponent(sourceId)}`, {method: "DELETE"});
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not delete EPG source.");
  epgSources = data.sources || [];
  renderEpgSources();
  setStatus("Deleted EPG source.");
}

function formatElapsedSeconds(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds}s elapsed`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s elapsed`;
}

function updateProviderProgressPanel(payload = {}) {
  const panel = document.getElementById("providerOperationStatus");
  if (!panel) return;
  const stage = document.getElementById("providerOperationStage");
  const detail = document.getElementById("providerOperationDetail");
  const elapsed = document.getElementById("providerOperationElapsed");
  const spinner = document.getElementById("providerOperationSpinner");
  const channelCount = payload.channel_count;
  const detailParts = [];
  if (payload.detail) detailParts.push(payload.detail);
  if (channelCount !== null && channelCount !== undefined) {
    detailParts.push(`${Number(channelCount).toLocaleString()} live channels`);
  }
  stage.textContent = payload.stage || "Working…";
  detail.textContent = detailParts.join(" • ");
  elapsed.textContent = formatElapsedSeconds((Date.now() - providerOperationStartedAt) / 1000);
  panel.classList.remove("d-none", "is-complete", "is-failed");
  panel.classList.toggle("is-complete", payload.status === "complete");
  panel.classList.toggle("is-failed", payload.status === "failed");
  spinner.classList.toggle("d-none", payload.status === "complete" || payload.status === "failed");
}

async function pollProviderProgress() {
  if (!providerOperationBusy) return;
  providerProgressPollTick += 1;
  const elapsed = document.getElementById("providerOperationElapsed");
  if (elapsed) elapsed.textContent = formatElapsedSeconds((Date.now() - providerOperationStartedAt) / 1000);
  if (providerProgressPollTick % 2 !== 0) return;
  try {
    const response = await fetch("/api/providers/progress", {cache: "no-store"});
    if (response.ok) updateProviderProgressPanel(await response.json());
  } catch {
    // The elapsed clock still proves the browser is waiting even if a poll fails.
  }
}

function startProviderProgress(initialStage) {
  providerOperationBusy = true;
  providerOperationStartedAt = Date.now();
  providerProgressPollTick = 0;
  updateProviderProgressPanel({stage: initialStage, status: "running"});
  setSourceMode(currentSourceMode);
  renderProviderSources();
  clearInterval(providerProgressTimer);
  providerProgressTimer = setInterval(pollProviderProgress, 500);
  pollProviderProgress();
}

function finishProviderProgress(payload, {hideAfter = 5000} = {}) {
  providerOperationBusy = false;
  clearInterval(providerProgressTimer);
  providerProgressTimer = null;
  if (payload) updateProviderProgressPanel(payload);
  setSourceMode(currentSourceMode);
  renderProviderSources();
  if (hideAfter) {
    window.setTimeout(() => {
      const panel = document.getElementById("providerOperationStatus");
      if (panel && !providerOperationBusy) panel.classList.add("d-none");
    }, hideAfter);
  }
}

function renderProviderSources() {
  const list = document.getElementById("providerSources");
  if (!list) return;
  const addButton = document.getElementById("addFallbackBtn");
  const hasPrimary = providerSources.some(source => source.role === "primary");
  setSourceMode(currentSourceMode);
  if (addButton) addButton.disabled = !hasPrimary || providerOperationBusy;
  if (!providerSources.length) {
    list.innerHTML = `<tr><td colspan="7" class="small-muted">No URL provider loaded. Load a primary provider above.</td></tr>`;
    return;
  }
  list.innerHTML = providerSources.map(source => {
    const primary = source.role === "primary";
    const priority = primary ? "Primary" : `Fallback ${Number(source.priority || 1)}`;
    const kind = source.kind === "xtream"
      ? (source.xtream_api ? "Xtream API" : "Xtream-compatible")
      : "Direct M3U";
    const updated = source.last_refresh
      ? escapeHtml(formatEpgTimestamp(source.last_refresh))
      : source.cached ? "Cached" : "—";
    const operationalStatus = !primary && !hasPrimary
      ? `<span class="text-warning">Waiting for primary</span>`
      : source.last_error
      ? `<span class="text-warning">Refresh failed</span>`
      : source.deferred
        ? "Ready — loads during Master Update"
        : "Ready";
    const accountBits = [];
    if (source.kind === "xtream") {
      if (source.account_status) accountBits.push(escapeHtml(source.account_status));
      if (source.expires_at) accountBits.push(`Expires ${escapeHtml(formatProviderExpiry(source.expires_at))}`);
    }
    const accountStatus = accountBits.length
      ? `${operationalStatus !== "Ready" ? `${operationalStatus} • ` : ""}<span class="provider-account-status">${accountBits.join(" • ")}</span>`
      : operationalStatus;
    const title = source.last_error
      ? escapeHtml(source.last_error)
      : source.warning ? escapeHtml(source.warning) : "";
    return `
      <tr class="provider-source-row" data-id="${escapeHtml(source.id)}">
        <td><span class="badge ${primary ? "text-bg-primary" : "text-bg-secondary"}">${escapeHtml(priority)}</span></td>
        <td><strong>${escapeHtml(source.name || source.source_label || source.id)}</strong><div class="small-muted">${escapeHtml(source.source_label || "Provider")}</div></td>
        <td>${escapeHtml(kind)}</td>
        <td class="text-end">${Number(source.channel_count || 0).toLocaleString()}</td>
        <td class="small-muted">${updated}</td>
        <td class="small-muted" title="${title}">${accountStatus}</td>
        <td class="text-end">${primary
          ? `<button class="btn btn-outline-danger btn-sm provider-remove-primary-btn" type="button">Remove</button>`
          : `<button class="btn btn-outline-danger btn-sm provider-delete-btn" type="button">Remove</button>`}</td>
      </tr>`;
  }).join("");
}

async function loadProviderSources() {
  try {
    const response = await fetch("/api/providers");
    const data = await response.json();
    providerSources = data.sources || [];
  } catch {
    providerSources = [];
  }
  renderProviderSources();
  setSourceMode(currentSourceMode);
}

async function addFallbackProvider() {
  const nameInput = document.getElementById("fallbackName");
  const urlInput = document.getElementById("fallbackUrl");
  const usernameInput = document.getElementById("fallbackUsername");
  const passwordInput = document.getElementById("fallbackPassword");
  const name = nameInput.value.trim();
  const url = urlInput.value.trim();
  if (!name) return nameInput.focus();
  if (!url) return urlInput.focus();
  setStatus("Validating fallback provider...");
  startProviderProgress("Validating fallback provider…");
  let response;
  let data;
  try {
    response = await fetch("/api/providers/fallback", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        name,
        url,
        username: usernameInput.value,
        password: passwordInput.value
      })
    });
    data = await response.json();
  } catch (error) {
    finishProviderProgress({stage: "Provider request failed", detail: String(error), status: "failed"}, {hideAfter: 0});
    setStatus("");
    return alert("Could not contact the application while adding the fallback provider.");
  }
  if (!response.ok) {
    finishProviderProgress({stage: "Provider validation failed", detail: data.error || "Could not add fallback provider.", status: "failed"}, {hideAfter: 0});
    setStatus("");
    return alert(data.error || "Could not add fallback provider.");
  }
  providerSources = data.sources || [];
  nameInput.value = "";
  urlInput.value = "";
  usernameInput.value = "";
  passwordInput.value = "";
  renderProviderSources();
  finishProviderProgress({stage: "Fallback provider saved", detail: "Live channels will load only when Master Update runs.", channel_count: 0, status: "complete"});
  setStatus(`Added sports fallback provider: ${name}`);
}

async function deleteFallbackProvider(sourceId) {
  const response = await fetch(`/api/providers/${encodeURIComponent(sourceId)}`, {method: "DELETE"});
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not delete fallback provider.");
  providerSources = data.sources || [];
  renderProviderSources();
  setStatus("Deleted fallback provider.");
}

async function removePrimarySource() {
  const keepFallbacks = providerSources.some(source => source.role === "fallback");
  const message = keepFallbacks
    ? "Remove the primary provider? Fallback settings will be kept but remain inactive until a new primary is added."
    : "Remove the primary source?";
  if (!window.confirm(message)) return;

  setStatus("Removing primary source...");
  const response = await fetch("/api/providers/primary", {method: "DELETE"});
  const data = await response.json();
  if (!response.ok) {
    setStatus("");
    return alert(data.error || "Could not remove the primary source.");
  }

  providerSources = data.sources || [];
  applyChannelPayload(data);
  setSourceMode(data.source_mode || "");
  renderProviderSources();
  document.getElementById("m3uFile").value = "";
  await loadSports();
  setStatus("Primary source removed. You can now add a new primary.");
}

async function setActiveGroup(slug) {
  activeGroupSlug = slug || "";
  els.activeGroup.value = activeGroupSlug;
  updateGroupUrl();
  if (!activeGroupSlug) {
    activeGroupMembers = new Set();
    showGroupOnly = false;
    renderGroups();
    render();
    return;
  }
  const response = await fetch(`/api/groups/${activeGroupSlug}/channels`);
  const data = await response.json();
  activeGroupMembers = new Set(data.channel_keys || []);
  renderGroups();
  render();
}

async function createGroup() {
  const input = document.getElementById("newGroupName");
  const name = input.value.trim();
  if (!name) {
    input.focus();
    return;
  }
  const response = await fetch("/api/groups", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name})
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not create group.");
  input.value = "";
  await loadGroups();
  await setActiveGroup(data.group.slug);
  setStatus(`Created group: ${data.group.name}`);
}

async function addVisibleToGroup() {
  if (!activeGroupSlug) return alert("Choose or create a group first.");
  const keys = filteredChannels().filter(channel => !isGeneratedSportsChannel(channel)).map(channelKey).filter(Boolean);
  const response = await fetch(`/api/groups/${activeGroupSlug}/channels`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel_keys: keys})
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not add channels.");
  await setActiveGroup(activeGroupSlug);
  setStatus(`Added ${data.added} visible channels to group.`);
}

async function removeVisibleFromGroup() {
  if (!activeGroupSlug) return alert("Choose a group first.");
  const keys = filteredChannels().filter(channel => !isGeneratedSportsChannel(channel)).map(channelKey).filter(Boolean);
  const response = await fetch(`/api/groups/${activeGroupSlug}/channels`, {
    method: "DELETE",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel_keys: keys})
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not remove channels.");
  await setActiveGroup(activeGroupSlug);
  setStatus(`Removed ${data.removed} visible channels from group.`);
}

function applyChannelPayload(data) {
  channels = data.channels || [];
  selected = new Set((data.selected_ids || []).map(Number));
  rebuildProviderGroupFilter();
  render();
}

async function loadFromUrl() {
  if (currentSourceMode || providerSources.some(source => source.role === "primary")) {
    return alert("Remove the current primary source before adding another one.");
  }
  const url = document.getElementById("m3uUrl").value.trim();
  if (!url) {
    showUrlModal();
    return;
  }
  const name = document.getElementById("primaryName").value.trim() || "Primary";
  const username = document.getElementById("m3uUsername").value;
  const password = document.getElementById("m3uPassword").value;
  setStatus(username || password ? "Detecting Xtream provider..." : "Loading primary provider...");
  startProviderProgress(username || password ? "Probing Xtream provider…" : "Loading primary provider…");
  let response;
  let data;
  try {
    response = await fetch("/api/load-url", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, url, username, password})
    });
    data = await response.json();
  } catch (error) {
    finishProviderProgress({stage: "Provider request failed", detail: String(error), status: "failed"}, {hideAfter: 0});
    setStatus("");
    return alert("Could not contact the application while loading the provider.");
  }
  if (!response.ok) {
    finishProviderProgress({stage: "Provider load failed", detail: data.error || "URL load failed.", status: "failed"}, {hideAfter: 0});
    setStatus("");
    return alert(data.error || "URL load failed.");
  }
  applyChannelPayload(data);
  providerSources = data.providers || [];
  renderProviderSources();
  document.getElementById("m3uUrl").value = "";
  document.getElementById("m3uUsername").value = "";
  document.getElementById("m3uPassword").value = "";
  document.getElementById("modalUrlInput").value = "";
  document.getElementById("modalUsernameInput").value = "";
  document.getElementById("modalPasswordInput").value = "";
  setSourceMode("url");
  if (activeGroupSlug) await setActiveGroup(activeGroupSlug);
  await loadSports();
  finishProviderProgress({stage: "Primary provider loaded", detail: "Live-only channel catalog ready.", channel_count: channels.length, status: "complete"});
  setStatus(`Source loaded. ${channels.length} channels available.`);
}

async function uploadFile() {
  if (currentSourceMode || providerSources.some(source => source.role === "primary")) {
    return alert("Remove the current primary source before adding another one.");
  }
  const file = document.getElementById("m3uFile").files[0];
  if (!file) return alert("Choose an M3U file first.");
  setStatus("Uploading file...");
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/upload", {method: "POST", body: form});
  const data = await response.json();
  if (!response.ok) {
    setStatus("");
    return alert(data.error || "Upload failed.");
  }
  applyChannelPayload(data);
  providerSources = [];
  renderProviderSources();
  document.getElementById("m3uFile").value = "";
  setSourceMode("file");
  if (activeGroupSlug) await setActiveGroup(activeGroupSlug);
  await loadSports();
  setStatus(`Source loaded. ${channels.length} channels available.`);
}

async function loadInitialChannels({quiet = false} = {}) {
  try {
    const response = await fetch("/api/channels");
    const data = await response.json();
    applyChannelPayload(data);
    if (Array.isArray(data.providers)) {
      providerSources = data.providers;
      renderProviderSources();
    }
    setSourceMode(data.source_mode || currentSourceMode);
    if (!quiet && channels.length > 0) setStatus(`Loaded ${channels.length} cached channels.`);
  } catch {
    if (!quiet) setStatus("Could not load cached channels.");
  }
}

function scheduleSaveSelected() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSelected, 250);
}

async function saveSelected() {
  const manualIds = [...selected].filter(id => Number(id) >= 0);
  setStatus("Saving playlist...");
  const response = await fetch("/api/selection", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ids: manualIds})
  });
  const data = await response.json();
  if (!response.ok) {
    setStatus(data.error || "Save failed.");
    return;
  }
  setStatus("Playlist updated.");
}

els.table.addEventListener("change", event => {
  if (!event.target.classList.contains("channel-check") || event.target.disabled) return;
  const id = Number(event.target.dataset.id);
  if (event.target.checked) selected.add(id);
  else selected.delete(id);
  render();
  scheduleSaveSelected();
});

els.table.addEventListener("dblclick", event => {
  const row = event.target.closest("tr");
  if (!row) return;
  const id = Number(row.dataset.id);
  if (id < 0) return;
  if (selected.has(id)) selected.delete(id);
  else selected.add(id);
  render();
  scheduleSaveSelected();
});

document.getElementById("loadUrlBtn").addEventListener("click", loadFromUrl);
document.getElementById("uploadBtn").addEventListener("click", uploadFile);
document.getElementById("addFallbackBtn")?.addEventListener("click", addFallbackProvider);
document.getElementById("fallbackPassword")?.addEventListener("keydown", event => { if (event.key === "Enter") addFallbackProvider(); });
document.getElementById("providerSources")?.addEventListener("click", event => {
  const row = event.target.closest(".provider-source-row");
  if (row && event.target.classList.contains("provider-remove-primary-btn")) {
    removePrimarySource();
    return;
  }
  if (row && event.target.classList.contains("provider-delete-btn")) {
    deleteFallbackProvider(row.dataset.id);
  }
});
document.getElementById("copyPlaylistBtn").addEventListener("click", () => copyInputValue("playlistUrl", "copyPlaylistBtn"));
document.getElementById("copyGroupBtn").addEventListener("click", () => copyInputValue("groupPlaylistUrl", "copyGroupBtn"));
document.getElementById("copyEpgBtn")?.addEventListener("click", () => copyInputValue("epgOutputUrl", "copyEpgBtn"));
document.getElementById("addEpgBtn")?.addEventListener("click", addEpgSource);
document.getElementById("epgUrl")?.addEventListener("keydown", event => { if (event.key === "Enter") addEpgSource(); });
document.getElementById("epgSources")?.addEventListener("click", event => {
  const row = event.target.closest(".epg-source-row");
  if (!row) return;
  if (event.target.classList.contains("epg-delete-btn")) {
    deleteEpgSource(row.dataset.id);
    return;
  }
  if (event.target.classList.contains("epg-copy-btn")) {
    const input = row.querySelector(".epg-served-cell input");
    if (!input) return;
    input.select();
    try { navigator.clipboard.writeText(input.value); } catch {}
    document.execCommand("copy");
    event.target.textContent = "Copied!";
    setTimeout(() => { event.target.textContent = "Copy"; }, 1500);
  }
});
document.getElementById("modalLoadBtn").addEventListener("click", acceptModalUrl);
document.getElementById("modalUrlInput").addEventListener("keydown", event => { if (event.key === "Enter") acceptModalUrl(); });
document.getElementById("modalPasswordInput")?.addEventListener("keydown", event => { if (event.key === "Enter") acceptModalUrl(); });
document.getElementById("removeFilePrimaryBtn")?.addEventListener("click", removePrimarySource);
document.getElementById("createGroupBtn").addEventListener("click", createGroup);
document.getElementById("newGroupName").addEventListener("keydown", event => { if (event.key === "Enter") createGroup(); });
els.activeGroup.addEventListener("change", event => setActiveGroup(event.target.value));
document.getElementById("addVisibleToGroupBtn").addEventListener("click", addVisibleToGroup);
document.getElementById("removeVisibleFromGroupBtn").addEventListener("click", removeVisibleFromGroup);
document.getElementById("showGroupOnlyBtn").addEventListener("click", () => {
  showGroupOnly = activeGroupSlug ? !showGroupOnly : false;
  setStatus(showGroupOnly ? "Showing active group only." : "Showing all matching channels.");
  render();
});

document.getElementById("selectVisibleBtn").addEventListener("click", () => {
  const visible = filteredChannels().filter(channel => !isGeneratedSportsChannel(channel));
  for (const channel of visible) selected.add(Number(channel.id));
  render();
  scheduleSaveSelected();
  setStatus(`Added ${visible.length} channels from current search.`);
});

document.getElementById("clearVisibleBtn").addEventListener("click", () => {
  const removable = removableVisibleChannels();
  for (const channel of removable) selected.delete(Number(channel.id));
  render();
  scheduleSaveSelected();
  setStatus(`Removed ${removable.length} channels from current search.`);
});

document.getElementById("showSelectedBtn").addEventListener("click", () => {
  els.selectedOnly.checked = !els.selectedOnly.checked;
  render();
  setStatus(els.selectedOnly.checked ? "Showing saved channels only." : "Showing all channels.");
});

document.getElementById("groupPills").addEventListener("click", event => {
  const pill = event.target.closest(".group-pill");
  if (pill) setActiveGroup(pill.dataset.slug);
});

els.search.addEventListener("input", () => {
  updateClearSearchButton();
  render();
});
els.groupFilter.addEventListener("change", render);
els.selectedOnly.addEventListener("change", render);
els.excludeSdChannels?.addEventListener("change", () => {
  render();
  scheduleSportsSave({exclude_sd: els.excludeSdChannels.checked});
  setStatus(els.excludeSdChannels.checked
    ? "SD / LOW BANDWIDTH channels hidden, including sports-generated feeds."
    : "SD / LOW BANDWIDTH channels visible.");
});

document.getElementById("clearSearchBtn").addEventListener("click", () => {
  els.search.value = "";
  updateClearSearchButton();
  render();
  els.search.focus();
});

// Custom playlist order modal. Sports-generated channels have fixed numbers and
// intentionally do not appear here.
let orderChannels = [];
let orderSelectedKey = "";

function renderOrderTable() {
  const tbody = document.getElementById("orderTable");
  tbody.innerHTML = orderChannels.map((channel, index) => `
    <tr data-key="${escapeHtml(channel.key)}" class="${channel.key === orderSelectedKey ? "order-selected" : ""}">
      <td>${index + 1}</td>
      <td>${escapeHtml(channel.name || channel.url)}</td>
      <td>${escapeHtml(channel.group || "")}</td>
    </tr>`).join("");
}

async function openOrderModal() {
  const response = await fetch("/api/selection/order");
  const data = await response.json();
  orderChannels = data.channels || [];
  orderSelectedKey = "";
  renderOrderTable();
  new bootstrap.Modal(document.getElementById("orderModal")).show();
}

function moveSelectedOrder(direction) {
  if (!orderSelectedKey) return;
  const index = orderChannels.findIndex(channel => channel.key === orderSelectedKey);
  const next = index + direction;
  if (index < 0 || next < 0 || next >= orderChannels.length) return;
  const [item] = orderChannels.splice(index, 1);
  orderChannels.splice(next, 0, item);
  renderOrderTable();
}

async function saveOrder() {
  const response = await fetch("/api/selection/order", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({keys: orderChannels.map(channel => channel.key)})
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "Could not save order.");
  setStatus(`Saved manual order for ${data.count} channels.`);
  bootstrap.Modal.getInstance(document.getElementById("orderModal"))?.hide();
}

document.getElementById("manageOrderBtn").addEventListener("click", openOrderModal);
document.getElementById("orderTable").addEventListener("click", event => {
  const row = event.target.closest("tr");
  if (!row) return;
  orderSelectedKey = row.dataset.key;
  renderOrderTable();
});
document.getElementById("moveOrderUpBtn").addEventListener("click", () => moveSelectedOrder(-1));
document.getElementById("moveOrderDownBtn").addEventListener("click", () => moveSelectedOrder(1));
document.getElementById("saveOrderBtn").addEventListener("click", saveOrder);

// Sports automation ---------------------------------------------------------
let sportsState = {settings: {}, rules: [], catalog: [], generated: [], last_scan: null, scan: {running: false}, next_update: null, numbering: {blocks: []}, schedule_api: {}};
const SPORTS_COLLAPSE_KEY = "m3u-picker.sports-collapsed";
const SPORTS_SCAN_DISMISSED_KEY = "m3u-picker.sports-scan-dismissed";
let sportsCollapsed = localStorage.getItem(SPORTS_COLLAPSE_KEY) === "true";
let sportsModal = null;
let sportsSaveTimer = null;
let sportsStatusPollTimer = null;
let sportsScanPulseTimer = null;
let sportsScanDotCount = 1;
let pendingSportsChanges = {};
let sportsGeneratedSignature = "";
let pendingSportsSelections = new Set();

function sportsElement(id) {
  return document.getElementById(id);
}

function setSportsError(message = "") {
  const element = sportsElement("sportsSaveError");
  element.textContent = message;
  element.classList.toggle("d-none", !message);
}

function preferenceOptions(rule) {
  const common = [["best", "Best feed per game"], ["all", "Show all feeds"]];
  const options = rule.scope_type === "team"
    ? [["favorite", `${rule.display_name} feed first`], ["home", "Home feed first"], ["away", "Away feed first"], ["national", "National feed first"], ["all", "Show all feeds"]]
    : [...common, ["national", "National feed first"]];
  return options.map(([value, label]) =>
    `<option value="${value}" ${rule.feed_preference === value ? "selected" : ""}>${escapeHtml(label)}</option>`
  ).join("");
}

function renderSportsRules() {
  const target = sportsElement("sportsRules");
  if (!sportsState.rules.length) {
    target.innerHTML = `<div class="small-muted">No sports selected. Use Add selection to build the nightly rules.</div>`;
    return;
  }
  target.innerHTML = sportsState.rules.map(rule => `
    <div class="sports-rule" data-id="${rule.id}">
      <div><span class="sports-rule-type">${escapeHtml(rule.scope_type)}</span></div>
      <div>
        <strong>${escapeHtml(rule.display_name)}</strong>
        <div class="small-muted">${escapeHtml(sportsCatalogSubtitle(rule.scope_type, rule.scope_id))}</div>
      </div>
      <select class="form-select form-select-sm sports-rule-preference" aria-label="Feed preference for ${escapeHtml(rule.display_name)}">
        ${preferenceOptions(rule)}
      </select>
      <button type="button" class="btn btn-outline-danger btn-sm sports-rule-remove">Remove</button>
    </div>`).join("");
}

function sportsCatalogSubtitle(scopeType, scopeId) {
  const item = sportsState.catalog.find(entry => entry.scope_type === scopeType && entry.id === scopeId);
  return item?.subtitle || "";
}

function renderSportsPreview() {
  const target = sportsElement("sportsPreview");
  const rows = sportsState.generated || [];
  sportsElement("sportsPreviewCount").textContent = `${rows.length} channel${rows.length === 1 ? "" : "s"}`;
  if (!rows.length) {
    target.innerHTML = `<div class="small-muted">No generated sports channels yet. The daily Master Update or Update Now will populate this list.</div>`;
    return;
  }
  target.innerHTML = rows.map(row => `
    <div class="sports-preview-row">
      <div class="sports-preview-number">${escapeHtml(row.assigned_number)}</div>
      <div>
        <div class="fw-semibold">${escapeHtml(row.display_name)}</div>
        <div class="small-muted">${escapeHtml(row.subtitle || "")}</div>
      </div>
      <span class="badge text-bg-secondary">${escapeHtml(row.feed_type)}</span>
    </div>`).join("");
}

function formatNextUpdate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
}

function formatSportsClock(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return value || "—";
  const date = new Date(2000, 0, 1, Number(match[1]), Number(match[2]));
  return date.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
}


function formatScanDuration(startedAt, finishedAt = null) {
  const started = new Date(startedAt || "");
  const finished = finishedAt ? new Date(finishedAt) : new Date();
  if (Number.isNaN(started.getTime()) || Number.isNaN(finished.getTime())) return "";
  const seconds = Math.max(0, Math.floor((finished.getTime() - started.getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (!minutes) return `${remainder}s`;
  return `${minutes}m ${remainder}s`;
}

function scanResultSignature(scan) {
  return scan?.id ? String(scan.id) : "";
}

function renderSportsScanStatus() {
  const panel = sportsElement("sportsScanStatus");
  const title = sportsElement("sportsScanStatusTitle");
  const details = sportsElement("sportsScanStatusDetails");
  const dismiss = sportsElement("sportsScanStatusDismiss");
  const spinner = sportsElement("sportsScanStatusSpinner");
  if (!panel || !title || !details || !dismiss) return;

  const scan = sportsState.scan || {running: false};
  const lastScan = sportsState.last_scan;
  const masterRunning = Boolean(masterUpdateBusy || masterUpdateState.running);
  const running = Boolean(masterRunning || scan.running);
  panel.classList.remove("is-running", "is-success", "is-failed", "is-neutral");
  if (spinner) spinner.classList.toggle("d-none", !running);

  if (running) {
    panel.classList.remove("d-none");
    panel.classList.add("is-running");
    dismiss.classList.add("d-none");

    const scanTrigger = String(scan.trigger || "").trim();
    const trigger = masterUpdateBusy
      ? "manual"
      : String(masterUpdateState.trigger || scanTrigger || "scheduled").trim();
    const label = trigger === "scheduled" ? "Automatic update" : "Manual update";
    const cancellationRequested = String(scan.stage || "").toLowerCase().includes("cancellation requested");
    title.textContent = cancellationRequested ? "Cancelling update" : `${label} in progress`;

    const stage = scan.running
      ? (scan.stage || "Scanning and matching sports channels")
      : "Preparing sports scan";
    const elapsedSeconds = masterRunning
      ? masterUpdateElapsedSeconds()
      : (scan.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(scan.started_at)) / 1000)) : 0);
    details.textContent = [
      stage,
      `Elapsed ${formatElapsedSeconds(elapsedSeconds)}`
    ].filter(Boolean).join(" • ");
    return;
  }

  if (!lastScan) {
    panel.classList.add("d-none");
    return;
  }

  const dismissed = localStorage.getItem(SPORTS_SCAN_DISMISSED_KEY) || "";
  if (dismissed && dismissed === scanResultSignature(lastScan)) {
    panel.classList.add("d-none");
    return;
  }

  const status = String(lastScan.status || "").toLowerCase();
  panel.classList.remove("d-none");
  dismiss.classList.remove("d-none");
  const sportsDuration = formatScanDuration(lastScan.started_at, lastScan.finished_at);
  const masterFinished = masterUpdateState.last_update ? Date.parse(masterUpdateState.last_update) : NaN;
  const scanFinished = lastScan.finished_at ? Date.parse(lastScan.finished_at) : NaN;
  const sameMasterCycle = Number.isFinite(masterFinished) && Number.isFinite(scanFinished)
    && Math.abs(masterFinished - scanFinished) <= 5 * 60 * 1000;
  const masterDuration = sameMasterCycle && masterUpdateState.last_duration_seconds != null
    ? formatUpdateDuration(masterUpdateState.last_duration_seconds)
    : "";

  if (status === "success") {
    panel.classList.add("is-success");
    title.textContent = "Sports update complete";
    details.textContent = [
      `${lastScan.channel_count || 0} channels generated`,
      `${lastScan.event_count || 0} events matched`,
      lastScan.message || "",
      `Completed ${formatNextUpdate(lastScan.finished_at)}`,
      sportsDuration ? `Sports scan ${sportsDuration}` : "",
      masterDuration ? `Master update ${masterDuration}` : ""
    ].filter(Boolean).join(" • ");
  } else if (status === "failed") {
    panel.classList.add("is-failed");
    title.textContent = "Sports update failed";
    details.textContent = [
      lastScan.message || "Existing sports channels were kept.",
      `Finished ${formatNextUpdate(lastScan.finished_at)}`,
      sportsDuration ? `Sports scan ${sportsDuration}` : "",
      masterDuration ? `Master update ${masterDuration}` : ""
    ].filter(Boolean).join(" • ");
  } else if (status === "cancelled") {
    panel.classList.add("is-neutral");
    title.textContent = "Sports update cancelled";
    details.textContent = [
      lastScan.message || "Existing sports channels were kept.",
      `Stopped ${formatNextUpdate(lastScan.finished_at)}`,
      sportsDuration ? `Sports scan ${sportsDuration}` : "",
      masterDuration ? `Master update ${masterDuration}` : ""
    ].filter(Boolean).join(" • ");
  } else {
    panel.classList.add("is-neutral");
    title.textContent = "Sports update finished";
    details.textContent = [lastScan.message, formatNextUpdate(lastScan.finished_at)].filter(Boolean).join(" • ");
  }
}

function updateSportsScanPulse() {
  const running = Boolean(sportsState.scan?.running);
  if (running && !sportsScanPulseTimer) {
    sportsScanPulseTimer = setInterval(() => {
      sportsScanDotCount = sportsScanDotCount >= 3 ? 1 : sportsScanDotCount + 1;
      renderSportsScanStatus();
    }, 550);
  } else if (!running && sportsScanPulseTimer) {
    clearInterval(sportsScanPulseTimer);
    sportsScanPulseTimer = null;
    sportsScanDotCount = 1;
  }
  renderSportsScanStatus();
}


function scheduleApiStatusLabel(api) {
  if (api.effective) return "Active";
  if (api.configured) return "Disabled";
  return "Needs key";
}

function renderSportsScheduleApiList() {
  const target = sportsElement("sportsScheduleApiList");
  if (!target) return;
  const api = sportsState.schedule_api || {};
  const entries = Array.isArray(api.apis) ? api.apis : [];
  if (!entries.length) {
    target.innerHTML = `<tr><td colspan="6" class="small-muted">No API-backed schedule dataset is required by the current sports selections.</td></tr>`;
    return;
  }
  target.innerHTML = entries.map(entry => {
    const status = scheduleApiStatusLabel(entry);
    const updated = entry.last_fetch_at ? formatNextUpdate(entry.last_fetch_at) : "—";
    const cacheCount = Number(entry.cached_event_count || 0);
    return `
      <tr>
        <td>${escapeHtml(entry.provider || "API-SPORTS")}</td>
        <td>${escapeHtml(entry.product || "—")}</td>
        <td>${escapeHtml(entry.scope || "—")}</td>
        <td>${escapeHtml(status)}</td>
        <td>${escapeHtml(updated)}</td>
        <td>${cacheCount ? `${cacheCount.toLocaleString()} games` : "—"}</td>
      </tr>`;
  }).join("");
}

function renderSportsScheduleApiPlan(api) {
  const target = sportsElement("sportsScheduleApiPlan");
  if (!target) return;
  const plan = api.plan || {};
  const datasets = Array.isArray(plan.datasets) ? plan.datasets.map(item => item.label).filter(Boolean) : [];
  const legacy = Array.isArray(plan.legacy_rules) ? plan.legacy_rules.filter(Boolean) : [];
  const mixed = Array.isArray(plan.mixed_rules) ? plan.mixed_rules.filter(Boolean) : [];
  const bits = [];
  if (datasets.length) bits.push(`API-backed: ${datasets.join(", ")}`);
  if (legacy.length) bits.push(`Legacy only: ${legacy.join(", ")}`);
  if (mixed.length) bits.push(`Mixed/API + legacy: ${mixed.join(", ")}`);
  if (!bits.length) bits.push("No sports selections currently require API-SPORTS; legacy matching remains available.");
  target.textContent = bits.join(" • ");
}

function renderSportsScheduleApi() {
  const api = sportsState.schedule_api || {};
  const enabled = Boolean(api.enabled);
  const enabledInput = sportsElement("sportsScheduleApiEnabled");
  const fieldset = sportsElement("sportsScheduleApiFields");
  const keyInput = sportsElement("sportsScheduleApiKey");
  const status = sportsElement("sportsScheduleApiStatus");
  const refreshButton = sportsElement("sportsScheduleApiRefresh");
  if (!enabledInput || !fieldset || !keyInput || !status) return;
  enabledInput.checked = enabled;
  fieldset.disabled = !enabled;
  keyInput.value = "";
  keyInput.placeholder = api.key_configured ? "API-SPORTS key saved" : "Enter API-SPORTS key";
  if (refreshButton) refreshButton.disabled = !api.effective || !(api.plan?.datasets || []).length;

  if (!enabled) {
    status.textContent = api.configured
      ? "Schedule API disabled • legacy provider/EPG matching active."
      : "Legacy provider/EPG matching active.";
  } else if (!api.effective) {
    status.textContent = "Schedule API enabled • enter an API-SPORTS key, then save.";
  } else if (!(api.plan?.datasets || []).length) {
    status.textContent = "API-SPORTS configured • no selected sport currently requires an API-backed schedule • legacy matching active.";
  } else {
    const bits = ["API-SPORTS active"];
    if (api.last_fetch_at) bits.push(`Last fetch ${formatNextUpdate(api.last_fetch_at)}`);
    if (Number(api.cached_event_count || 0) > 0) bits.push(`${Number(api.cached_event_count).toLocaleString()} games cached`);
    if (api.remaining_quota !== null && api.remaining_quota !== undefined) bits.push(`${api.remaining_quota} requests remaining`);
    status.textContent = bits.join(" • ");
  }
  renderSportsScheduleApiPlan(api);
  renderSportsScheduleApiList();
}

async function patchSportsScheduleApi(payload, failureMessage) {
  const response = await fetch("/api/sports/schedule-api", {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || failureMessage);
  sportsState.schedule_api = data.schedule_api || {};
  return sportsState.schedule_api;
}

async function toggleSportsScheduleApiEnabled() {
  const input = sportsElement("sportsScheduleApiEnabled");
  if (!input) return;
  const desired = input.checked;
  input.disabled = true;
  const fieldset = sportsElement("sportsScheduleApiFields");
  if (fieldset) fieldset.disabled = !desired;
  try {
    await patchSportsScheduleApi({enabled: desired}, "Could not update schedule API state.");
    setSportsError("");
  } catch (error) {
    input.checked = !desired;
    setSportsError(`Could not update schedule API state. ${error.message}`);
  } finally {
    input.disabled = false;
    renderSportsScheduleApi();
  }
}

async function saveSportsScheduleApi() {
  const button = sportsElement("sportsScheduleApiSave");
  const enabled = Boolean(sportsElement("sportsScheduleApiEnabled")?.checked);
  const apiKey = sportsElement("sportsScheduleApiKey")?.value.trim() || "";
  if (!enabled) return;
  if (button) button.disabled = true;
  try {
    const payload = {};
    if (apiKey) payload.api_key = apiKey;
    await patchSportsScheduleApi(payload, "Could not save schedule API settings.");
    sportsElement("sportsScheduleApiKey").value = "";
    setSportsError("");
  } catch (error) {
    setSportsError(`Could not save schedule API settings. ${error.message}`);
  } finally {
    if (button) button.disabled = false;
    renderSportsScheduleApi();
  }
}

async function removeSportsScheduleApi() {
  const button = sportsElement("sportsScheduleApiRemove");
  if (button) button.disabled = true;
  try {
    await patchSportsScheduleApi(
      {enabled: false, clear_key: true},
      "Could not remove schedule API."
    );
    setSportsError("");
  } catch (error) {
    setSportsError(`Could not remove schedule API. ${error.message}`);
  } finally {
    if (button) button.disabled = false;
    renderSportsScheduleApi();
  }
}

async function forceSportsScheduleApiRefresh() {
  const button = sportsElement("sportsScheduleApiRefresh");
  if (button) {
    button.disabled = true;
    button.textContent = "Refreshing…";
  }
  try {
    const response = await fetch("/api/sports/schedule-api/refresh", {method: "POST"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not refresh schedule API.");
    sportsState.schedule_api = data.schedule_api || sportsState.schedule_api || {};
    const warning = data.result?.warning || "";
    setSportsError(warning ? `Schedule API refresh warning: ${warning}` : "");
  } catch (error) {
    setSportsError(`Could not refresh schedule API. ${error.message}`);
  } finally {
    if (button) button.textContent = "Force Schedule Refresh";
    renderSportsScheduleApi();
  }
}

function applySportsState() {
  const settings = sportsState.settings || {};
  const enabled = Boolean(settings.enabled);
  sportsElement("sportsEnabled").checked = enabled;
  const sportsBodyHidden = !enabled || sportsCollapsed;
  sportsElement("sportsBody").classList.toggle("d-none", sportsBodyHidden);
  const collapseButton = sportsElement("sportsCollapseBtn");
  collapseButton.disabled = !enabled;
  collapseButton.textContent = sportsBodyHidden ? "Expand" : "Collapse";
  collapseButton.setAttribute("aria-expanded", String(enabled && !sportsCollapsed));
  sportsElement("sportsEnabledBadge").textContent = enabled ? "Enabled" : "Off";
  sportsElement("sportsEnabledBadge").classList.toggle("text-bg-success", enabled);
  sportsElement("sportsEnabledBadge").classList.toggle("text-bg-secondary", !enabled);
  renderSportsScheduleApi();

  sportsElement("sportsStartChannel").value = settings.start_channel ?? 1000;
  sportsElement("sportsBlockSize").value = settings.channels_per_event ?? 10;
  sportsElement("sportsGroupTitle").value = settings.group_title || "Sports Today";
  sportsElement("sportsTimezone").value = settings.timezone || "America/New_York";
  sportsElement("sportsEventWindow").value = settings.event_window || "today";
  sportsElement("sportsIncludeReplays").checked = Boolean(settings.include_replays);
  sportsElement("sportsIncludePregame").checked = Boolean(settings.include_pregame);
  sportsElement("sportsUseBackups").checked = Boolean(settings.use_backup_feeds);
  sportsElement("sportsEverythingMode").checked = Boolean(settings.everything_mode);
  if (els.excludeSdChannels) els.excludeSdChannels.checked = Boolean(settings.exclude_sd);
  renderMasterUpdate();

  const numbering = sportsState.numbering || {};
  const capacity = Number(numbering.events_per_primary_block || 0);
  sportsElement("sportsBlockCapacity").textContent = capacity
    ? `Each league gets 1,000 channels: ${capacity} event slots at ${settings.channels_per_event || 10} channels per event. Overflow uses a separate continuation block.`
    : "Each league gets its own 1,000-channel block.";
  renderSportsBlockMap();

  const conflictCount = Number(sportsState.number_conflicts || 0);
  const numberingAdjustment = sportsState.numbering_adjustment || {};
  const effectiveStart = Number(numberingAdjustment.effective_start_channel || settings.start_channel || 1000);
  const configuredStart = Number(numberingAdjustment.configured_start_channel || settings.start_channel || 1000);
  const warning = sportsElement("sportsNumberWarning");
  warning.classList.toggle("d-none", conflictCount === 0);
  warning.textContent = conflictCount
    ? `Configured sports start ${configuredStart} overlaps ${conflictCount} manual channel${conflictCount === 1 ? "" : "s"}. Generated sports channels will start at ${effectiveStart} automatically so Jellyfin receives unique channel numbers.`
    : "";

  const generatedCount = (sportsState.generated || []).length;
  const cachedCount = Number(sportsState.disabled_cache?.count || 0);
  sportsElement("sportsHeaderSummary").textContent = enabled
    ? `${settings.everything_mode ? "Everything mode" : `${sportsState.rules.length} selection${sportsState.rules.length === 1 ? "" : "s"}`} • ${generatedCount} generated channel${generatedCount === 1 ? "" : "s"}`
    : cachedCount
      ? `Sports channels hidden • ${cachedCount} cached for 24-hour recovery`
      : "Automatically build a scheduled sports channel block.";

  const everythingNotice = sportsElement("sportsEverythingModeNotice");
  everythingNotice.classList.toggle("d-none", !settings.everything_mode);
  everythingNotice.textContent = settings.everything_mode
    ? `Everything Mode is active. Your ${sportsState.rules.length} curated selection${sportsState.rules.length === 1 ? " is" : "s are"} safely preserved. Scans may take for-fucking-ever.`
    : "";

  sportsElement("sportsNextUpdate").textContent = "Runs with the Master Update cycle.";

  const lastScan = sportsState.last_scan;
  sportsElement("sportsLastScan").textContent = lastScan
    ? `Last scan: ${lastScan.status} • ${lastScan.channel_count} channels • ${formatNextUpdate(lastScan.finished_at)}`
    : "";

  renderSportsRules();
  renderSportsPreview();
  updateSportsScanPulse();
  sportsGeneratedSignature = JSON.stringify((sportsState.generated || []).map(row => [row.id, row.generated_at, row.assigned_number]));
}

async function loadSports({quiet = false} = {}) {
  try {
    const response = await fetch("/api/sports/settings");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load sports settings.");
    sportsState = data;
    setSportsError("");
    applySportsState();
  } catch (error) {
    if (!quiet) setSportsError(error.message);
  }
}

function scheduleSportsSave(changes) {
  Object.assign(sportsState.settings, changes);
  Object.assign(pendingSportsChanges, changes);
  clearTimeout(sportsSaveTimer);
  sportsSaveTimer = setTimeout(saveSportsSettings, 650);
}

async function saveSportsSettings() {
  const changes = {...pendingSportsChanges};
  pendingSportsChanges = {};
  if (!Object.keys(changes).length) return true;
  try {
    const response = await fetch("/api/sports/settings", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(changes)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save sports settings.");
    sportsState = data;
    setSportsError("");
    applySportsState();
    if (Object.prototype.hasOwnProperty.call(changes, "enabled")) {
      await loadInitialChannels({quiet: true});
    }
    return true;
  } catch (error) {
    Object.assign(pendingSportsChanges, changes);
    setSportsError(`Could not save sports settings. ${error.message}`);
    return false;
  }
}

function sportsFamily(item) {
  const metadata = item?.metadata || {};
  if (metadata.family) return String(metadata.family);
  const sportId = metadata.sport_id || item?.sport_id || "";
  const sportItem = sportsState.catalog.find(entry => entry.scope_type === "sport" && entry.id === sportId);
  if (sportItem?.name) return sportItem.name;
  if (item?.scope_type === "sport") return item.name || "Other";
  return "Other";
}

function sportsBlockForId(scopeId) {
  return (sportsState.numbering?.blocks || []).find(block => block.id === scopeId) || null;
}

function sportsBlockRange(scopeId) {
  const block = sportsBlockForId(scopeId);
  return block ? `${block.start}–${block.end}` : "";
}

function renderSportsBlockMap() {
  const filter = sportsElement("sportsBlockSportFilter");
  const target = sportsElement("sportsBlockMap");
  if (!filter || !target) return;
  const blocks = sportsState.numbering?.blocks || [];
  const families = [...new Set(blocks.map(block => block.sport).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const current = filter.value;
  filter.innerHTML = `<option value="">All sports</option>` +
    families.map(family => `<option value="${escapeHtml(family)}">${escapeHtml(family)}</option>`).join("");
  if (families.includes(current)) filter.value = current;

  const selectedFamily = filter.value;
  const visible = blocks.filter(block => !selectedFamily || block.sport === selectedFamily);
  let lastFamily = "";
  target.innerHTML = visible.map(block => {
    const familyHeader = block.sport !== lastFamily
      ? `<div class="sports-block-family">${escapeHtml(block.sport)}</div>`
      : "";
    lastFamily = block.sport;
    return `${familyHeader}<div class="sports-block-item">
      <span class="sports-block-item-name" title="${escapeHtml(block.name)}">${escapeHtml(block.name)}</span>
      <span class="sports-block-range">${escapeHtml(block.start)}–${escapeHtml(block.end)}</span>
    </div>`;
  }).join("") || `<div class="small-muted">No channel blocks match this sport.</div>`;
}


function updateSportsSelectionSportOptions() {
  const type = sportsElement("sportsSelectionType").value;
  const current = sportsElement("sportsSelectionSport").value;
  const families = [...new Set(
    sportsState.catalog
      .filter(item => item.scope_type === type)
      .map(sportsFamily)
  )].sort((a, b) => a.localeCompare(b));
  sportsElement("sportsSelectionSport").innerHTML =
    `<option value="">All sports</option>` +
    families.map(family => `<option value="${escapeHtml(family)}">${escapeHtml(family)}</option>`).join("");
  if (families.includes(current)) sportsElement("sportsSelectionSport").value = current;
}

function updateSportsAddSelectedButton() {
  const button = sportsElement("sportsAddSelectedBtn");
  const count = pendingSportsSelections.size;
  button.disabled = count === 0;
  button.textContent = count ? `Add ${count} selected` : "Add selected";
}

function renderSportsSelectionResults() {
  const type = sportsElement("sportsSelectionType").value;
  const query = sportsElement("sportsSelectionSearch").value.trim().toLowerCase();
  const family = sportsElement("sportsSelectionSport").value;
  const existing = new Set(sportsState.rules.map(rule => `${rule.scope_type}:${rule.scope_id}`));
  const items = sportsState.catalog.filter(item => {
    if (item.scope_type !== type) return false;
    if (family && sportsFamily(item) !== family) return false;
    const haystack = `${item.name} ${item.subtitle} ${item.league_id || ""} ${(item.aliases || []).join(" ")}`.toLowerCase();
    return !query || haystack.includes(query);
  }).sort((a, b) => {
    const familyCompare = sportsFamily(a).localeCompare(sportsFamily(b));
    return familyCompare || String(a.name).localeCompare(String(b.name));
  });

  const placeholder = type === "team"
    ? "Search teams…"
    : type === "league"
      ? "Search leagues…"
      : type === "conference"
        ? "Search conferences…"
        : "Search sports…";
  sportsElement("sportsSelectionSearch").placeholder = placeholder;

  let lastFamily = "";
  const results = sportsElement("sportsSelectionResults");
  results.classList.toggle("has-range-header", type === "league");
  const rangeHeader = type === "league"
    ? `<div class="sports-selection-column-header"><span>Channel Range</span></div>`
    : "";
  const resultRows = items.map(item => {
    const key = `${item.scope_type}:${item.id}`;
    const added = existing.has(key);
    const checked = pendingSportsSelections.has(key);
    const itemFamily = sportsFamily(item);
    const familyHeader = itemFamily !== lastFamily
      ? `<div class="sports-selection-family">${escapeHtml(itemFamily)}</div>`
      : "";
    lastFamily = itemFamily;
    const logo = item.logo_url
      ? `<img class="sports-selection-logo" src="${escapeHtml(item.logo_url)}" alt="" loading="lazy">`
      : `<span class="sports-selection-logo sports-selection-logo-fallback" aria-hidden="true">${escapeHtml((item.name || "?").slice(0, 1).toUpperCase())}</span>`;
    const range = item.scope_type === "league" ? sportsBlockRange(item.id) : "";
    return `${familyHeader}
      <label class="sports-selection-result ${added ? "is-added" : ""}" role="listitem">
        <input class="form-check-input sports-selection-check" type="checkbox"
          data-key="${escapeHtml(key)}" ${checked ? "checked" : ""} ${added ? "disabled" : ""}>
        ${logo}
        <span class="sports-selection-copy">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="small-muted">${escapeHtml(item.subtitle || itemFamily)}</span>
        </span>
        ${range ? `<span class="sports-selection-range">${escapeHtml(range)}</span>` : added ? '<span class="badge text-bg-secondary">Added</span>' : '<span class="sports-selection-plus" aria-hidden="true">+</span>'}
      </label>`;
  }).join("");
  results.innerHTML = rangeHeader + (resultRows || `<div class="small-muted p-3">No matches.</div>`);
  updateSportsAddSelectedButton();
}


async function addSelectedSportsRules() {
  const selectedItems = [...pendingSportsSelections].map(key => {
    const [scopeType, ...idParts] = key.split(":");
    const scopeId = idParts.join(":");
    const item = sportsState.catalog.find(entry => entry.scope_type === scopeType && entry.id === scopeId);
    return item ? {
      scope_type: item.scope_type,
      scope_id: item.id,
      feed_preference: item.scope_type === "team" ? "favorite" : "best"
    } : null;
  }).filter(Boolean);
  if (!selectedItems.length) return;

  const button = sportsElement("sportsAddSelectedBtn");
  button.disabled = true;
  button.textContent = "Adding…";
  try {
    const response = await fetch("/api/sports/rules", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({items: selectedItems})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not add sports selections.");
    sportsState.rules = data.rules;
    pendingSportsSelections.clear();
    setSportsError("");
    bootstrap.Modal.getInstance(sportsElement("sportsSelectionModal"))?.hide();
    applySportsState();
  } catch (error) {
    setSportsError(error.message);
    updateSportsAddSelectedButton();
  }
}


async function updateSportsRule(ruleId, changes) {
  const response = await fetch(`/api/sports/rules/${ruleId}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(changes)
  });
  const data = await response.json();
  if (!response.ok) return setSportsError(data.error || "Could not update sports selection.");
  sportsState.rules = data.rules;
  setSportsError("");
  renderSportsRules();
}

async function deleteSportsRule(ruleId) {
  const response = await fetch(`/api/sports/rules/${ruleId}`, {method: "DELETE"});
  const data = await response.json();
  if (!response.ok) return setSportsError(data.error || "Could not remove sports selection.");
  sportsState.rules = data.rules;
  setSportsError("");
  renderSportsRules();
  applySportsState();
}

async function cancelSportsScan() {
  setSportsError("");
  const button = document.getElementById("masterUpdateNowBtn");
  if (button) {
    button.disabled = true;
    button.textContent = "Cancelling…";
  }
  try {
    const response = await fetch("/api/sports/scan/cancel", {method: "POST"});
    const data = await response.json();
    if (data.sports) sportsState = data.sports;
    if (!response.ok) throw new Error(data.error || data.message || "Could not cancel the scan.");
    setStatus(data.message || "Cancellation requested.");
    applySportsState();
    scheduleSportsStatusPoll(500);
  } catch (error) {
    setSportsError(error.message);
    await pollSportsStatus({reschedule: false});
  }
}

async function handleSportsScanAction() {
  if (sportsState.scan?.running) {
    await cancelSportsScan();
  } else {
    await runSportsScan();
  }
}

async function runSportsScan() {
  clearTimeout(sportsSaveTimer);
  const settingsSaved = await saveSportsSettings();
  if (!settingsSaved) return;

  sportsState.scan = {
    running: true,
    started_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    stage: "Starting sports update",
    trigger: "manual"
  };
  applySportsState();
  scheduleSportsStatusPoll(1000);
  setSportsError("");
  try {
    const response = await fetch("/api/sports/scan", {method: "POST"});
    const data = await response.json();
    if (!response.ok) {
      if (data.sports) {
        sportsState = data.sports;
        applySportsState();
      }
      throw new Error(data.error || "Sports scan failed.");
    }
    sportsState = data.sports;
    applyChannelPayload(data);
    applySportsState();
    setStatus(data.result.message || `Generated ${data.result.count} sports channels.`);
  } catch (error) {
    await pollSportsStatus({reschedule: false});
    const cancelled = String(sportsState.last_scan?.status || "").toLowerCase() === "cancelled";
    if (!sportsState.scan?.running && !cancelled) setSportsError(error.message);
  } finally {
    scheduleSportsStatusPoll();
  }
}

function scheduleSportsStatusPoll(delay = null) {
  clearTimeout(sportsStatusPollTimer);
  const resolvedDelay = delay ?? (sportsState.scan?.running ? 3000 : 30000);
  sportsStatusPollTimer = setTimeout(() => pollSportsStatus(), resolvedDelay);
}

async function pollSportsStatus({reschedule = true} = {}) {
  try {
    const response = await fetch("/api/sports/status");
    const data = await response.json();
    if (!response.ok) return;
    const signature = JSON.stringify((data.generated || []).map(row => [row.id, row.generated_at, row.assigned_number]));
    const changed = sportsGeneratedSignature && signature !== sportsGeneratedSignature;
    sportsState = data;
    applySportsState();
    if (data.scan?.running || masterUpdateState.running) await loadMasterUpdate();
    else renderMasterUpdate();
    if (changed) await loadInitialChannels({quiet: true});
  } catch {
    // Silent polling failure. User-facing errors are reserved for explicit actions.
  } finally {
    if (reschedule) scheduleSportsStatusPoll();
  }
}

function bindSports() {
  if (!sportsElement("sportsEnabled")) return;
  sportsModal = new bootstrap.Modal(sportsElement("sportsSelectionModal"));

  sportsElement("sportsEnabled").addEventListener("change", event => {
    const enabled = event.target.checked;
    sportsCollapsed = !enabled;
    localStorage.setItem(SPORTS_COLLAPSE_KEY, String(sportsCollapsed));
    sportsElement("sportsBody").classList.toggle("d-none", !enabled);
    scheduleSportsSave({enabled});
  });
  sportsElement("sportsCollapseBtn").addEventListener("click", () => {
    if (!sportsState.settings?.enabled) return;
    sportsCollapsed = !sportsCollapsed;
    localStorage.setItem(SPORTS_COLLAPSE_KEY, String(sportsCollapsed));
    applySportsState();
  });
  sportsElement("sportsBlockSportFilter").addEventListener("change", renderSportsBlockMap);
  sportsElement("sportsStartChannel").addEventListener("input", event => scheduleSportsSave({start_channel: Number(event.target.value)}));
  sportsElement("sportsBlockSize").addEventListener("input", event => scheduleSportsSave({channels_per_event: Number(event.target.value)}));
  sportsElement("sportsGroupTitle").addEventListener("input", event => scheduleSportsSave({group_title: event.target.value}));
  sportsElement("sportsTimezone").addEventListener("change", event => scheduleSportsSave({timezone: event.target.value}));
  sportsElement("sportsEventWindow").addEventListener("change", event => scheduleSportsSave({event_window: event.target.value}));
  sportsElement("sportsIncludeReplays").addEventListener("change", event => scheduleSportsSave({include_replays: event.target.checked}));
  sportsElement("sportsIncludePregame").addEventListener("change", event => scheduleSportsSave({include_pregame: event.target.checked}));
  sportsElement("sportsUseBackups").addEventListener("change", event => scheduleSportsSave({use_backup_feeds: event.target.checked}));
  sportsElement("sportsEverythingMode").addEventListener("change", event => {
    scheduleSportsSave({everything_mode: event.target.checked});
  });
  sportsElement("sportsScanStatusDismiss").addEventListener("click", () => {
    const signature = scanResultSignature(sportsState.last_scan);
    if (signature) localStorage.setItem(SPORTS_SCAN_DISMISSED_KEY, signature);
    renderSportsScanStatus();
  });

  sportsElement("sportsScheduleApiEnabled")?.addEventListener("change", toggleSportsScheduleApiEnabled);
  sportsElement("sportsScheduleApiSave")?.addEventListener("click", saveSportsScheduleApi);
  sportsElement("sportsScheduleApiRemove")?.addEventListener("click", removeSportsScheduleApi);
  sportsElement("sportsScheduleApiRefresh")?.addEventListener("click", forceSportsScheduleApiRefresh);

  sportsElement("sportsAddSelectionBtn").addEventListener("click", () => {
    pendingSportsSelections.clear();
    sportsElement("sportsEverythingMode").checked = Boolean(sportsState.settings?.everything_mode);
    sportsElement("sportsSelectionType").value = "league";
    sportsElement("sportsSelectionSearch").value = "";
    updateSportsSelectionSportOptions();
    renderSportsSelectionResults();
    sportsModal.show();
  });
  sportsElement("sportsSelectionType").addEventListener("change", () => {
    sportsElement("sportsSelectionSearch").value = "";
    updateSportsSelectionSportOptions();
    renderSportsSelectionResults();
  });
  sportsElement("sportsSelectionSearch").addEventListener("input", renderSportsSelectionResults);
  sportsElement("sportsSelectionSport").addEventListener("change", renderSportsSelectionResults);
  sportsElement("sportsSelectionResults").addEventListener("change", event => {
    const checkbox = event.target.closest(".sports-selection-check");
    if (!checkbox) return;
    if (checkbox.checked) pendingSportsSelections.add(checkbox.dataset.key);
    else pendingSportsSelections.delete(checkbox.dataset.key);
    updateSportsAddSelectedButton();
  });
  sportsElement("sportsAddSelectedBtn").addEventListener("click", addSelectedSportsRules);
  sportsElement("sportsSelectionModal").addEventListener("hidden.bs.modal", () => {
    pendingSportsSelections.clear();
    updateSportsAddSelectedButton();
  });
  sportsElement("sportsRules").addEventListener("change", event => {
    const row = event.target.closest(".sports-rule");
    if (row && event.target.classList.contains("sports-rule-preference")) {
      updateSportsRule(Number(row.dataset.id), {feed_preference: event.target.value});
    }
  });
  sportsElement("sportsRules").addEventListener("click", event => {
    const row = event.target.closest(".sports-rule");
    if (row && event.target.classList.contains("sports-rule-remove")) {
      deleteSportsRule(Number(row.dataset.id));
    }
  });
}

async function initialize() {
  // Load primary state deterministically. The former Promise.all allowed the
  // channel and provider requests to race while both changed the source-form
  // lock, which could leave only part of the primary form disabled.
  await loadInitialChannels();
  await loadProviderSources();
  await Promise.all([loadGroups(), loadEpgSources(), loadSports(), loadMasterUpdate()]);
  setSourceMode(currentSourceMode);
  render();
  updateClearSearchButton();
  scheduleSportsStatusPoll();
}

document.getElementById("tvGuideLink")?.addEventListener("click", event => {
  event.preventDefault();
  const href = event.currentTarget.href;
  // Ask Chrome for a normal browser window instead of the stripped-down
  // popup chrome used in exp1. This behaves better when moving the guide
  // between macOS displays while still requesting a useful initial size.
  const guideWindow = window.open(
    href,
    "m3u-web-picker-guide",
    "width=1280,height=860,resizable=yes,scrollbars=yes,toolbar=yes,location=yes,menubar=yes,status=yes"
  );
  if (guideWindow) guideWindow.focus();
  else window.open(href, "_blank", "noopener");
});

document.getElementById("masterUpdateEnabled")?.addEventListener("change", event => {
  saveMasterUpdate({enabled: event.target.checked});
});
document.getElementById("masterUpdateTime")?.addEventListener("change", event => {
  if (event.target.value) saveMasterUpdate({time: event.target.value});
});
document.getElementById("masterUpdateNowBtn")?.addEventListener("click", runMasterUpdate);
document.getElementById("publicEpgCountries")?.addEventListener("change", event => {
  if (event.target.classList.contains("public-epg-country-check")) savePublicEpgSelections();
});

bindSports();
initialize();
