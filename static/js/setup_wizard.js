(() => {
  "use strict";

  const ctx = {
    payload: null,
    step: "choices",
    busy: false,
    channelIds: new Set(),
    channelsInitialized: false,
    visibleChannelIds: [],
    channelMatchingTotal: 0,
    sportsKeys: new Set(),
    sportsItems: [],
    sportsByKey: new Map(),
  };

  const body = () => document.getElementById("setupBody");
  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch {}
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }

  function setBusy(value, message = "") {
    ctx.busy = Boolean(value);
    body()?.querySelectorAll("button, input, select").forEach(element => {
      element.disabled = ctx.busy || element.dataset.modeDisabled === "true";
    });
    if (message) setStatus(message);
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("setupStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `setup-status${kind ? ` ${kind}` : ""}`;
  }

  function features() {
    return ctx.payload?.state?.features || {};
  }

  function updateChannelResultCount() {
    const count = document.getElementById("channelResultCount");
    if (!count) return;
    const shown = ctx.visibleChannelIds.length;
    const selectedShown = ctx.visibleChannelIds.filter(id => ctx.channelIds.has(id)).length;
    const matching = Number(ctx.channelMatchingTotal || shown);
    const matchCopy = matching > shown
      ? `${shown.toLocaleString()} shown of ${matching.toLocaleString()} matches`
      : `${matching.toLocaleString()} matching`;
    count.textContent = `${matchCopy} · ${selectedShown.toLocaleString()} shown selected · ${ctx.channelIds.size.toLocaleString()} total selected`;
  }

  function isTesting() {
    return ctx.payload?.state?.mode === "testing";
  }

  function steps() {
    const values = [{id: "choices", label: "Start"}];
    if (!isTesting()) values.push({id: "provider", label: "Provider"});
    values.push({id: "channels", label: "Channels"});
    if (!isTesting()) values.push(
      {id: "sports", label: "Sports"},
      {id: "api", label: "Sports API"},
      {id: "dvr", label: "DVR"},
      {id: "media", label: "Media Server"},
    );
    values.push({id: "build", label: "Build"});
    return values;
  }

  function renderProgress() {
    const items = steps();
    const activeIndex = Math.max(0, items.findIndex(item => item.id === ctx.step));
    document.getElementById("setupProgress").innerHTML = items.map((item, index) => {
      const state = index < activeIndex ? " complete" : index === activeIndex ? " active" : "";
      return `<div class="setup-progress-item${state}">${esc(item.label)}</div>`;
    }).join("");
  }

  function actions({back = true, next = "Continue", nextId = "setupNext"} = {}) {
    return `<div class="setup-actions">
      ${back ? '<button id="setupBack" class="setup-btn" type="button">Back</button>' : "<span></span>"}
      <div class="setup-actions-right"><button id="${nextId}" class="setup-btn primary" type="button">${esc(next)}</button></div>
    </div><div id="setupStatus" class="setup-status" role="status" aria-live="polite"></div>`;
  }

  function move(offset) {
    const list = steps();
    const index = list.findIndex(item => item.id === ctx.step);
    const target = list[index + offset];
    if (target) {
      ctx.step = target.id;
      render();
    }
  }

  function bindBack() {
    document.getElementById("setupBack")?.addEventListener("click", () => move(-1));
  }

  function heading(title, help) {
    return `<div class="setup-heading"><div><h2>${esc(title)}</h2><p>${esc(help)}</p></div></div>`;
  }

  function renderChoices() {
    const state = ctx.payload.state;
    const selectedMode = state.mode || "testing";
    body().innerHTML = `${heading("How do you want to start?", "Choose the channel source first. You will choose optional features after the provider and channel lineup are ready.")}
      <div class="setup-choice-grid">
        <label class="setup-choice">
          <input type="radio" name="setupMode" value="testing" ${selectedMode === "testing" ? "checked" : ""}>
          <strong>Just Testing</strong>
          <span>Load a free public U.S. lineup and go directly to channel selection.</span>
        </label>
        <label class="setup-choice">
          <input type="radio" name="setupMode" value="provider" ${selectedMode === "provider" ? "checked" : ""}>
          <strong>Use My Provider</strong>
          <span>Validate an M3U or Xtream provider, then configure the full application.</span>
        </label>
      </div>
      <p class="setup-help" style="margin-top:14px">Just Testing uses the free public lineup and skips optional feature configuration.</p>
      ${actions({back: false, next: "Continue"})}`;

    document.getElementById("setupNext").addEventListener("click", async () => {
      const mode = document.querySelector('input[name="setupMode"]:checked')?.value || "testing";
      setBusy(true, mode === "testing" ? "Loading the free testing lineup…" : "Saving source choice…");
      try {
        const data = await api("/api/setup/choices", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({mode}),
        });
        ctx.payload.state = data.state;
        if (mode === "testing" && !ctx.payload.provider_configured) {
          const provider = await api("/api/setup/provider", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
          ctx.payload.state = provider.state;
          ctx.payload.provider_configured = true;
          ctx.payload.channel_count = provider.channel_count;
        }
        ctx.step = mode === "testing" ? "channels" : "provider";
        render();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      }
    });
  }

  function renderProvider() {
    body().innerHTML = `${heading("Connect your provider", "Enter either a direct M3U URL or an Xtream server URL with both credentials. Nothing advances until the provider returns a valid catalog.")}
      <div class="setup-grid">
        <div class="setup-field"><label for="providerName">Provider name</label><input id="providerName" value="Primary" autocomplete="off"></div>
        <div class="setup-field wide"><label for="providerUrl">Provider or M3U URL</label><input id="providerUrl" placeholder="https://provider.example:8080" autocomplete="url"></div>
        <div class="setup-field"><label for="providerUsername">Xtream username</label><input id="providerUsername" autocomplete="username"></div>
        <div class="setup-field"><label for="providerPassword">Xtream password</label><input id="providerPassword" type="password" autocomplete="current-password"></div>
      </div>${actions({next: "Validate & Load"})}`;
    bindBack();
    document.getElementById("setupNext").addEventListener("click", async () => {
      const payload = {
        name: document.getElementById("providerName").value.trim() || "Primary",
        url: document.getElementById("providerUrl").value.trim(),
        username: document.getElementById("providerUsername").value,
        password: document.getElementById("providerPassword").value,
      };
      if (!payload.url) return setStatus("Enter the provider URL.", "error");
      setBusy(true, "Validating the provider and loading its channels…");
      try {
        const data = await api("/api/setup/provider", {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
        });
        ctx.payload.state = data.state;
        ctx.payload.provider_configured = true;
        ctx.payload.channel_count = data.channel_count;
        ctx.step = "channels";
        render();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      }
    });
  }

  async function fetchChannels() {
    const query = document.getElementById("channelSearch")?.value || "";
    const group = document.getElementById("channelGroup")?.value || "";
    const hideSd = Boolean(document.getElementById("hideSdChannels")?.checked);
    const data = await api(`/api/setup/channels?q=${encodeURIComponent(query)}&group=${encodeURIComponent(group)}&hide_sd=${hideSd ? "1" : "0"}`);
    if (!ctx.channelsInitialized) {
      (data.selected_ids || []).forEach(id => ctx.channelIds.add(Number(id)));
      ctx.channelsInitialized = true;
    }
    ctx.visibleChannelIds = (data.channels || []).map(item => Number(item.id));
    ctx.channelMatchingTotal = Number(data.total || ctx.visibleChannelIds.length);
    const list = document.getElementById("channelList");
    if (list) list.innerHTML = data.channels?.length ? data.channels.map(item => `
      <label class="setup-list-item">
        <input class="channel-check" type="checkbox" value="${Number(item.id)}" ${ctx.channelIds.has(Number(item.id)) ? "checked" : ""}>
        ${item.tvg_logo ? `<img src="${esc(item.tvg_logo)}" alt="" loading="lazy">` : "<span></span>"}
        <span><strong>${esc(item.name)}</strong><small>${esc(item.group || "Ungrouped")}</small></span>
      </label>`).join("") : '<div class="setup-empty">No matching channels.</div>';
    updateChannelResultCount();
    const groupSelect = document.getElementById("channelGroup");
    if (groupSelect) {
      const previous = groupSelect.value;
      groupSelect.innerHTML = '<option value="">All groups</option>';
      (data.groups || []).forEach(value => groupSelect.add(new Option(value, value)));
      if ((data.groups || []).includes(previous)) groupSelect.value = previous;
    }
  }

  function renderChannels() {
    body().innerHTML = `<div class="setup-heading setup-channel-heading"><div>
        <h2>Choose your channels</h2>
        <div class="setup-channel-status">
          <label class="setup-check compact"><input id="hideSdChannels" type="checkbox" ${ctx.payload.state.channels.hide_sd ? "checked" : ""}><span>Hide SD / Low Bandwidth channels</span></label>
          <span id="channelResultCount"></span>
        </div>
      </div></div>
      <div class="setup-search"><input id="channelSearch" type="search" placeholder="Search channels or groups…"><select id="channelGroup"><option value="">All groups</option></select></div>
      <div class="setup-tools"><button id="selectVisible" class="setup-btn" type="button">Select visible</button><button id="clearChannels" class="setup-btn" type="button">Clear selection</button></div>
      <div id="channelList" class="setup-list"><div class="setup-empty">Loading channels…</div></div>
      ${actions({next: "Save Channels"})}`;
    bindBack();
    let timer;
    document.getElementById("channelSearch").addEventListener("input", () => {
      clearTimeout(timer); timer = setTimeout(() => fetchChannels().catch(error => setStatus(error.message, "error")), 180);
    });
    document.getElementById("channelGroup").addEventListener("change", () => fetchChannels().catch(error => setStatus(error.message, "error")));
    document.getElementById("hideSdChannels").addEventListener("change", () => fetchChannels().catch(error => setStatus(error.message, "error")));
    document.getElementById("channelList").addEventListener("change", event => {
      const input = event.target.closest(".channel-check");
      if (!input) return;
      const id = Number(input.value);
      if (input.checked) ctx.channelIds.add(id); else ctx.channelIds.delete(id);
      updateChannelResultCount();
    });
    document.getElementById("selectVisible").addEventListener("click", () => {
      ctx.visibleChannelIds.forEach(id => ctx.channelIds.add(id));
      document.querySelectorAll(".channel-check").forEach(input => { input.checked = true; });
      updateChannelResultCount();
    });
    document.getElementById("clearChannels").addEventListener("click", () => {
      ctx.channelIds.clear();
      document.querySelectorAll(".channel-check").forEach(input => { input.checked = false; });
      updateChannelResultCount();
    });
    document.getElementById("setupNext").addEventListener("click", async () => {
      if (!ctx.channelIds.size) return setStatus("Choose at least one channel.", "error");
      setBusy(true, "Saving the curated channel lineup…");
      try {
        const data = await api("/api/setup/channels", {
          method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
            ids: [...ctx.channelIds],
            hide_sd: document.getElementById("hideSdChannels").checked,
          }),
        });
        ctx.payload.state = data.state;
        move(1);
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
    fetchChannels().catch(error => setStatus(error.message, "error"));
  }

  function renderDvr() {
    const values = ctx.payload.state.dvr;
    const enabled = Boolean(features().dvr);
    body().innerHTML = `${heading("Set up DVR", "Choose whether this installation should record programs. Media-server export comes next.")}
      <label class="setup-check"><input id="dvrEnabled" type="checkbox" ${enabled ? "checked" : ""}><span><strong>Enable DVR</strong><br>Record programs from the guide and convert completed recordings to H.265.</span></label>
      <div id="dvrFields" class="setup-grid" ${enabled ? "" : "hidden"} style="margin-top:18px">
        <div class="setup-field wide"><label for="dvrPath">DVR recording folder</label><input id="dvrPath" value="${esc(values.host_path || "C:/DVR")}" placeholder="C:/DVR"></div>
        <div class="setup-field"><label for="dvrConcurrent">Maximum simultaneous recordings</label><input id="dvrConcurrent" type="number" min="1" max="8" value="${Number(values.max_concurrent_recordings || 2)}"></div>
        <label class="setup-check"><input id="dvrImmediate" type="checkbox" ${values.process_immediately ? "checked" : ""}><span>Convert each recording immediately after it finishes</span></label>
        <label class="setup-check"><input id="dvrCommercials" type="checkbox" ${values.remove_commercials ? "checked" : ""}><span>Run commercial detection before H.265 conversion</span></label>
      </div>
      ${actions({next: "Continue"})}`;
    bindBack();
    const enabledToggle = document.getElementById("dvrEnabled");
    const syncDvrFields = () => {
      const fields = document.getElementById("dvrFields");
      fields.hidden = !enabledToggle.checked;
      fields.querySelectorAll("input").forEach(input => {
        input.disabled = !enabledToggle.checked;
      });
    };
    enabledToggle.addEventListener("change", syncDvrFields);
    syncDvrFields();
    document.getElementById("setupNext").addEventListener("click", async () => {
      setBusy(true, "Saving DVR configuration…");
      try {
        const data = await api("/api/setup/dvr", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
          enabled: enabledToggle.checked,
          host_path: document.getElementById("dvrPath").value,
          process_immediately: document.getElementById("dvrImmediate").checked,
          remove_commercials: document.getElementById("dvrCommercials").checked,
          max_concurrent_recordings: Number(document.getElementById("dvrConcurrent").value || 2),
        })});
        ctx.payload.state = data.state; move(1);
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
  }

  function renderMediaServer() {
    const state = ctx.payload.state;
    const selected = state.media_server?.type || "none";
    const dvrEnabled = Boolean(state.features.dvr);
    body().innerHTML = `${heading("Choose a media server", "This controls optional library integration only. Browser playback, Cast, Roku, and the M3U Web Picker DVR Library remain available with every choice.")}
      <div class="setup-choice-grid setup-choice-grid-three">
        <label class="setup-choice">
          <input type="radio" name="mediaServer" value="none" ${selected === "none" ? "checked" : ""}>
          <strong>No media server</strong>
          <span>Keep recordings in M3U Web Picker and use its built-in Library.</span>
        </label>
        <label class="setup-choice">
          <input type="radio" name="mediaServer" value="jellyfin" ${selected === "jellyfin" ? "checked" : ""}>
          <strong>Jellyfin</strong>
          <span>Configure the optional Jellyfin cache integration.</span>
        </label>
        <label class="setup-choice${dvrEnabled ? "" : " disabled"}">
          <input type="radio" name="mediaServer" value="plex" ${selected === "plex" ? "checked" : ""} ${dvrEnabled ? "" : "disabled"}>
          <strong>Plex</strong>
          <span>${dvrEnabled ? "Export completed DVR recordings to a Plex library folder." : "Enable DVR first to export recordings to Plex."}</span>
        </label>
      </div>
      <div id="jellyfinMediaFields" ${selected === "jellyfin" ? "" : "hidden"} style="margin-top:18px">
        <div class="setup-field"><label for="jellyfinPath">Jellyfin cache folder</label><input id="jellyfinPath" value="${esc(state.jellyfin.cache_path || "")}" placeholder="C:/ProgramData/Jellyfin/Server/cache"></div>
        <label class="setup-check"><input id="jellyfinCleanup" type="checkbox" ${state.jellyfin.cleanup_enabled ? "checked" : ""}><span>Clear stale cache data after successful updates</span></label>
        <label class="setup-check"><input id="jellyfinAck" type="checkbox" ${state.jellyfin.acknowledged ? "checked" : ""}><span>I understand cleanup can affect cached information for downloaded media and recordings.</span></label>
      </div>
      <div id="plexMediaFields" ${selected === "plex" ? "" : "hidden"} style="margin-top:18px">
        <div class="setup-field"><label for="plexPath">Plex library folder</label><input id="plexPath" value="${esc(state.dvr.server_path || "")}" placeholder="C:/DVR/PLEX"></div>
        <p class="setup-help">The production installer will mount this folder with the DVR storage configuration.</p>
      </div>
      ${actions({next: "Save Media Server"})}`;
    bindBack();
    const sync = () => {
      const value = document.querySelector('input[name="mediaServer"]:checked')?.value || "none";
      document.getElementById("jellyfinMediaFields").hidden = value !== "jellyfin";
      document.getElementById("plexMediaFields").hidden = value !== "plex";
    };
    document.querySelectorAll('input[name="mediaServer"]').forEach(input => input.addEventListener("change", sync));
    sync();
    document.getElementById("setupNext").addEventListener("click", async () => {
      const serverType = document.querySelector('input[name="mediaServer"]:checked')?.value || "none";
      setBusy(true, "Saving media-server choice…");
      try {
        const data = await api("/api/setup/media-server", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            type: serverType,
            cache_path: document.getElementById("jellyfinPath").value,
            cleanup_enabled: document.getElementById("jellyfinCleanup").checked,
            acknowledged: document.getElementById("jellyfinAck").checked,
            plex_path: document.getElementById("plexPath").value,
          }),
        });
        ctx.payload.state = data.state; move(1);
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
  }

  async function fetchSports(query = "") {
    const data = await api(`/api/setup/sports/catalog?q=${encodeURIComponent(query)}`);
    ctx.sportsItems = data.items || [];
    ctx.sportsItems.forEach(item => ctx.sportsByKey.set(`${item.scope_type}:${item.id}`, item));
    const list = document.getElementById("sportsList");
    if (!list) return;
    list.innerHTML = ctx.sportsItems.length ? ctx.sportsItems.map(item => {
      const key = `${item.scope_type}:${item.id}`;
      return `<label class="setup-list-item setup-sports-item"><input class="sports-check" type="checkbox" data-key="${esc(key)}" ${ctx.sportsKeys.has(key) ? "checked" : ""}><span><strong>${esc(item.name)}</strong><small>${esc(item.subtitle || item.scope_type)}</small></span></label>`;
    }).join("") : '<div class="setup-empty">No matching teams or leagues.</div>';
  }

  function renderSports() {
    const enabled = Boolean(ctx.payload.state.sports.enabled);
    body().innerHTML = `${heading("Sports Automation", "Choose teams and leagues without writing regular expressions. Provider and EPG matching work even when the optional schedule API is off.")}
      <label class="setup-check"><input id="sportsEnabled" type="checkbox" ${enabled ? "checked" : ""}><span><strong>Enable Sports Automation</strong></span></label>
      <div id="sportsPicker" ${enabled ? "" : "hidden"}>
        <div class="setup-search" style="margin-top:16px"><input id="sportsSearch" type="search" placeholder="Search teams or leagues…"></div>
        <div id="sportsList" class="setup-list"><div class="setup-empty">Loading sports catalog…</div></div>
      </div>${actions({next: "Save Sports"})}`;
    bindBack();
    if (!ctx.sportsKeys.size) (ctx.payload.sports.rules || []).forEach(rule => ctx.sportsKeys.add(`${rule.scope_type}:${rule.scope_id}`));
    const toggle = document.getElementById("sportsEnabled");
    toggle.addEventListener("change", () => { document.getElementById("sportsPicker").hidden = !toggle.checked; });
    document.getElementById("sportsList").addEventListener("change", event => {
      const input = event.target.closest(".sports-check"); if (!input) return;
      if (input.checked) ctx.sportsKeys.add(input.dataset.key); else ctx.sportsKeys.delete(input.dataset.key);
    });
    let timer;
    document.getElementById("sportsSearch").addEventListener("input", event => {
      clearTimeout(timer); timer = setTimeout(() => fetchSports(event.target.value).catch(error => setStatus(error.message, "error")), 180);
    });
    document.getElementById("setupNext").addEventListener("click", async () => {
      const enabledNow = toggle.checked;
      const items = [...ctx.sportsKeys].map(key => ctx.sportsByKey.get(key)).filter(Boolean).map(item => ({scope_type: item.scope_type, scope_id: item.id, feed_preference: "best"}));
      if (enabledNow && !items.length) return setStatus("Choose at least one team or league, or turn Sports Automation off.", "error");
      setBusy(true, "Saving sports choices…");
      try {
        const data = await api("/api/setup/sports", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: enabledNow, items})});
        ctx.payload.state = data.state; ctx.payload.sports.rules = data.rules || []; move(1);
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
    fetchSports().catch(error => setStatus(error.message, "error"));
  }

  function renderApi() {
    const enabled = Boolean(features().sports_api);
    body().innerHTML = `${heading("Add Sports API schedules?", "Sports Automation works without this. API-SPORTS adds canonical schedules for supported leagues and teams.")}
      <p class="setup-help setup-api-link">Need a key? <a href="https://api-sports.io" target="_blank" rel="noopener noreferrer">Sign up with API-SPORTS ↗</a>, then enable it below.</p>
      <label class="setup-check"><input id="sportsApiEnabled" type="checkbox" ${enabled ? "checked" : ""}><span><strong>Use API-SPORTS schedules</strong><br>Optional; provider and EPG matching remain available when this is off.</span></label>
      <div id="sportsApiFields" ${enabled ? "" : "hidden"} style="margin-top:18px">
        <div class="setup-field"><label for="sportsApiKey">API-SPORTS key</label><input id="sportsApiKey" type="password" autocomplete="new-password" placeholder="API key"></div>
      </div>
      ${actions({next: "Continue"})}`;
    bindBack();
    const enabledToggle = document.getElementById("sportsApiEnabled");
    enabledToggle.addEventListener("change", () => {
      document.getElementById("sportsApiFields").hidden = !enabledToggle.checked;
    });
    document.getElementById("setupNext").addEventListener("click", async () => {
      setBusy(true, enabledToggle.checked ? "Saving the API key…" : "Skipping Sports API…");
      try {
        const data = await api("/api/setup/sports-api", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
          enabled: enabledToggle.checked,
          api_key: document.getElementById("sportsApiKey").value,
        })});
        ctx.payload.state = data.state; move(1);
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
  }

  function yesNo(value) { return value ? "Enabled" : "Not selected"; }

  function formatElapsed(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const minutes = Math.floor(value / 60);
    const remainder = Math.floor(value % 60);
    return minutes ? `${minutes}m ${String(remainder).padStart(2, "0")}s` : `${remainder}s`;
  }

  function renderInitialUpdate(update = {}) {
    ctx.step = "build";
    renderProgress();
    body().innerHTML = `<div class="setup-success setup-update-progress">
      <div class="setup-spinner" aria-hidden="true"></div>
      <h2>Preparing your guide</h2>
      <p>The first application update is loading channel and guide data. This can take several minutes.</p>
      <strong id="setupUpdateElapsed">Running for ${esc(formatElapsed(update.elapsed_seconds))}</strong>
      <div id="setupStatus" class="setup-status" role="status" aria-live="polite">Keep this page open. The guide will open automatically when it is ready.</div>
    </div>`;
  }

  async function watchInitialUpdate(initialUpdate = {}) {
    renderInitialUpdate(initialUpdate);
    while (true) {
      await new Promise(resolve => window.setTimeout(resolve, 1500));
      try {
        const data = await api(`/api/setup/build-status?_=${Date.now()}`);
        ctx.payload.state = data.state;
        const update = data.master_update || {};
        const elapsed = document.getElementById("setupUpdateElapsed");
        if (elapsed) elapsed.textContent = `Running for ${formatElapsed(update.elapsed_seconds)}`;
        const status = data.state?.initial_update?.status;
        if (status === "complete" && data.state?.completed) {
          body().innerHTML = `<div class="setup-success"><h2>Your guide is ready</h2><p>Opening M3U Web Picker on port 9998…</p></div>`;
          window.setTimeout(() => window.location.replace(data.launch_url || "/"), 500);
          return;
        }
        if (status === "failed") {
          const message = data.state?.initial_update?.error || "The first guide update did not complete.";
          body().innerHTML = `<div class="setup-success setup-update-failed"><h2>The guide update needs another try</h2><p>${esc(message)}</p><button id="setupRetryBuild" class="setup-btn primary" type="button">Back to Build</button></div>`;
          document.getElementById("setupRetryBuild")?.addEventListener("click", renderBuild);
          return;
        }
      } catch (error) {
        setStatus(`Still waiting for the application: ${error.message}`, "error");
      }
    }
  }

  function renderBuild() {
    const state = ctx.payload.state;
    body().innerHTML = `${heading("Ready to build", "Review the choices. Build & Restart will open a fully configured, isolated copy of M3U Web Picker on port 9998.")}
      <div class="setup-summary">
        <div class="setup-summary-row"><span>Source</span><strong>${state.mode === "testing" ? "Free testing lineup" : esc(state.provider.name || "Validated provider")}</strong></div>
        <div class="setup-summary-row"><span>Selected channels</span><strong>${Number(state.channels.selected_count || 0).toLocaleString()}</strong></div>
        <div class="setup-summary-row"><span>DVR</span><strong>${yesNo(state.features.dvr)}</strong></div>
        <div class="setup-summary-row"><span>Media server</span><strong>${state.media_server?.type === "jellyfin" ? "Jellyfin" : state.media_server?.type === "plex" ? "Plex" : "None"}</strong></div>
        <div class="setup-summary-row"><span>Sports Automation</span><strong>${yesNo(state.sports.enabled)}</strong></div>
        <div class="setup-summary-row"><span>Sports API</span><strong>${yesNo(state.features.sports_api)}</strong></div>
      </div>${actions({next: "Build & Restart"})}`;
    bindBack();
    document.getElementById("setupNext").addEventListener("click", async () => {
      setBusy(true, "Generating host configuration and handoff manifest…");
      try {
        const data = await api("/api/setup/build", {method: "POST"});
        ctx.payload.state = data.state;
        watchInitialUpdate(data.master_update || {});
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
  }

  function render() {
    renderProgress();
    if (ctx.step === "choices") renderChoices();
    else if (ctx.step === "provider") renderProvider();
    else if (ctx.step === "channels") renderChannels();
    else if (ctx.step === "dvr") renderDvr();
    else if (ctx.step === "sports") renderSports();
    else if (ctx.step === "api") renderApi();
    else if (ctx.step === "media") renderMediaServer();
    else renderBuild();
  }

  async function start() {
    try {
      ctx.payload = await api("/api/setup/state");
      if (["starting", "running"].includes(ctx.payload.state?.initial_update?.status)) {
        watchInitialUpdate();
        return;
      }
      ctx.step = ctx.payload.state.completed ? "build" : (ctx.payload.state.current_step || "choices");
      if (!steps().some(item => item.id === ctx.step)) ctx.step = "choices";
      render();
    } catch (error) {
      body().innerHTML = `<div class="setup-success"><h2>Setup could not start</h2><p>${esc(error.message)}</p></div>`;
    }
  }

  document.getElementById("setupReset")?.addEventListener("click", async () => {
    if (!window.confirm("Start over and clear only this isolated port-9998 setup?")) return;
    try {
      const data = await api("/api/setup/reset", {method: "POST"});
      ctx.payload = data;
      ctx.step = "choices";
      ctx.channelIds.clear();
      ctx.channelsInitialized = false;
      ctx.sportsKeys.clear();
      ctx.sportsItems = [];
      ctx.sportsByKey.clear();
      render();
    } catch (error) {
      if (document.getElementById("setupStatus")) {
        setStatus(error.message, "error");
      } else {
        body().innerHTML = `<div class="setup-success"><h2>Reset failed</h2><p>${esc(error.message)}</p></div>`;
      }
    }
  });

  start();
})();
