(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const form = el("uiLightingForm");
  if (!form) return;
  let knownLights = [], knownRokus = [], savedRokus = [], discoveredRokus = [];
  let rokuRevision = 0, draftNumber = 0, loaded = false, busy = false, selectedRoom = "";
  const rooms = new Map();
  const status = (message, error = false) => {
    el("uiLightingStatus").textContent = message;
    el("uiLightingStatus").className = `ui-settings-status is-${error ? "error" : "success"}`;
  };
  function syncButtons() {
    const disabled = !loaded || busy;
    for (const id of ["uiLightingSave", "uiLightingDiscover", "uiLightingRefresh", "uiLightingAddRoom", "uiLightingDeleteRoom", "uiLightingRoom", "uiLightingEditor"]) el(id).disabled = disabled;
  }
  function options(id, values, selected, placeholder) {
    const select = el(id);
    select.replaceChildren(...(placeholder ? [new Option(placeholder, "")] : []));
    values.forEach(value => {
      const option = new Option(value.label, value.key);
      option.disabled = Boolean(value.disabled);
      select.add(option);
    });
    select.value = selected || "";
  }
  function selectedLights() {
    return [...el("uiLightingLights").querySelectorAll("input:checked")].map(input => input.value);
  }
  function captureRoom() {
    const room = rooms.get(selectedRoom);
    if (!room) return;
    room.name = el("uiLightingRoomName").value;
    room.enabled = el("uiLightingEnabled").checked;
    room.brightness = Number(el("uiLightingPlaying").value);
    room.paused_brightness = Number(el("uiLightingPaused").value);
    room.transition_ms = Math.round(Number(el("uiLightingFade").value) * 1000);
    room.roku_host = el("uiLightingRoku").value;
    const roku = knownRokus.find(value => value.host === room.roku_host);
    room.roku_name = roku?.name || room.roku_name;
    room.targets = selectedLights().map(id => knownLights.find(light => light.device_id === id)
      || room.targets.find(light => light.device_id === id)).filter(Boolean);
  }
  function renderRoomList() {
    options("uiLightingRoom", [...rooms].map(([id, room]) => ({key: id, label: `${room.name || "Unnamed room"}${room.id ? "" : " (unsaved)"}`})), selectedRoom);
  }
  function renderLights(selected = []) {
    const chosen = new Set(selected), available = new Map(knownLights.map(light => [light.device_id, light]));
    for (const light of rooms.get(selectedRoom)?.targets || []) if (!available.has(light.device_id)) available.set(light.device_id, light);
    const list = el("uiLightingLights");
    list.replaceChildren();
    for (const light of available.values()) {
      const owner = [...rooms].find(([id, room]) => id !== selectedRoom && room.targets.some(target => target.device_id === light.device_id));
      const label = document.createElement("label"), input = document.createElement("input"), text = document.createElement("span");
      label.className = "ui-lighting-light";
      input.type = "checkbox";
      input.value = light.device_id;
      input.checked = chosen.has(light.device_id);
      input.disabled = Boolean(owner);
      text.textContent = light.name + (owner ? ` · ${owner[1].name}` : "");
      label.append(input, text);
      list.append(label);
    }
    if (!available.size) list.textContent = "No lights found yet. Use Discover HOs to find lights.";
  }
  function renderRokus(selected) {
    const room = rooms.get(selectedRoom);
    const choices = knownRokus.map(roku => {
      const owner = [...rooms].find(([id, other]) => id !== selectedRoom && other.roku_host === roku.host);
      return {key: roku.host, label: roku.name + (owner ? ` · ${owner[1].name}` : ""), disabled: Boolean(owner)};
    });
    if (room?.roku_host && !choices.some(choice => choice.key === room.roku_host))
      choices.push({key: room.roku_host, label: `${room.roku_name || room.roku_host} (saved)`});
    options("uiLightingRoku", choices, selected, "Choose a Roku");
  }
  function renderRoom() {
    const room = rooms.get(selectedRoom);
    renderRoomList();
    el("uiLightingRoomName").value = room.name;
    el("uiLightingEnabled").checked = Boolean(room.enabled);
    el("uiLightingPlaying").value = room.brightness;
    el("uiLightingPaused").value = room.paused_brightness;
    el("uiLightingFade").value = room.transition_ms / 1000;
    renderLights(room.targets.map(light => light.device_id));
    renderRokus(room.roku_host);
    syncButtons();
  }
  function addDraft() {
    const key = `draft-${++draftNumber}`;
    let number = 1;
    while ([...rooms.values()].some(room => room.name === `Room ${number}`)) number += 1;
    rooms.set(key, {name: `Room ${number}`, enabled: false, brightness: 10, paused_brightness: 100,
      transition_ms: 2000, targets: [], roku_host: "", roku_name: ""});
    selectedRoom = key;
  }
  function updateRokus(devices, discovered) {
    const selectedHost = el("uiLightingRoku").value;
    const previous = knownRokus.find(roku => roku.host === selectedHost);
    if (Array.isArray(devices)) savedRokus = devices;
    if (Array.isArray(discovered)) discoveredRokus = discovered;
    const byIdentity = new Map();
    for (const roku of [...discoveredRokus, ...savedRokus]) if (roku.host) byIdentity.set(roku.device_key || roku.host, roku);
    knownRokus = [...byIdentity.values()];
    rokuRevision += 1;
    const matching = previous?.device_key ? knownRokus.find(roku => roku.device_key === previous.device_key) : null;
    renderRokus(matching?.host || selectedHost);
  }
  async function request(options = {}, path = "/api/movie-lighting") {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not update lighting settings.");
    return data;
  }
  async function refreshRokus() {
    const revision = rokuRevision;
    try {
      const data = await request({}, "/api/guide/roku/devices");
      if (revision === rokuRevision) updateRokus(data.devices);
    } catch (error) { status(error.message, true); }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!loaded || busy || !form.reportValidity()) return;
    captureRoom();
    const room = rooms.get(selectedRoom), {id, revision, ...values} = room;
    if (room.enabled && (!room.roku_host || !room.targets.length)) {
      status("Choose a Roku and at least one light before enabling this room.", true); return;
    }
    busy = true; syncButtons(); status("Saving room…");
    try {
      const saved = await request({method: id ? "PATCH" : "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)},
        `/api/movie-lighting/rooms${id ? `/${encodeURIComponent(id)}` : ""}`);
      if (!id) rooms.delete(selectedRoom);
      selectedRoom = saved.id;
      rooms.set(saved.id, saved);
      renderRoom();
      status("Room saved. Brightness changes apply on play or pause; changes to its light group apply to the next movie.");
    } catch (error) { status(error.message, true); }
    finally { busy = false; syncButtons(); }
  });
  el("uiLightingRoom").addEventListener("change", () => {
    const next = el("uiLightingRoom").value;
    captureRoom(); selectedRoom = next; renderRoom(); status("Room edits are kept here until you save each room.");
  });
  el("uiLightingAddRoom").addEventListener("click", () => {
    if (!loaded || busy) return;
    captureRoom(); addDraft(); renderRoom(); status("Name this room, choose its Roku and lights, then save.");
  });
  el("uiLightingDeleteRoom").addEventListener("click", async () => {
    if (!loaded || busy) return;
    const room = rooms.get(selectedRoom);
    if (!window.confirm(`Delete ${room.name || "this room"}? Its lighting assignment will be removed.`)) return;
    busy = true; syncButtons();
    try {
      if (room.id) await request({method: "DELETE"}, `/api/movie-lighting/rooms/${encodeURIComponent(room.id)}`);
      rooms.delete(selectedRoom);
      selectedRoom = rooms.keys().next().value;
      if (!selectedRoom) addDraft();
      renderRoom(); status("Room deleted.");
    } catch (error) { status(error.message, true); }
    finally { busy = false; syncButtons(); }
  });
  async function loadLights(discover) {
    if (!loaded || busy) return;
    const selected = selectedLights();
    busy = true; syncButtons(); status(discover ? "Looking for compatible lights on your network…" : "Reading light names…");
    try {
      const data = await request({method: "POST"}, `/api/movie-lighting/lights/${discover ? "discover" : "refresh"}`);
      knownLights = data.lights; renderLights(selected);
      status(data.responding ? `Found ${data.responding} compatible light${data.responding === 1 ? "" : "s"}. Select the lights for this room and save.`
        : "No compatible lights responded. Check their network connection and try again.");
    } catch (error) { status(error.message, true); }
    finally { busy = false; syncButtons(); }
  }
  el("uiLightingDiscover").addEventListener("click", () => loadLights(true));
  el("uiLightingRefresh").addEventListener("click", () => loadLights(false));
  window.addEventListener("ui:roku-devices-changed", event => updateRokus(event.detail?.devices, event.detail?.discovered));
  document.querySelector('[data-settings-panel="lighting"]')?.addEventListener("click", refreshRokus);
  syncButtons();
  const initialRevision = rokuRevision;
  Promise.all([request(), request({}, "/api/movie-lighting/lights"), request({}, "/api/guide/roku/devices")])
    .then(([settings, lights, rokus]) => {
      knownLights = lights.lights;
      if (initialRevision === rokuRevision) updateRokus(rokus.devices);
      for (const room of settings.rooms || []) rooms.set(room.id, room);
      selectedRoom = rooms.keys().next().value;
      if (!selectedRoom) addDraft();
      loaded = true; renderRoom();
    }).catch(error => status(error.message, true));
})();
