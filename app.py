#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from flask import Flask, Response, jsonify, render_template_string, request, send_file


APP_DIR = Path(__file__).resolve().parent
EXPORT_DIR = APP_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)

DB_PATH = APP_DIR / "m3u_picker.db"
CONFIG_PATH = APP_DIR / "config.json"
MASTER_CACHE_PATH = APP_DIR / "master_playlist_cache.m3u"

PLAYLIST_NAME = "custom.m3u"
PLAYLIST_PATH = EXPORT_DIR / PLAYLIST_NAME
PORT = 9999

SCHEDULE_HOUR = 3
SCHEDULE_MINUTE = 0

app = Flask(__name__)

channels: List[dict] = []
selected_ids: set[int] = set()
last_source_url = ""
last_refresh = None
source_mode = ""
scheduler_started = False


@dataclass
class Entry:
    id: int
    name: str
    group: str
    url: str
    raw: list[str]


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS selections (
            key TEXT PRIMARY KEY,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_channels (
            group_id INTEGER NOT NULL,
            channel_key TEXT NOT NULL,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            PRIMARY KEY (group_id, channel_key),
            FOREIGN KEY (group_id) REFERENCES custom_groups(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "group"


def unique_slug(conn, name: str) -> str:
    base = slugify(name)
    slug = base
    i = 2
    while conn.execute("SELECT 1 FROM custom_groups WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{i}"
        i += 1
    return slug


def channel_key(channel: dict) -> str:
    return str(channel.get("url", "")).strip()


def load_selected_keys_from_db() -> set[str]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT key FROM selections").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def save_selected_channels_to_db(selected_channels: list[dict]) -> None:
    conn = db_connect()
    try:
        conn.execute("DELETE FROM selections")
        for ch in selected_channels:
            key = channel_key(ch)
            if not key:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO selections
                (key, name, group_title, url)
                VALUES (?, ?, ?, ?)
                """,
                (key, ch.get("name", ""), ch.get("group", ""), ch.get("url", "")),
            )
        conn.commit()
    finally:
        conn.close()


def apply_saved_selections_to_loaded_channels() -> None:
    global selected_ids
    saved_keys = load_selected_keys_from_db()
    selected_ids = {
        int(ch["id"])
        for ch in channels
        if channel_key(ch) in saved_keys
    }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config() -> None:
    data = {
        "source_url": last_source_url,
        "source_mode": source_mode,
        "last_refresh": last_refresh,
        "schedule": {"hour": SCHEDULE_HOUR, "minute": SCHEDULE_MINUTE},
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def restore_config() -> None:
    global last_source_url, source_mode, last_refresh
    data = load_config()
    last_source_url = str(data.get("source_url", "")).strip()
    source_mode = str(data.get("source_mode", "")).strip()
    last_refresh = data.get("last_refresh")


def parse_m3u_text(text: str) -> list[dict]:
    lines = text.splitlines()
    parsed: list[Entry] = []
    current: list[str] = []
    name = ""
    group = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#EXTINF"):
            current = [stripped]
            name = stripped.rsplit(",", 1)[1].strip() if "," in stripped else ""
            match = re.search(r'group-title="([^"]*)"', stripped)
            group = match.group(1) if match else ""
            continue

        if current and stripped.startswith("#"):
            current.append(stripped)
            continue

        if current and not stripped.startswith("#"):
            current.append(stripped)
            parsed.append(
                Entry(
                    id=len(parsed),
                    name=name or stripped,
                    group=group,
                    url=stripped,
                    raw=current.copy(),
                )
            )
            current = []

    return [asdict(entry) for entry in parsed]


def download_url_text(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8-sig", errors="replace")


def download_m3u_text(url: str, timeout: int = 60) -> str:
    return download_url_text(url, timeout=timeout)



def write_current_playlist() -> int:
    selected_channels = [ch for ch in channels if int(ch["id"]) in selected_ids]
    lines = ["#EXTM3U"]
    for ch in selected_channels:
        lines.extend(ch["raw"])
    PLAYLIST_PATH.write_text("\n".join(lines), encoding="utf-8")
    save_selected_channels_to_db(selected_channels)
    return len(selected_channels)


def channel_by_key_map() -> dict[str, dict]:
    return {channel_key(ch): ch for ch in channels if channel_key(ch)}


def group_channels_for_slug(slug: str) -> tuple[str, list[dict]]:
    conn = db_connect()
    try:
        group = conn.execute(
            "SELECT id, name FROM custom_groups WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not group:
            return "", []
        group_id, group_name = group
        keys = [
            row[0]
            for row in conn.execute(
                "SELECT channel_key FROM group_channels WHERE group_id = ? ORDER BY name",
                (group_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    current = channel_by_key_map()
    output = []
    for key in keys:
        if key in current:
            output.append(current[key])
    return group_name, output


def all_grouped_channels() -> list[dict]:
    conn = db_connect()
    try:
        keys = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT channel_key FROM group_channels ORDER BY name"
            ).fetchall()
        ]
    finally:
        conn.close()

    current = channel_by_key_map()
    return [current[key] for key in keys if key in current]


def m3u_from_channels(items: list[dict]) -> str:
    lines = ["#EXTM3U"]
    seen = set()
    for ch in items:
        key = channel_key(ch)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.extend(ch["raw"])
    return "\n".join(lines) + "\n"


def refresh_master_from_url() -> tuple[bool, str]:
    global channels, last_refresh, source_mode
    if not last_source_url:
        return False, "No source URL configured."
    try:
        text = download_m3u_text(last_source_url)
        MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
        channels = parse_m3u_text(text)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        last_refresh = datetime.now().isoformat(timespec="seconds")
        source_mode = "url"
        save_config()
        return True, f"Refreshed {len(channels)} channels."
    except Exception as exc:
        return False, str(exc)



def seconds_until_next_run(hour: int, minute: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def scheduler_loop() -> None:
    while True:
        time.sleep(seconds_until_next_run(SCHEDULE_HOUR, SCHEDULE_MINUTE))
        refresh_master_from_url()


def start_scheduler_once() -> None:
    global scheduler_started
    if scheduler_started:
        return
    scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True).start()


restore_config()

def load_cached_master_playlist_on_startup() -> None:
    global channels

    if not MASTER_CACHE_PATH.exists():
        return

    try:
        text = MASTER_CACHE_PATH.read_text(encoding="utf-8-sig", errors="replace")
        channels = parse_m3u_text(text)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
    except Exception as exc:
        print(f"Startup cache load failed: {exc}")

load_cached_master_playlist_on_startup()
start_scheduler_once()


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>M3U Web Picker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #111827; color: #e5e7eb; }
    .card { background: #1f2937; border-color: #374151; }
    .form-control, .form-select { background: #111827; color: #e5e7eb; border-color: #4b5563; }
    .form-control:focus, .form-select:focus { background: #111827; color: #e5e7eb; }
    .form-control:disabled { background: #374151; color: #9ca3af; }
    .table {
      --bs-table-bg: #111827;
      --bs-table-color: #e5e7eb;
      --bs-table-border-color: #374151;
      --bs-table-hover-bg: #1f2937;
      --bs-table-hover-color: #ffffff;
    }
    .table-wrap { max-height: 58vh; overflow: auto; border: 1px solid #374151; border-radius: .5rem; }
    thead th { position: sticky; top: 0; background: #1f2937 !important; z-index: 2; }
    .small-muted { color: #9ca3af; font-size: .9rem; }
    .url-cell { max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .group-pill { cursor: pointer; }
    .group-pill.active { outline: 2px solid #22c55e; }
  </style>
</head>
<body>
  <div class="container-fluid py-3">
    <div class="d-flex align-items-center justify-content-between mb-3 gap-3">
      <div>
        <h1 class="h3 mb-1">M3U Web Picker</h1>
        <div class="small-muted">Load, select, group, auto-save, and serve M3U playlists.</div>
      </div>

      <div class="text-end" style="min-width: 560px;">
        <div class="small-muted">Selected Playlist URL</div>
        <div class="input-group mb-2">
          <input id="playlistUrl" class="form-control" readonly>
          <button class="btn btn-success" id="copyPlaylistBtn" type="button">Copy</button>
        </div>
      </div>
    </div>

    <div class="card mb-3">
      <div class="card-body">
        <h2 class="h6 mb-3">Load Master Playlist</h2>

        <div class="mb-3">
          <label class="form-label">M3U URL</label>
          <div class="input-group">
            <input id="m3uUrl" class="form-control" placeholder="https://example.com/playlist.m3u">
            <button id="loadUrlBtn" class="btn btn-primary" type="button">Load URL</button>
          </div>
          <div class="small-muted mt-1">Provider M3U link. Saved and refreshed daily at 3:00 AM.</div>
        </div>


        <div>
          <label class="form-label">M3U File</label>
          <div class="input-group">
            <input id="m3uFile" type="file" class="form-control" accept=".m3u,.m3u8,text/plain">
            <button id="uploadBtn" class="btn btn-secondary" type="button">Load File</button>
          </div>
        </div>

        <div class="mt-3">
          <button id="changeSourceBtn" class="btn btn-outline-warning btn-sm" type="button">Change Source</button>
          <span id="sourceModeLabel" class="small-muted ms-2"></span>
        </div>
      </div>
    </div>

    <div class="card mb-3">
      <div class="card-body">
        <div class="d-flex align-items-end gap-2 flex-wrap">
          <div style="min-width:260px;">
            <label class="form-label">Custom Groups</label>
            <div class="input-group">
              <input id="newGroupName" class="form-control" placeholder="New group name">
              <button id="createGroupBtn" class="btn btn-outline-success" type="button">Create</button>
            </div>
          </div>

          <div>
            <label class="form-label">Active Group</label>
            <select id="activeGroup" class="form-select" style="min-width:220px;">
              <option value="">No group selected</option>
            </select>
          </div>

          <button id="addVisibleToGroupBtn" class="btn btn-outline-light" type="button">Add visible to group</button>
          <button id="removeVisibleFromGroupBtn" class="btn btn-outline-warning" type="button">Remove visible from group</button>
          <button id="showGroupOnlyBtn" class="btn btn-outline-info" type="button">Show group only</button>

          <div class="ms-auto" style="min-width:420px;">
            <label class="form-label">Active Group Playlist URL</label>
            <div class="input-group">
              <input id="groupPlaylistUrl" class="form-control" readonly>
              <button id="copyGroupBtn" class="btn btn-success" type="button">Copy</button>
            </div>
          </div>
        </div>

        <div id="groupPills" class="mt-3 d-flex flex-wrap gap-2"></div>
      </div>
    </div>

    <div class="card mb-3"><div class="card-body">
      <div class="row g-2 align-items-end">
        <div class="col-md-4">
          <label class="form-label">Search</label>
          <input id="search" class="form-control" placeholder="channel, group, or URL">

          <div class="d-flex gap-2 mt-2">
            <button id="selectVisibleBtn" class="btn btn-primary" type="button">Add all</button>
            <button id="clearVisibleBtn" class="btn btn-outline-light" type="button">Remove all</button>
          </div>
        </div>
        <div class="col-md-3">
          <label class="form-label">Provider Group</label>
          <select id="groupFilter" class="form-select"><option value="">All provider groups</option></select>
        </div>
        <div class="col-auto">
          <div class="form-check mt-4">
            <input id="selectedOnly" class="form-check-input" type="checkbox">
            <label class="form-check-label" for="selectedOnly">Show selected only</label>
          </div>
        </div>
        <div class="col-auto ms-auto"><span class="badge text-bg-success p-2">Auto-save enabled</span></div>
      </div>
      <div class="mt-3 d-flex gap-4 flex-wrap">
        <div><strong id="selectedCount">0</strong> selected</div>
        <div><strong id="visibleCount">0</strong> visible</div>
        <div><strong id="totalCount">0</strong> total</div>
        <div id="status" class="small-muted"></div>
      </div>
    </div></div>

    <div class="table-wrap">
      <table class="table table-hover table-sm align-middle mb-0">
        <thead><tr><th style="width:50px;">✓</th><th>Channel</th><th>Provider Group</th><th>URL</th></tr></thead>
        <tbody id="channelTable"></tbody>
      </table>
    </div>
  </div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>

<div class="modal fade" id="urlModal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content bg-dark text-light border-secondary">
      <div class="modal-header border-secondary">
        <h5 class="modal-title">Enter M3U URL</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <input id="modalUrlInput" class="form-control" placeholder="https://provider.com/playlist.m3u">
      </div>
      <div class="modal-footer border-secondary">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="modalLoadBtn">Load Playlist</button>
      </div>
    </div>
  </div>
</div>

<script>
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

  if (mode === "url") {
    fileInput.disabled = true; fileBtn.disabled = true;
    urlInput.disabled = false; urlBtn.disabled = false;
    label.textContent = "URL source active. File loading disabled.";
  } else if (mode === "file") {
    urlInput.disabled = true; urlBtn.disabled = true;
    fileInput.disabled = false; fileBtn.disabled = false;
    label.textContent = "File source active. URL loading disabled.";
  } else {
    urlInput.disabled = false; urlBtn.disabled = false;
    fileInput.disabled = false; fileBtn.disabled = false;
    label.textContent = "";
  }
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

  return channels.filter(ch => {
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

  if (selectBtn) selectBtn.disabled = visible.length === 0;
  if (clearBtn) clearBtn.disabled = visible.length === 0;
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
      setStatus(`Loaded ${channels.length} cached channels.`);
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
  setStatus(`Saved ${data.count} channels.`);
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

els.search.addEventListener("input", render);
els.groupFilter.addEventListener("change", render);
els.selectedOnly.addEventListener("change", render);

loadInitialChannels();
loadGroups();
render();
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(HTML)


@app.post("/api/load-url")
def api_load_url():
    global channels, last_source_url, last_refresh, source_mode
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return jsonify(error="URL must start with http:// or https://"), 400

    try:
        text = download_m3u_text(url)
        MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
        channels = parse_m3u_text(text)
        last_source_url = url
        source_mode = "url"
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        last_refresh = datetime.now().isoformat(timespec="seconds")
        save_config()
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    return jsonify(count=len(channels), channels=channels, selected_ids=sorted(selected_ids))


@app.post("/api/upload")
def api_upload():
    global channels, last_refresh, source_mode
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify(error="No file uploaded."), 400

    try:
        raw = uploaded.read()
        text = raw.decode("utf-8-sig", errors="replace")
        MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
        channels = parse_m3u_text(text)
        source_mode = "file"
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        last_refresh = datetime.now().isoformat(timespec="seconds")
        save_config()
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    return jsonify(count=len(channels), channels=channels, selected_ids=sorted(selected_ids))


@app.post("/api/selection")
def api_selection():
    global selected_ids
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get("ids", [])
    selected_ids = set(int(i) for i in ids)
    count = write_current_playlist()
    save_config()
    return jsonify(count=count, path=str(PLAYLIST_PATH), url="/playlist/custom.m3u")


@app.get("/playlist/custom.m3u")
def playlist():
    if not PLAYLIST_PATH.exists():
        return Response("#EXTM3U\n", mimetype="audio/x-mpegurl")
    return send_file(PLAYLIST_PATH, mimetype="audio/x-mpegurl", as_attachment=False, download_name=PLAYLIST_NAME)


@app.get("/playlist/all.m3u")
def playlist_all():
    return Response(m3u_from_channels(all_grouped_channels()), mimetype="audio/x-mpegurl")


@app.get("/playlist/group/<slug>.m3u")
def playlist_group(slug: str):
    _, items = group_channels_for_slug(slug)
    return Response(m3u_from_channels(items), mimetype="audio/x-mpegurl")


@app.get("/epg.xml")
def epg():
    if not EPG_CACHE_PATH.exists():
        return Response("", mimetype="application/xml")
    return send_file(EPG_CACHE_PATH, mimetype="application/xml", as_attachment=False, download_name="epg.xml")


@app.get("/api/channels")
def api_channels():
    return jsonify(
        count=len(channels),
        channels=channels,
        selected_ids=sorted(selected_ids),
        source_mode=source_mode,
        source_url_configured=bool(last_source_url),
    )


@app.get("/api/status")
def api_status():
    return jsonify(
        loaded=len(channels),
        selected=len(selected_ids),
        saved_selections=len(load_selected_keys_from_db()),
        playlist_exists=PLAYLIST_PATH.exists(),
        playlist_url="/playlist/custom.m3u",
        playlist_all_url="/playlist/all.m3u",
        playlist_path=str(PLAYLIST_PATH),
        source_url_configured=bool(last_source_url),
        source_mode=source_mode,
        last_refresh=last_refresh,
        schedule={"hour": SCHEDULE_HOUR, "minute": SCHEDULE_MINUTE},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
