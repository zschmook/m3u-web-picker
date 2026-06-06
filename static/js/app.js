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

function setStatus(msg) { els.status.textContent = msg || ""; }


function updateClearSearchButton() {
  const btn = document.getElementById("clearSearchBtn");
  if (!btn) return;

  if (els.search.value.length > 0) {
    btn.classList.remove("d-none");
  } else {
    btn.classList.add("d-none");
  }
}


async function copyInputValue(inputId, buttonId) {
  const input = document.getElementById(inputId);
  const btn = document.getElementById(buttonId);
  try { await navigator.clipboard.writeText(input.value); }
  catch { input.select(); document.execCommand("copy"); }
  btn.textContent = "Copied!";
  setTimeout(() => { btn.textContent = "Copy"; }, 1500);
}

function channelKey(ch) { return String(ch.url || "").trim(); }

function setSourceMode(mode) {
  const urlInput = document.getElementById("m3uUrl");
  const urlBtn = document.getElementById("loadUrlBtn");
  const fileInput = document.getElementById("m3uFile");
  const fileBtn = document.getElementById("uploadBtn");
  const label = document.getElementById("sourceModeLabel");

  // If the source is locked after a successful load, do not unlock it here.
  if (urlInput && urlInput.value === "Source Loaded") {
    return;
  }

  if (mode === "url") {
    if (fileInput) fileInput.disabled = true;
    if (fileBtn) fileBtn.disabled = true;
    if (urlInput) urlInput.disabled = false;
    if (urlBtn) urlBtn.disabled = false;
    if (label) label.textContent = "URL source active. File loading disabled.";
  } else if (mode === "file") {
    if (urlInput) urlInput.disabled = true;
    if (urlBtn) urlBtn.disabled = true;
    if (fileInput) fileInput.disabled = false;
    if (fileBtn) fileBtn.disabled = false;
    if (label) label.textContent = "File source active. URL loading disabled.";
  } else {
    if (urlInput) urlInput.disabled = false;
    if (urlBtn) urlBtn.disabled = false;
    if (fileInput) fileInput.disabled = false;
    if (fileBtn) fileBtn.disabled = false;
    if (label) label.textContent = "";
  }
}


function lockLoadedSourceControls() {
  const urlInput = document.getElementById("m3uUrl");
  const loadUrlBtn = document.getElementById("loadUrlBtn");
  const fileInput = document.getElementById("m3uFile");
  const uploadBtn = document.getElementById("uploadBtn");
  const changeSourceBtn = document.getElementById("changeSourceBtn");
  const label = document.getElementById("sourceModeLabel");

  if (!urlInput || !loadUrlBtn || !changeSourceBtn) return;

  urlInput.value = "Source Loaded";
  urlInput.disabled = true;
  urlInput.classList.add("source-loaded-placeholder");

  loadUrlBtn.disabled = true;
  if (fileInput) fileInput.disabled = true;
  if (uploadBtn) uploadBtn.disabled = true;

  changeSourceBtn.classList.remove("d-none");
  if (label) label.textContent = "Source loaded. Click Change Source to replace it.";
}

function unlockLoadedSourceControls() {
  const urlInput = document.getElementById("m3uUrl");
  const loadUrlBtn = document.getElementById("loadUrlBtn");
  const fileInput = document.getElementById("m3uFile");
  const uploadBtn = document.getElementById("uploadBtn");
  const changeSourceBtn = document.getElementById("changeSourceBtn");
  const label = document.getElementById("sourceModeLabel");

  if (!urlInput || !loadUrlBtn || !changeSourceBtn) return;

  urlInput.value = "";
  urlInput.disabled = false;
  urlInput.classList.remove("source-loaded-placeholder");

  loadUrlBtn.disabled = false;
  if (fileInput) {
    fileInput.disabled = false;
    fileInput.value = "";
  }
  if (uploadBtn) uploadBtn.disabled = false;

  changeSourceBtn.classList.add("d-none");
  if (label) label.textContent = "";

  urlInput.focus();
}

function showUrlModal() {
  const modalEl = document.getElementById("urlModal");
  const modalInput = document.getElementById("modalUrlInput");
  modalInput.value = "";
  const modal = new bootstrap.Modal(modalEl);
  modal.show();
  modalEl.addEventListener("shown.bs.modal", () => modalInput.focus(), { once: true });
}

