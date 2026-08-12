(() => {
  const select = document.getElementById("guideRokuDevice");
  const discoverButton = document.getElementById("guideRokuDiscoverBtn");
  const testButton = document.getElementById("guideRokuTestBtn");
  if (!select || !testButton || typeof guideState === "undefined" || typeof guideEls === "undefined") return;

  const multiRoku = {
    savedByKey: new Map(),
    savedByHost: new Map(),
    sessions: new Map(),
    sessionKeyByHost: new Map(),
  };

  guideState.roku.sessions = multiRoku.sessions;

  const addButton = document.createElement("button");
  addButton.id = "guideRokuAddBtn";
  addButton.className = "btn btn-outline-success btn-sm";
  addButton.type = "button";
  addButton.textContent = "Add Device";
  testButton.parentElement?.insertBefore(addButton, testButton);

  function selectedHost() {
    return String(select.value || configuredRokuHost() || "").trim();
  }

  function selectedSavedDevice() {
    return multiRoku.savedByHost.get(selectedHost()) || null;
  }

  function selectedDeviceKey() {
    const saved = selectedSavedDevice();
    if (saved?.device_key) return saved.device_key;
    const host = selectedHost();
    return host ? `host:${host}` : "";
  }

  function selectedSession() {
    const host = selectedHost();
    const key = multiRoku.sessionKeyByHost.get(host) || selectedDeviceKey();
    return key ? multiRoku.sessions.get(key) || null : null;
  }

  function setSavedDevices(devices) {
    multiRoku.savedByKey.clear();
    multiRoku.savedByHost.clear();
    for (const device of Array.isArray(devices) ? devices : []) {
      const key = String(device?.device_key || "").trim();
      const host = String(device?.host || "").trim();
      if (!key || !host) continue;
      multiRoku.savedByKey.set(key, device);
      multiRoku.savedByHost.set(host, device);
    }
    annotateOptions();
    restoreStableSelection();
    syncSelectedSession();
  }

  function annotateOptions() {
    for (const option of select.options) {
      const host = String(option.value || "").trim();
      if (!host) continue;
      const saved = multiRoku.savedByHost.get(host);
      const clean = String(option.textContent || "").replace(/\s+·\s+Saved$/, "");
      option.textContent = saved ? `${clean} · Saved` : clean;
    }
    const saved = selectedSavedDevice();
    addButton.disabled = !selectedHost() || Boolean(saved);
    addButton.textContent = saved ? "Saved" : "Add Device";
  }

  function restoreStableSelection() {
    const preferredKey = localStorage.getItem("m3u-guide-roku-device-key") || "";
    const preferred = multiRoku.savedByKey.get(preferredKey);
    if (!preferred?.host) return;
    const option = Array.from(select.options).find(item => item.value === preferred.host);
    if (!option) return;
    if (select.value !== preferred.host) {
      select.value = preferred.host;
      guideEls.rokuHost.value = preferred.host;
      guideState.roku.host = preferred.host;
      guideState.roku.deviceName = preferred.name || "Roku TV";
    }
  }

  async function refreshSavedDevices() {
    try {
      const response = await fetch(`/api/guide/roku/devices?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not load saved Roku devices.");
      setSavedDevices(data.devices || []);
    } catch (error) {
      console.warn("Could not load saved Roku devices", error);
    }
  }

  async function refreshDiscoveryMetadata() {
    try {
      const response = await fetch(`/api/guide/roku/discover?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok || !data.ok) return;
      setSavedDevices(data.saved_devices || []);
    } catch (_) {
      // The built-in discovery UI owns the user-facing error state.
    }
  }

  function migrateSession(oldKey, newKey) {
    if (!oldKey || !newKey || oldKey === newKey) return;
    const existing = multiRoku.sessions.get(oldKey);
    if (existing) {
      multiRoku.sessions.delete(oldKey);
      existing.deviceKey = newKey;
      multiRoku.sessions.set(newKey, existing);
    }
    for (const [host, key] of multiRoku.sessionKeyByHost.entries()) {
      if (key === oldKey) multiRoku.sessionKeyByHost.set(host, newKey);
    }
  }

  async function addSelectedDevice() {
    const host = selectedHost();
    if (!host || selectedSavedDevice()) return;
    const previousText = addButton.textContent;
    addButton.disabled = true;
    addButton.textContent = "Saving…";
    try {
      const response = await fetch("/api/guide/roku/devices", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({roku_host: host}),
        cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Could not save Roku device.");
      const device = data.device || {};
      const oldKey = multiRoku.sessionKeyByHost.get(host) || `host:${host}`;
      const newKey = String(device.device_key || oldKey);
      migrateSession(oldKey, newKey);
      multiRoku.sessionKeyByHost.set(host, newKey);
      localStorage.setItem("m3u-guide-roku-device-key", newKey);
      localStorage.setItem("m3u-guide-roku-host", host);
      await refreshSavedDevices();
      updateRokuControls(`Saved ${device.name || "Roku TV"}${device.model ? ` · ${device.model}` : ""}.`);
    } catch (error) {
      updateRokuControls(error?.message || String(error));
    } finally {
      if (!selectedSavedDevice()) {
        addButton.disabled = false;
        addButton.textContent = previousText;
      }
    }
  }

  async function stopRokuRelayToken(token) {
    if (!token) return;
    try {
      await fetch("/api/guide/roku/stop", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token}),
        cache: "no-store",
      });
    } catch (error) {
      console.warn("Could not stop previous Roku relay", error);
    }
  }

  function syncSelectedSession(message = "") {
    const host = selectedHost();
    const session = selectedSession();
    guideState.roku.host = host;
    guideState.roku.active = Boolean(session);
    guideState.roku.relayToken = session?.token || "";
    guideState.roku.deviceName = session?.deviceName || selectedSavedDevice()?.name || guideState.roku.deviceName || "Roku TV";
    if (host) guideEls.rokuHost.value = host;
    updateRokuControls(message);
  }

  window.updateRokuControls = function(message = "") {
    const host = selectedHost();
    const saved = selectedSavedDevice();
    const session = selectedSession();
    guideState.roku.active = Boolean(session);
    guideEls.rokuBtn.disabled = !session && (!guideState.currentChannel || !host);
    guideEls.rokuBtn.textContent = session ? `Disconnect ${session.deviceName || "Roku"}` : "Roku";

    annotateOptions();
    if (message) {
      guideEls.rokuStatus.textContent = message;
    } else if (!host) {
      guideEls.rokuStatus.textContent = "Choose a discovered Roku device.";
    } else if (session) {
      guideEls.rokuStatus.textContent = `Playing on ${session.deviceName || "Roku TV"} (${host}).`;
    } else if (saved) {
      guideEls.rokuStatus.textContent = `Saved ${saved.name || "Roku TV"} · ready at ${host}.`;
    } else {
      guideEls.rokuStatus.textContent = `Discovered Roku at ${host}. Add it to save this device.`;
    }
  };

  window.startRokuChannel = async function(channel) {
    const host = selectedHost();
    if (!host) throw new Error("Choose a Roku device in Diagnostics first.");

    if (currentCastSession()) {
      await stopRemoteMedia();
      await stopCastRelay();
      guideState.cast.context.endCurrentSession(true);
    }

    const selectedKeyBefore = multiRoku.sessionKeyByHost.get(host) || selectedDeviceKey();
    const previousSession = selectedSession();
    const saved = selectedSavedDevice();

    setCurrentChannel(channel);
    stopLocalStream({hidePanel: false});
    guideState.roku.host = host;
    guideState.roku.deviceName = saved?.name || guideState.roku.deviceName || "Roku TV";
    guideEls.playerMessage.textContent = `Starting Roku relay for ${guideState.roku.deviceName}…`;
    showRokuPlayer();

    const response = await fetch("/api/guide/roku/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        play_url: channel?.play_url || "",
        roku_host: host,
        roku_device_key: saved?.device_key || "",
      }),
      cache: "no-store",
    });
    const data = await response.json();
    if (!response.ok) {
      showLocalPlayer();
      throw new Error(data.error || "Could not start Roku playback.");
    }

    const stableKey = String(data.roku_device_key || "");
    const sessionKey = data.saved && stableKey ? stableKey : selectedKeyBefore;
    const deviceName = data.device?.name || saved?.name || "Roku TV";
    if (data.saved && stableKey) migrateSession(selectedKeyBefore, stableKey);
    multiRoku.sessions.set(sessionKey, {
      deviceKey: sessionKey,
      host: data.roku_host || host,
      deviceName,
      token: data.token || "",
      channel,
      mediaUrl: data.media_url || "",
    });
    multiRoku.sessionKeyByHost.set(data.roku_host || host, sessionKey);

    guideState.roku.host = data.roku_host || host;
    guideState.roku.deviceName = deviceName;
    guideState.roku.relayToken = data.token || "";
    guideState.roku.active = true;
    localStorage.setItem("m3u-guide-roku-host", guideState.roku.host);
    if (data.saved && stableKey) localStorage.setItem("m3u-guide-roku-device-key", stableKey);
    guideEls.rokuHost.value = guideState.roku.host;
    showRokuPlayer();
    guideEls.playerMessage.textContent = `Playing on ${deviceName}.`;
    updateRokuControls(`Playing on ${deviceName} · ${data.media_url || "HLS relay active"}`);

    if (previousSession?.token && previousSession.token !== data.token) {
      await stopRokuRelayToken(previousSession.token);
    }
  };

  window.stopRokuPlayback = async function({sendHome = true, deviceKey = ""} = {}) {
    const selected = selectedSession();
    const host = selected?.host || selectedHost();
    const key = String(deviceKey || multiRoku.sessionKeyByHost.get(host) || selected?.deviceKey || selectedDeviceKey() || "");
    const session = multiRoku.sessions.get(key) || selected || null;
    const token = session?.token || "";
    const saved = multiRoku.savedByKey.get(key) || multiRoku.savedByHost.get(host) || null;

    if (token || (sendHome && host)) {
      try {
        await fetch("/api/guide/roku/stop", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            token,
            roku_host: sendHome && !saved ? host : "",
            roku_device_key: sendHome && saved ? saved.device_key : "",
          }),
          cache: "no-store",
        });
      } catch (error) {
        console.warn("Could not stop Roku playback", error);
      }
    }

    if (key) multiRoku.sessions.delete(key);
    if (host && multiRoku.sessionKeyByHost.get(host) === key) {
      multiRoku.sessionKeyByHost.delete(host);
    }
    syncSelectedSession();
  };

  window.toggleRoku = async function() {
    const session = selectedSession();
    if (session) {
      await stopRokuPlayback({sendHome: true, deviceKey: session.deviceKey});
      showLocalPlayer();
      if (guideState.currentChannel) {
        guideEls.playerMessage.textContent = `${session.deviceName || "Roku"} disconnected. Press Play to resume locally.`;
      }
      return;
    }

    if (!guideState.currentChannel) {
      updateRokuControls("Press Play on a channel first.");
      return;
    }

    guideEls.rokuBtn.disabled = true;
    try {
      await startRokuChannel(guideState.currentChannel);
    } catch (error) {
      console.error("Roku playback failed", error);
      guideEls.playerMessage.textContent = `Roku playback failed: ${error.message || error}.`;
      updateRokuControls(error.message || String(error));
    } finally {
      updateRokuControls(guideEls.rokuStatus.textContent);
    }
  };

  addButton.addEventListener("click", addSelectedDevice);
  select.addEventListener("change", () => {
    const saved = selectedSavedDevice();
    if (saved?.device_key) {
      localStorage.setItem("m3u-guide-roku-device-key", saved.device_key);
    }
    syncSelectedSession();
  });

  const optionObserver = new MutationObserver(() => {
    window.setTimeout(() => {
      refreshSavedDevices();
      annotateOptions();
    }, 0);
  });
  optionObserver.observe(select, {childList: true});

  discoverButton?.addEventListener("click", () => window.setTimeout(refreshDiscoveryMetadata, 300));
  refreshSavedDevices();
  window.setTimeout(refreshDiscoveryMetadata, 900);
})();
