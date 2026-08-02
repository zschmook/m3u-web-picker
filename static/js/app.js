let channels = [];
let selected = new Set();
let saveTimer = null;
let customGroups = [];
let activeGroupSlug = "";
let activeGroupMembers = new Set();
let showGroupOnly = false;

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

els.playlistUrl.value = `${location.origin}/playlist/custom.m3u`;
els.groupPlaylistUrl.value = `${location.origin}/playlist/all.m3u`;

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
  return String(channel.url || "").trim();
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
  const urlInput = document.getElementById("m3uUrl");
  const urlButton = document.getElementById("loadUrlBtn");
  const fileInput = document.getElementById("m3uFile");
  const fileButton = document.getElementById("uploadBtn");
  const label = document.getElementById("sourceModeLabel");

  if (mode === "url") {
    fileInput.disabled = true;
    fileButton.disabled = true;
    urlInput.disabled = false;
    urlButton.disabled = false;
    label.textContent = "Source loaded.";
  } else if (mode === "file") {
    urlInput.disabled = true;
    urlButton.disabled = true;
    fileInput.disabled = false;
    fileButton.disabled = false;
    label.textContent = "Source loaded.";
  } else {
    urlInput.disabled = false;
    urlButton.disabled = false;
    fileInput.disabled = false;
    fileButton.disabled = false;
    label.textContent = "";
  }
}

function showUrlModal() {
  const modalElement = document.getElementById("urlModal");
  const input = document.getElementById("modalUrlInput");
  input.value = "";
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
  bootstrap.Modal.getInstance(modalElement)?.hide();
  loadFromUrl();
}