function acceptModalUrl() {
  const modalEl = document.getElementById("urlModal");
  const modalInput = document.getElementById("modalUrlInput");
  const value = modalInput.value.trim();
  if (!value) { modalInput.focus(); return; }
  document.getElementById("m3uUrl").value = value;
  const modal = bootstrap.Modal.getInstance(modalEl);
  if (modal) modal.hide();
  loadFromUrl();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function filteredChannels() {
  const q = els.search.value.trim().toLowerCase();
  const group = els.groupFilter.value;
  const only = els.selectedOnly.checked;
  const excludeSd = els.excludeSdChannels && els.excludeSdChannels.checked;

  return channels.filter(ch => {
    if (excludeSd && String(ch.group || "").trim().toUpperCase() === "LOW BANDWIDTH") return false;
    if (group && ch.group !== group) return false;
    if (only && !selected.has(ch.id)) return false;
    if (showGroupOnly && activeGroupSlug && !activeGroupMembers.has(channelKey(ch))) return false;
    if (q) {
      const haystack = `${ch.name} ${ch.group} ${ch.url}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

function rebuildProviderGroupFilter() {
  const groups = [...new Set(channels.map(ch => ch.group).filter(Boolean))].sort();
  els.groupFilter.innerHTML = `<option value="">All provider groups</option>` +
    groups.map(g => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`).join("");
}

function render() {
  const visible = filteredChannels();
  els.table.innerHTML = visible.map(ch => `
    <tr data-id="${ch.id}">
      <td><input class="form-check-input channel-check" type="checkbox" data-id="${ch.id}" ${selected.has(ch.id) ? "checked" : ""}></td>
      <td>${escapeHtml(ch.name)}</td>
      <td>${escapeHtml(ch.group)}</td>
      <td class="url-cell" title="${escapeHtml(ch.url)}">${escapeHtml(ch.url)}</td>
    </tr>
  `).join("");

  els.selectedCount.textContent = selected.size;
  els.visibleCount.textContent = visible.length;
  els.totalCount.textContent = channels.length;

  const selectBtn = document.getElementById("selectVisibleBtn");
  const clearBtn = document.getElementById("clearVisibleBtn");

  if (selectBtn) selectBtn.textContent = `Add all ${visible.length}`;
  if (clearBtn) clearBtn.textContent = `Remove all ${visible.length}`;

  const showSelectedBtn = document.getElementById("showSelectedBtn");
  if (showSelectedBtn) {
    showSelectedBtn.textContent = els.selectedOnly.checked
      ? `Show all (${selected.size} saved)`
      : `Saved ${selected.size} channels`;
  }

  const savedMode = els.selectedOnly.checked;

  if (selectBtn) selectBtn.disabled = savedMode || visible.length === 0;
  if (clearBtn) clearBtn.disabled = savedMode || visible.length === 0;
  if (showSelectedBtn) {
    showSelectedBtn.disabled = selected.size === 0 && !savedMode;

    if (savedMode) {
      showSelectedBtn.classList.remove("btn-outline-success");
      showSelectedBtn.classList.add("btn-success");
    } else {
      showSelectedBtn.classList.remove("btn-success");
      showSelectedBtn.classList.add("btn-outline-success");
    }
  }
}

function renderGroups() {
  const pills = document.getElementById("groupPills");
  pills.innerHTML = customGroups.map(g => `
    <span class="badge rounded-pill text-bg-secondary group-pill ${g.slug === activeGroupSlug ? "active" : ""}" data-slug="${escapeHtml(g.slug)}">
      ${escapeHtml(g.name)}
    </span>
  `).join("");

  els.activeGroup.innerHTML = `<option value="">No group selected</option>` +
    customGroups.map(g => `<option value="${escapeHtml(g.slug)}">${escapeHtml(g.name)}</option>`).join("");

  if (activeGroupSlug) els.activeGroup.value = activeGroupSlug;
  updateGroupUrl();
}

function updateGroupUrl() {
  if (activeGroupSlug) {
    els.groupPlaylistUrl.value = `${location.origin}/playlist/group/${activeGroupSlug}.m3u`;
  } else {
    els.groupPlaylistUrl.value = `${location.origin}/playlist/all.m3u`;
  }
}

async function loadGroups() {
  const res = await fetch("/api/groups");
  const data = await res.json();
  customGroups = data.groups || [];
  renderGroups();
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

  const res = await fetch(`/api/groups/${activeGroupSlug}/channels`);
  const data = await res.json();
  activeGroupMembers = new Set(data.channel_keys || []);
  renderGroups();
  render();
}

async function createGroup() {
  const input = document.getElementById("newGroupName");
  const name = input.value.trim();
  if (!name) { input.focus(); return; }

  const res = await fetch("/api/groups", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name})
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || "Could not create group.");

  input.value = "";
  await loadGroups();
  await setActiveGroup(data.group.slug);
  setStatus(`Created group: ${data.group.name}`);
}

async function addVisibleToGroup() {
  if (!activeGroupSlug) return alert("Choose or create a group first.");
  const visible = filteredChannels();
  const keys = visible.map(channelKey).filter(Boolean);

  const res = await fetch(`/api/groups/${activeGroupSlug}/channels`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel_keys: keys})
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || "Could not add channels.");

  await setActiveGroup(activeGroupSlug);
  setStatus(`Added ${data.added} visible channels to group.`);
}

