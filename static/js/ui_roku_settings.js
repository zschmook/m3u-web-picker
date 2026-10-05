(() => {
  "use strict";
  const el = id => document.getElementById(id);
  if (!el("uiRokuDiscover")) return;
  const state = {busy: false, discovered: [], saved: []};

  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }

  function status(message, kind = "") {
    const node = el("uiRokuDiscoveryStatus");
    node.textContent = message;
    node.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`;
  }

  function isSaved(device) {
    return state.saved.some(saved => device.device_key
      ? saved.device_key === device.device_key : saved.host === device.host);
  }

  function renderDiscovered() {
    const list = el("uiRokuDiscoveredList");
    list.replaceChildren();
    list.hidden = state.discovered.length === 0;
    for (const device of state.discovered) {
      const row = document.createElement("div");
      row.className = "ui-roku-device-row ui-roku-discovered-row";
      const details = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = device.name || "Roku";
      const description = document.createElement("small");
      description.textContent = [device.model || device.model_number, device.host].filter(Boolean).join(" · ");
      details.append(name, description);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn ui-btn-secondary";
      button.textContent = isSaved(device) ? "Saved" : "Add Roku";
      button.disabled = state.busy || isSaved(device);
      button.addEventListener("click", () => save(device.host));
      row.append(details, button);
      list.append(row);
    }
  }

  function syncControls() {
    el("uiRokuDiscover").disabled = state.busy;
    el("uiRokuAdd").disabled = state.busy;
    el("uiRokuHost").disabled = state.busy;
    renderDiscovered();
  }

  function setSaved(devices) {
    state.saved = Array.isArray(devices) ? devices : [];
    window.dispatchEvent(new CustomEvent("ui:roku-devices-changed", {
      detail: {devices: state.saved, discovered: state.discovered},
    }));
  }

  async function discover() {
    if (state.busy) return;
    state.busy = true;
    state.discovered = [];
    syncControls();
    el("uiRokuDiscover").textContent = "Discovering…";
    status("Looking for Roku devices on your network…");
    try {
      const data = await api("/api/guide/roku/discover");
      state.discovered = Array.isArray(data.devices) ? data.devices : [];
      setSaved(data.saved_devices);
      if (data.warning) status(data.warning, "error");
      else if (!data.subnet && !state.discovered.length) {
        status("Automatic discovery needs this server's LAN address. You can add your Roku by IP below.");
      } else if (!state.discovered.length) {
        status("No Rokus found. Check that your Roku is on the same network, or add its IP address below.");
      } else {
        status(`Found ${state.discovered.length} Roku${state.discovered.length === 1 ? "" : "s"}. Choose Add Roku to save a device.`, "success");
      }
    } catch (error) {
      status(error.message, "error");
    } finally {
      state.busy = false;
      el("uiRokuDiscover").textContent = "Discover Rokus";
      syncControls();
    }
  }

  async function save(host) {
    if (state.busy || !String(host || "").trim()) return;
    state.busy = true;
    syncControls();
    status("Checking and saving Roku…");
    try {
      const data = await api("/api/guide/roku/devices", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({roku_host: String(host).trim()}),
      });
      state.saved = [...state.saved.filter(device => device.device_key !== data.device.device_key), data.device];
      try {
        const saved = await api("/api/guide/roku/devices");
        state.saved = Array.isArray(saved.devices) ? saved.devices : state.saved;
      } catch (_) {
        // The status panel will retry; retain the confirmed save in the meantime.
      }
      setSaved(state.saved);
      el("uiRokuHost").value = "";
      status(`Saved ${data.device.name || "Roku"}.`, "success");
    } catch (error) {
      status(error.message, "error");
    } finally {
      state.busy = false;
      syncControls();
    }
  }

  el("uiRokuDiscover").addEventListener("click", discover);
  el("uiRokuAddForm").addEventListener("submit", event => {
    event.preventDefault();
    void save(el("uiRokuHost").value);
  });
})();
