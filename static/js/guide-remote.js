(() => {
  const castButton = document.getElementById("guideCastBtn");
  const rokuButton = document.getElementById("guideRokuBtn");
  const rokuSelect = document.getElementById("guideRokuDevice");
  const rokuRefreshButton = document.getElementById("guideRokuRefreshBtn");
  let castDisconnectSequence = 0;
  let castDisconnectHandoff = null;
  let remoteTransitionBusy = false;
  let discoveredRokus = [];

  function sameChannel(left, right) {
    if (!left || !right) return false;
    return String(left.play_url || "") === String(right.play_url || "");
  }

  function syncRemoteButtonStyle(button) {
    const active = String(button?.textContent || "").trim().startsWith("Disconnect");
    button?.classList.toggle("btn-success", active);
    button?.classList.toggle("btn-outline-light", !active);
    if (button === rokuButton) {
      if (rokuSelect) rokuSelect.disabled = active || remoteTransitionBusy;
      if (rokuRefreshButton) rokuRefreshButton.disabled = active || remoteTransitionBusy;
      if (guideEls.rokuHost) guideEls.rokuHost.disabled = active || remoteTransitionBusy;
      if (guideEls.rokuTestBtn) guideEls.rokuTestBtn.disabled = active || remoteTransitionBusy;
    }
  }

  const remoteButtonObserver = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      const button = mutation.target?.nodeType === Node.TEXT_NODE
        ? mutation.target.parentElement
        : mutation.target;
      if (button === castButton || button === rokuButton) syncRemoteButtonStyle(button);
    }
  });

  for (const button of [castButton, rokuButton]) {
    syncRemoteButtonStyle(button);
    remoteButtonObserver.observe(button, {childList: true, characterData: true, subtree: true});
  }

  function labelForRoku(device) {
    const name = String(device?.name || "Roku");
    const model = String(device?.model || "").trim();
    const host = String(device?.host || "").trim();
    return `${name}${model ? ` · ${model}` : ""}${host ? ` · ${host}` : ""}`;
  }

  function populateRokuSelect(devices, selectedKey = "") {
    if (!rokuSelect) return;
    rokuSelect.innerHTML = "";
    if (!devices.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No Roku auto-detected";
      rokuSelect.appendChild(option);
      return;
    }

    for (const device of devices) {
      const option = document.createElement("option");
      option.value = String(device?.device_key || device?.host || "");
      option.dataset.host = String(device?.host || "");
      option.textContent = labelForRoku(device);
      rokuSelect.appendChild(option);
    }

    if (selectedKey && [...rokuSelect.options].some(option => option.value === selectedKey)) {
      rokuSelect.value = selectedKey;
    }
  }

  function clearRokuDiscovery(message) {
    discoveredRokus = [];
    populateRokuSelect([]);
    const manualHost = String(guideEls.rokuHost?.value || "").trim();
    if (!manualHost) {
      guideState.roku.host = "";
      guideState.roku.deviceName = "Roku TV";
      rokuButton.classList.add("d-none");
      rokuButton.disabled = true;
      rokuButton.title = "";
    } else {
      rokuButton.classList.remove("d-none");
      updateRokuControls();
    }
    guideEls.rokuStatus.textContent = message || "No Roku found automatically. Manual IP still works.";
  }

  function useDiscoveredRoku(device) {
    const host = String(device?.host || "").trim();
    if (!host) return;
    const key = String(device?.device_key || host);
    guideState.roku.host = host;
    guideState.roku.deviceName = device?.name || "Roku TV";
    guideState.roku.deviceKey = key;
    guideEls.rokuHost.value = host;
    localStorage.setItem("m3u-guide-roku-host", host);
    localStorage.setItem("m3u-guide-roku-device", key);
    if (rokuSelect && [...rokuSelect.options].some(option => option.value === key)) {
      rokuSelect.value = key;
    }
    updateRokuControls(
      `Found ${guideState.roku.deviceName}${device?.model ? ` · ${device.model}` : ""} at ${host} via ${device?.discovery || "ECP"}.`
    );
    rokuButton.classList.remove("d-none");
    rokuButton.title = `${guideState.roku.deviceName} · ${host}`;
  }

  function selectedDiscoveredRoku() {
    const key = String(rokuSelect?.value || "");
    return discoveredRokus.find(device => String(device?.device_key || device?.host || "") === key) || null;
  }

  async function discoverRoku() {
    if (rokuRefreshButton) {
      rokuRefreshButton.disabled = true;
      rokuRefreshButton.textContent = "Scanning…";
    }
    guideEls.rokuStatus.textContent = "Discovering Roku devices with SSDP/ECP…";
    try {
      const response = await fetch(`/api/guide/roku/discover?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Roku discovery failed.");
      discoveredRokus = Array.isArray(data.devices) ? data.devices : [];
      if (!discoveredRokus.length) {
        clearRokuDiscovery(data.warning || "No Roku responded to SSDP/ECP discovery or the fallback scan.");
        return;
      }

      const savedKey = localStorage.getItem("m3u-guide-roku-device") || "";
      const savedHost = localStorage.getItem("m3u-guide-roku-host") || "";
      let selected = discoveredRokus.find(device =>
        String(device?.device_key || "") === savedKey
      );
      if (!selected) {
        selected = discoveredRokus.find(device => String(device?.host || "") === savedHost);
      }
      if (!selected) selected = discoveredRokus[0];

      populateRokuSelect(discoveredRokus, String(selected?.device_key || selected?.host || ""));
      useDiscoveredRoku(selected);
      if (discoveredRokus.length > 1) {
        guideEls.rokuStatus.textContent += ` ${discoveredRokus.length} Roku devices available.`;
      }
    } catch (error) {
      clearRokuDiscovery(`Roku discovery failed: ${error?.message || error}. Manual IP still works.`);
      console.error("Automatic Roku discovery failed", error);
    } finally {
      if (rokuRefreshButton) {
        rokuRefreshButton.disabled = guideState.roku.active;
        rokuRefreshButton.textContent = "Refresh";
      }
      syncRemoteButtonStyle(rokuButton);
    }
  }

  rokuSelect?.addEventListener("change", () => {
    if (guideState.roku.active) return;
    const device = selectedDiscoveredRoku();
    if (device) useDiscoveredRoku(device);
  });

  rokuRefreshButton?.addEventListener("click", discoverRoku);

  guideEls.rokuHost?.addEventListener("input", () => {
    if (guideState.roku.active) return;
    const host = String(guideEls.rokuHost.value || "").trim();
    if (host) {
      guideState.roku.host = host;
      guideState.roku.deviceName = "Roku TV";
      guideState.roku.deviceKey = host;
      rokuButton.classList.remove("d-none");
      rokuButton.title = `Roku · ${host}`;
    } else if (!discoveredRokus.length) {
      rokuButton.classList.add("d-none");
    }
  });

  function applyCastAvailability(state) {
    if (!window.cast?.framework || !guideState.cast.context) return false;
    const castState = state || guideState.cast.context.getCastState?.();
    const available = Boolean(castState) && castState !== cast.framework.CastState.NO_DEVICES_AVAILABLE;
    castButton.classList.toggle("d-none", !available);
    if (available) {
      castButton.title = "Google Cast receiver available";
      updateCastStatus();
    } else {
      castButton.title = "";
      guideEls.castStatus.textContent = "No Google Cast receivers found.";
    }
    return true;
  }

  function attachCastDiscovery(attempt) {
    const count = Number(attempt || 0);
    if (window.cast?.framework && guideState.cast.context) {
      applyCastAvailability();
      guideState.cast.context.addEventListener(
        cast.framework.CastContextEventType.CAST_STATE_CHANGED,
        event => applyCastAvailability(event.castState)
      );
      return;
    }
    if (count < 100) window.setTimeout(() => attachCastDiscovery(count + 1), 100);
  }

  function setRemoteTransitionBusy(busy) {
    remoteTransitionBusy = Boolean(busy);
    if (remoteTransitionBusy) {
      castButton.disabled = true;
      rokuButton.disabled = true;
    }
    if (rokuSelect) rokuSelect.disabled = remoteTransitionBusy || guideState.roku.active;
    if (rokuRefreshButton) rokuRefreshButton.disabled = remoteTransitionBusy || guideState.roku.active;
    if (guideEls.rokuHost) guideEls.rokuHost.disabled = remoteTransitionBusy || guideState.roku.active;
    if (guideEls.rokuTestBtn) guideEls.rokuTestBtn.disabled = remoteTransitionBusy || guideState.roku.active;
  }

  async function withRemoteTransition(work) {
    if (remoteTransitionBusy) return;
    setRemoteTransitionBusy(true);
    try {
      await work();
    } finally {
      remoteTransitionBusy = false;
      updateRokuControls();
      updateCastStatus();
      syncRemoteButtonStyle(castButton);
      syncRemoteButtonStyle(rokuButton);
    }
  }

  async function waitForCastSessionEnd(timeoutMs = 4000) {
    const deadline = Date.now() + timeoutMs;
    while (currentCastSession() && Date.now() < deadline) {
      await new Promise(resolve => window.setTimeout(resolve, 75));
    }
    return !currentCastSession();
  }

  async function stopCastForRemoteSwitch() {
    if (!currentCastSession()) return;
    await stopRemoteMedia();
    await stopCastRelay();
    guideState.cast.lastMediaUrl = "";
    guideState.cast.context.endCurrentSession(true);
    const ended = await waitForCastSessionEnd();
    if (!ended) throw new Error("Cast session did not disconnect before Roku handoff.");
  }

  function finishCastDisconnectHandoff(id) {
    const pending = castDisconnectHandoff;
    if (!pending || pending.id !== id) return;
    castDisconnectHandoff = null;
    if (!pending.channel) return;
    if (currentCastSession()) return;
    if (!sameChannel(guideState.currentChannel, pending.channel)) return;
    playLocalChannel(pending.channel);
  }

  async function disconnectRokuToLocal() {
    if (!guideState.roku.active) return;
    const channel = guideState.currentChannel;
    await stopRokuPlayback({sendHome: true});
    if (channel && sameChannel(guideState.currentChannel, channel) && !guideState.roku.active) {
      playLocalChannel(channel);
    }
  }

  async function disconnectCastToLocal() {
    if (!currentCastSession()) return;
    const channel = guideState.currentChannel;
    const id = ++castDisconnectSequence;
    castDisconnectHandoff = {id, channel: channel || null};
    try {
      await stopRemoteMedia();
      await stopCastRelay();
      guideState.cast.lastMediaUrl = "";
      guideState.cast.context.endCurrentSession(true);
      const ended = await waitForCastSessionEnd();
      if (ended) finishCastDisconnectHandoff(id);
    } catch (error) {
      if (castDisconnectHandoff?.id === id) castDisconnectHandoff = null;
      console.error("Could not hand Cast playback back to the browser", error);
    }
  }

  async function handleRokuButtonClick(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (remoteTransitionBusy) return;

    await withRemoteTransition(async () => {
      if (guideState.roku.active) {
        await disconnectRokuToLocal();
        return;
      }

      const channel = guideState.currentChannel;
      if (!channel) {
        updateRokuControls("Press Play on a channel first.");
        return;
      }

      if (currentCastSession()) {
        await stopCastForRemoteSwitch();
      }
      if (sameChannel(guideState.currentChannel, channel)) {
        await toggleRoku();
      }
    });
  }

  async function handleCastButtonClick(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (remoteTransitionBusy) return;

    await withRemoteTransition(async () => {
      if (currentCastSession()) {
        await disconnectCastToLocal();
        return;
      }
      await toggleCast();
    });
  }

  function attachCastDisconnectHandoff(attempt) {
    const count = Number(attempt || 0);
    if (window.cast?.framework && guideState.cast.context) {
      guideState.cast.context.addEventListener(
        cast.framework.CastContextEventType.SESSION_STATE_CHANGED,
        event => {
          if (event.sessionState !== cast.framework.SessionState.SESSION_ENDED) return;
          const pending = castDisconnectHandoff;
          if (!pending) return;
          finishCastDisconnectHandoff(pending.id);
        }
      );
      return;
    }
    if (count < 100) window.setTimeout(() => attachCastDisconnectHandoff(count + 1), 100);
  }

  rokuButton.addEventListener("click", handleRokuButtonClick, true);
  castButton.addEventListener("click", handleCastButtonClick, true);
  discoverRoku();
  attachCastDiscovery(0);
  attachCastDisconnectHandoff(0);
})();