async function removeVisibleFromGroup() {
  if (!activeGroupSlug) return alert("Choose a group first.");
  const visible = filteredChannels();
  const keys = visible.map(channelKey).filter(Boolean);

  const res = await fetch(`/api/groups/${activeGroupSlug}/channels`, {
    method: "DELETE",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel_keys: keys})
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || "Could not remove channels.");

  await setActiveGroup(activeGroupSlug);
  setStatus(`Removed ${data.removed} visible channels from group.`);
}

async function loadFromUrl() {
  const url = document.getElementById("m3uUrl").value.trim();
  if (!url) { showUrlModal(); return; }

  setStatus("Loading URL...");
  const res = await fetch("/api/load-url", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url})
  });
  const data = await res.json();
  if (!res.ok) { setStatus(""); return alert(data.error || "URL load failed."); }

  channels = data.channels;
  selected = new Set(data.selected_ids || []);
  rebuildProviderGroupFilter();
  render();
  setSourceMode("url");
  lockLoadedSourceControls();
  if (activeGroupSlug) await setActiveGroup(activeGroupSlug);
  setStatus(`Loaded ${channels.length} channels from URL.`);
}

async function uploadFile() {
  const file = document.getElementById("m3uFile").files[0];
  if (!file) return alert("Choose an M3U file first.");
  setStatus("Uploading file...");

  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  const data = await res.json();
  if (!res.ok) { setStatus(""); return alert(data.error || "Upload failed."); }

  channels = data.channels;
  selected = new Set(data.selected_ids || []);
  rebuildProviderGroupFilter();
  render();
  setSourceMode("file");
  lockLoadedSourceControls();
  if (activeGroupSlug) await setActiveGroup(activeGroupSlug);
  setStatus(`Loaded ${channels.length} channels from file.`);
}



async function loadInitialChannels() {
  try {
    const res = await fetch("/api/channels");
    const data = await res.json();

    channels = data.channels || [];
    selected = new Set(data.selected_ids || []);

    rebuildProviderGroupFilter();
    render();

    if (data.source_mode) {
      setSourceMode(data.source_mode);
    }

    if (channels.length > 0) {
      lockLoadedSourceControls();
      setStatus(`Loaded ${channels.length} cached channels.`);
    } else {
      unlockLoadedSourceControls();
    }
  } catch (err) {
    setStatus("Could not load cached channels.");
  }
}


function scheduleSaveSelected() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSelected, 250);
}

async function saveSelected() {
  setStatus("Saving playlist...");
  const res = await fetch("/api/selection", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ids: [...selected]})
  });
  const data = await res.json();
  if (!res.ok) { setStatus(data.error || "Save failed."); return; }
  setStatus("Playlist updated.");
}

els.table.addEventListener("change", e => {
  if (!e.target.classList.contains("channel-check")) return;
  const id = Number(e.target.dataset.id);
  if (e.target.checked) selected.add(id);
  else selected.delete(id);
  render();
  scheduleSaveSelected();
});