function filteredChannels() {
  const query = els.search.value.trim().toLowerCase();
  const group = els.groupFilter.value;
  const selectedOnly = els.selectedOnly.checked;
  const excludeSd = Boolean(els.excludeSdChannels?.checked);

  return channels.filter(channel => {
    if (excludeSd && String(channel.group || "").trim().toUpperCase() === "LOW BANDWIDTH") return false;
    if (group && channel.group !== group) return false;
    if (selectedOnly && !selected.has(Number(channel.id))) return false;
    if (showGroupOnly && activeGroupSlug && !activeGroupMembers.has(channelKey(channel))) return false;
    if (query) {
      const haystack = `${channel.name} ${channel.group} ${channel.url} ${channel.sports_subtitle || ""}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
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

  if (selectButton) selectButton.textContent = `Add all ${manualVisible.length}`;
  if (clearButton) clearButton.textContent = `Remove all ${manualVisible.length}`;
  if (showSelectedButton) {
    showSelectedButton.textContent = els.selectedOnly.checked
      ? `Show all (${selected.size} saved)`
      : `Saved ${selected.size} channels`;
  }

  const savedMode = els.selectedOnly.checked;
  if (selectButton) selectButton.disabled = savedMode || manualVisible.length === 0;
  if (clearButton) clearButton.disabled = savedMode || manualVisible.length === 0;
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
  const url = document.getElementById("m3uUrl").value.trim();
  if (!url) {
    showUrlModal();
    return;
  }
  setStatus("Loading URL...");
  const response = await fetch("/api/load-url", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url})
  });
  const data = await response.json();
  if (!response.ok) {
    setStatus("");
    return alert(data.error || "URL load failed.");
  }
  applyChannelPayload(data);
  document.getElementById("m3uUrl").value = "";
  document.getElementById("modalUrlInput").value = "";
  setSourceMode("url");
  if (activeGroupSlug) await setActiveGroup(activeGroupSlug);
  await loadSports();
  setStatus(`Source loaded. ${channels.length} channels available.`);
}

async function uploadFile() {
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
    if (data.source_mode) setSourceMode(data.source_mode);
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
document.getElementById("copyPlaylistBtn").addEventListener("click", () => copyInputValue("playlistUrl", "copyPlaylistBtn"));
document.getElementById("copyGroupBtn").addEventListener("click", () => copyInputValue("groupPlaylistUrl", "copyGroupBtn"));
document.getElementById("modalLoadBtn").addEventListener("click", acceptModalUrl);
document.getElementById("modalUrlInput").addEventListener("keydown", event => { if (event.key === "Enter") acceptModalUrl(); });
document.getElementById("changeSourceBtn").addEventListener("click", () => { setSourceMode(""); setStatus("Source unlocked."); });
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
  const visible = filteredChannels().filter(channel => !isGeneratedSportsChannel(channel));
  for (const channel of visible) selected.delete(Number(channel.id));
  render();
  scheduleSaveSelected();
  setStatus(`Removed ${visible.length} channels from current search.`);
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
let sportsState = {settings: {}, rules: [], catalog: [], generated: [], last_scan: null, scan: {running: false}, next_update: null, numbering: {blocks: []}};
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
    target.innerHTML = `<div class="small-muted">No generated sports channels yet. The nightly update or Update now will populate this list.</div>`;
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
  if (!panel || !title || !details || !dismiss) return;

  const scan = sportsState.scan || {running: false};
  const lastScan = sportsState.last_scan;
  panel.classList.remove("is-running", "is-success", "is-failed", "is-neutral");

  if (scan.running) {
    panel.classList.remove("d-none");
    panel.classList.add("is-running");
    dismiss.classList.add("d-none");
    title.textContent = `Scanning and matching channels${".".repeat(sportsScanDotCount)}`;
    const stage = scan.stage || "Scanning and matching channels";
    const started = scan.started_at ? `Started ${formatNextUpdate(scan.started_at)}` : "";
    const elapsed = scan.started_at ? `Elapsed ${formatScanDuration(scan.started_at)}` : "";
    details.textContent = [stage, started, elapsed].filter(Boolean).join(" • ");
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
  if (status === "success") {
    panel.classList.add("is-success");
    title.textContent = "Sports update complete";
    const duration = formatScanDuration(lastScan.started_at, lastScan.finished_at);
    details.textContent = [
      `${lastScan.channel_count || 0} channels generated`,
      `${lastScan.event_count || 0} events matched`,
      `Completed ${formatNextUpdate(lastScan.finished_at)}`,
      duration ? `Duration ${duration}` : ""
    ].filter(Boolean).join(" • ");
  } else if (status === "failed") {
    panel.classList.add("is-failed");
    title.textContent = "Sports update failed";
    const duration = formatScanDuration(lastScan.started_at, lastScan.finished_at);
    details.textContent = [
      lastScan.message || "Existing sports channels were kept.",
      `Finished ${formatNextUpdate(lastScan.finished_at)}`,
      duration ? `Duration ${duration}` : ""
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

  sportsElement("sportsStartChannel").value = settings.start_channel ?? 1000;
  sportsElement("sportsBlockSize").value = settings.channels_per_event ?? 10;
  sportsElement("sportsGroupTitle").value = settings.group_title || "Sports Today";
  sportsElement("sportsTimezone").value = settings.timezone || "America/New_York";
  sportsElement("sportsRefreshTime").value = settings.refresh_time || "03:00";
  sportsElement("sportsEventWindow").value = settings.event_window || "today";
  sportsElement("sportsIncludeReplays").checked = Boolean(settings.include_replays);
  sportsElement("sportsIncludePregame").checked = Boolean(settings.include_pregame);
  sportsElement("sportsUseBackups").checked = Boolean(settings.use_backup_feeds);
  sportsElement("sportsEverythingMode").checked = Boolean(settings.everything_mode);
  if (els.excludeSdChannels) els.excludeSdChannels.checked = Boolean(settings.exclude_sd);
  sportsElement("sportsAutoUpdate").checked = Boolean(settings.auto_update);
  sportsElement("sportsAutoUpdate").disabled = !enabled;
  const scanRunning = Boolean(sportsState.scan?.running);
  const scanButton = sportsElement("sportsRunScanBtn");
  scanButton.disabled = !enabled || scanRunning;
  scanButton.setAttribute("aria-busy", String(scanRunning));
  scanButton.textContent = scanRunning ? "Scanning…" : "Update now";

  const numbering = sportsState.numbering || {};
  const capacity = Number(numbering.events_per_primary_block || 0);
  sportsElement("sportsBlockCapacity").textContent = capacity
    ? `Each league/series gets 1,000 channels: ${capacity} event slots at ${settings.channels_per_event || 10} channels per event. Overflow uses a separate continuation block.`
    : "Each league/series gets its own 1,000-channel block.";
  renderSportsBlockMap();

  const conflictCount = Number(sportsState.number_conflicts || 0);
  const warning = sportsElement("sportsNumberWarning");
  warning.classList.toggle("d-none", conflictCount === 0);
  warning.textContent = conflictCount
    ? `Starting channel overlaps ${conflictCount} manually numbered channel${conflictCount === 1 ? "" : "s"}. Choose a higher start number.`
    : "";

  const generatedCount = (sportsState.generated || []).length;
  const cachedCount = Number(sportsState.disabled_cache?.count || 0);
  sportsElement("sportsHeaderSummary").textContent = enabled
    ? `${settings.everything_mode ? "Everything mode" : `${sportsState.rules.length} selection${sportsState.rules.length === 1 ? "" : "s"}`} • ${generatedCount} generated channel${generatedCount === 1 ? "" : "s"}`
    : cachedCount
      ? `Sports channels hidden • ${cachedCount} cached for 24-hour recovery`
      : "Automatically build a daily sports channel block.";

  const everythingNotice = sportsElement("sportsEverythingModeNotice");
  everythingNotice.classList.toggle("d-none", !settings.everything_mode);
  everythingNotice.textContent = settings.everything_mode
    ? `Everything Mode is active. Your ${sportsState.rules.length} curated selection${sportsState.rules.length === 1 ? " is" : "s are"} safely preserved. Scans may take for-fucking-ever.`
    : "";

  sportsElement("sportsNextUpdate").textContent = !enabled
    ? "Sports automation disabled"
    : settings.auto_update
      ? `Next update: ${formatNextUpdate(sportsState.next_update)}`
      : "Automatic updates disabled";

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
      ? "Search leagues, series, tours…"
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
    if (!sportsState.scan?.running) setSportsError(error.message);
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
    sportsElement("sportsAutoUpdate").disabled = !enabled;
    sportsElement("sportsRunScanBtn").disabled = !enabled || Boolean(sportsState.scan?.running);
    scheduleSportsSave({enabled});
  });
  sportsElement("sportsCollapseBtn").addEventListener("click", () => {
    if (!sportsState.settings?.enabled) return;
    sportsCollapsed = !sportsCollapsed;
    localStorage.setItem(SPORTS_COLLAPSE_KEY, String(sportsCollapsed));
    applySportsState();
  });
  sportsElement("sportsBlockSportFilter").addEventListener("change", renderSportsBlockMap);
  sportsElement("sportsAutoUpdate").addEventListener("change", event => scheduleSportsSave({auto_update: event.target.checked}));
  sportsElement("sportsStartChannel").addEventListener("input", event => scheduleSportsSave({start_channel: Number(event.target.value)}));
  sportsElement("sportsBlockSize").addEventListener("input", event => scheduleSportsSave({channels_per_event: Number(event.target.value)}));
  sportsElement("sportsGroupTitle").addEventListener("input", event => scheduleSportsSave({group_title: event.target.value}));
  sportsElement("sportsTimezone").addEventListener("change", event => scheduleSportsSave({timezone: event.target.value}));
  sportsElement("sportsEventWindow").addEventListener("change", event => scheduleSportsSave({event_window: event.target.value}));
  sportsElement("sportsIncludeReplays").addEventListener("change", event => scheduleSportsSave({include_replays: event.target.checked}));
  sportsElement("sportsIncludePregame").addEventListener("change", event => scheduleSportsSave({include_pregame: event.target.checked}));
  sportsElement("sportsUseBackups").addEventListener("change", event => scheduleSportsSave({use_backup_feeds: event.target.checked}));
  sportsElement("sportsRefreshTime").addEventListener("change", event => {
    if (event.target.value) scheduleSportsSave({refresh_time: event.target.value});
  });
  sportsElement("sportsEverythingMode").addEventListener("change", event => {
    scheduleSportsSave({everything_mode: event.target.checked});
  });
  sportsElement("sportsScanStatusDismiss").addEventListener("click", () => {
    const signature = scanResultSignature(sportsState.last_scan);
    if (signature) localStorage.setItem(SPORTS_SCAN_DISMISSED_KEY, signature);
    renderSportsScanStatus();
  });

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
  sportsElement("sportsRunScanBtn").addEventListener("click", runSportsScan);
}

async function initialize() {
  await Promise.all([loadInitialChannels(), loadGroups(), loadSports()]);
  render();
  updateClearSearchButton();
  scheduleSportsStatusPoll();
}

bindSports();
initialize();