els.table.addEventListener("dblclick", e => {
  const row = e.target.closest("tr");
  if (!row) return;
  const id = Number(row.dataset.id);
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
document.getElementById("modalUrlInput").addEventListener("keydown", e => { if (e.key === "Enter") acceptModalUrl(); });
document.getElementById("changeSourceBtn").addEventListener("click", () => { setSourceMode(""); setStatus("Source unlocked."); });
document.getElementById("createGroupBtn").addEventListener("click", createGroup);
document.getElementById("newGroupName").addEventListener("keydown", e => { if (e.key === "Enter") createGroup(); });
els.activeGroup.addEventListener("change", e => setActiveGroup(e.target.value));
document.getElementById("addVisibleToGroupBtn").addEventListener("click", addVisibleToGroup);
document.getElementById("removeVisibleFromGroupBtn").addEventListener("click", removeVisibleFromGroup);
document.getElementById("showGroupOnlyBtn").addEventListener("click", () => {
  showGroupOnly = activeGroupSlug ? !showGroupOnly : false;
  setStatus(showGroupOnly ? "Showing active group only." : "Showing all matching channels.");
  render();
});

document.getElementById("selectVisibleBtn").addEventListener("click", () => {
  const visible = filteredChannels();
  for (const ch of visible) selected.add(ch.id);
  render();
  scheduleSaveSelected();
  setStatus(`Added ${visible.length} channels from current search.`);
});
document.getElementById("showSelectedBtn").addEventListener("click", () => {
  els.selectedOnly.checked = !els.selectedOnly.checked;
  render();
  setStatus(
    els.selectedOnly.checked
      ? "Showing saved channels only. Add all / Remove all disabled."
      : "Showing all channels."
  );
});

document.getElementById("clearVisibleBtn").addEventListener("click", () => {
  const visible = filteredChannels();
  for (const ch of visible) selected.delete(ch.id);
  render();
  scheduleSaveSelected();
  setStatus(`Removed ${visible.length} channels from current search.`);
});
document.getElementById("groupPills").addEventListener("click", e => {
  const pill = e.target.closest(".group-pill");
  if (!pill) return;
  setActiveGroup(pill.dataset.slug);
});

els.search.addEventListener("input", () => {
  updateClearSearchButton();
  render();
});
els.groupFilter.addEventListener("change", render);
els.selectedOnly.addEventListener("change", render);
if (els.excludeSdChannels) {
  els.excludeSdChannels.addEventListener("change", () => {
    render();
    setStatus(
      els.excludeSdChannels.checked
        ? "SD / LOW BANDWIDTH channels hidden."
        : "SD / LOW BANDWIDTH channels visible."
    );
  });
}



// Custom playlist order modal
let orderChannels = [];
let orderSelectedKey = "";

function renderOrderTable() {
  const tbody = document.getElementById("orderTable");

  tbody.innerHTML = orderChannels.map(ch => {
    const index = orderChannels.findIndex(item => item.key === ch.key);
    return `
      <tr data-key="${escapeHtml(ch.key)}" class="${ch.key === orderSelectedKey ? "order-selected" : ""}">
        <td>${index + 1}</td>
        <td>${escapeHtml(ch.name || ch.url)}</td>
        <td>${escapeHtml(ch.group || "")}</td>
      </tr>
    `;
  }).join("");
}

async function openOrderModal() {
  const res = await fetch("/api/selection/order");
  const data = await res.json();

  orderChannels = data.channels || [];
  orderSelectedKey = "";

  renderOrderTable();

  const modal = new bootstrap.Modal(document.getElementById("orderModal"));
  modal.show();
}

function moveSelectedOrder(direction) {
  if (!orderSelectedKey) return;

  const idx = orderChannels.findIndex(ch => ch.key === orderSelectedKey);
  if (idx < 0) return;

  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= orderChannels.length) return;

  const [item] = orderChannels.splice(idx, 1);
  orderChannels.splice(newIdx, 0, item);

  renderOrderTable();
}

async function saveOrder() {
  const keys = orderChannels.map(ch => ch.key);

  const res = await fetch("/api/selection/order", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({keys})
  });

  const data = await res.json();

  if (!res.ok) {
    return alert(data.error || "Could not save order.");
  }

  setStatus(`Saved custom.m3u order for ${data.count} channels.`);

  const modalEl = document.getElementById("orderModal");
  const modal = bootstrap.Modal.getInstance(modalEl);
  if (modal) {
    modal.hide();
  }
}

document.getElementById("manageOrderBtn").addEventListener("click", openOrderModal);

document.getElementById("orderTable").addEventListener("click", e => {
  const row = e.target.closest("tr");
  if (!row) return;
  orderSelectedKey = row.dataset.key;
  renderOrderTable();
});

document.getElementById("moveOrderUpBtn").addEventListener("click", () => moveSelectedOrder(-1));
document.getElementById("moveOrderDownBtn").addEventListener("click", () => moveSelectedOrder(1));
document.getElementById("saveOrderBtn").addEventListener("click", saveOrder);


document.getElementById("clearSearchBtn").addEventListener("click", () => {
  els.search.value = "";
  updateClearSearchButton();
  render();
  els.search.focus();
});

loadInitialChannels();
loadGroups();
render();
updateClearSearchButton();
