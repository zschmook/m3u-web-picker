(() => {
  "use strict";

  const PAGE_IDS = ["overview", "providers", "channels", "sports", "settings"];
  const SETTINGS_PANEL_IDS = ["encoding", "dvr", "network", "jellyfin", "epg", "devices"];
  const state = {
    status: null,
    activePage: "overview",
    pollTimer: null,
    elapsedTimer: null,
    refreshInFlight: false,
    refreshQueued: false,
  };

  const el = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const icons = {
    overview: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/></svg>',
    providers: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v4H5zM5 11h14v4H5zM5 17h14v2H5z"/></svg>',
    channels: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h14M8 4v4M14 10v4M11 16v4"/></svg>',
    epg: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16v13H4zM8 3v6M16 3v6M4 10h16"/></svg>',
    sports: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M8 8l8 8M16 8l-8 8"/></svg>',
    devices: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M9 20h6M12 16v4"/></svg>',
    settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z"/></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M19 13v6H5V5h6"/></svg>',
  };

  function formatNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString() : "0";
  }

  function formatTime(value, fallback = "—") {
    if (!value) return fallback;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function formatElapsed(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(value / 60);
    const remainder = value % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function buildShell() {
    if (!document.getElementById("uiAppShell") || !document.getElementById("uiModernRoot")) return false;

    const mobileButton = el("uiSidebarToggle");
    const scrim = el("uiSidebarScrim");
    mobileButton?.addEventListener("click", () => document.body.classList.toggle("ui-sidebar-open"));
    scrim?.addEventListener("click", () => document.body.classList.remove("ui-sidebar-open"));
    return true;
  }

  function makePage(id, title, subtitle = "") {
    const section = document.createElement("section");
    section.id = `uiPage-${id}`;
    section.className = "ui-nav-page";
    section.dataset.uiPage = id;
    section.innerHTML = `
      <header class="ui-page-header">
        <div>
          <div class="ui-page-eyebrow">M3U Web Picker</div>
          <h1>${escapeHtml(title)}</h1>
          ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ""}
        </div>
      </header>`;
    return section;
  }

  function preparePages() {
    const root = document.querySelector(".ui-modern-root");
    if (!root) return;

    document.querySelector(".app-topbar")?.classList.add("ui-legacy-hidden");
    document.querySelector(".ui-jump-nav")?.classList.add("ui-legacy-hidden");
    document.querySelector("#uiOutputsDetails")?.classList.add("ui-legacy-hidden");
    document.querySelectorAll(".ui-section-separator, .ui-section-anchor").forEach(node => node.classList.add("ui-legacy-hidden"));

    const providerCard = el("providerSources")?.closest(".card");
    if (providerCard) {
      providerCard.dataset.uiPage = "providers";
      providerCard.classList.add("ui-nav-page", "ui-page-card-root");
      const body = providerCard.querySelector(":scope > .card-body");
      if (body && !body.querySelector(".ui-page-inline-heading")) {
        const intro = document.createElement("div");
        intro.className = "ui-page-inline-heading";
        intro.innerHTML = '<div class="ui-page-eyebrow">Sources</div><h1>Providers</h1><p>Manage the primary IPTV catalog and ordered sports fallbacks.</p>';
        body.prepend(intro);
      }
    }

    const channelShell = el("uiChannelSectionShell") || el("channelManagerHeader")?.parentElement;
    if (channelShell) {
      channelShell.dataset.uiPage = "channels";
      channelShell.classList.add("ui-nav-page", "ui-page-card-root");
      if (!channelShell.querySelector(":scope > .ui-page-inline-heading")) {
        const heading = document.createElement("div");
        heading.className = "ui-page-inline-heading ui-page-heading-before-section";
        heading.innerHTML = '<div class="ui-page-eyebrow">Lineup</div><h1>Channels</h1><p>Search, index, filter, and order the channels in your curated lineup.</p>';
        channelShell.prepend(heading);
      }
    }

    const sportsCard = el("sportsSectionTitle")?.closest(".sports-card");
    if (sportsCard) {
      sportsCard.dataset.uiPage = "sports";
      sportsCard.classList.add("ui-nav-page", "ui-page-card-root");
      const body = sportsCard.querySelector(":scope > .card-body");
      if (body && !body.querySelector(".ui-page-inline-heading")) {
        const heading = document.createElement("div");
        heading.className = "ui-page-inline-heading";
        heading.innerHTML = '<div class="ui-page-eyebrow">Automation</div><h1>Sports Automation</h1><p>Build event channels from your rules, provider feeds, EPG data, and schedule APIs.</p>';
        body.prepend(heading);
      }
    }

    const epgPanel = document.createElement("div");
    epgPanel.className = "ui-settings-panel ui-settings-collection";
    epgPanel.dataset.settingsPanelContent = "epg";
    epgPanel.innerHTML = '<div class="ui-settings-section-heading"><div class="ui-page-eyebrow">Guide data</div><h2>EPG</h2><p>Manage public and additional XMLTV sources used by the combined guide.</p></div>';
    const publicCard = el("publicEpgCard");
    const epgTableCard = el("epgSources")?.closest(".card");
    [publicCard, epgTableCard].filter(Boolean).forEach(card => {
      epgPanel.appendChild(card);
    });

    const overview = makePage("overview", "Overview", "Application-wide update scheduling and output access.");
    const overviewGrid = document.createElement("div");
    overviewGrid.className = "ui-overview-grid";
    overviewGrid.innerHTML = `
      <section class="ui-modern-card ui-overview-update-card">
        <div class="ui-card-heading"><div><span>Automatic Update</span><small>One application-wide refresh clock.</small></div></div>
        <div id="uiOverviewMasterSlot"></div>
      </section>
      <section class="ui-modern-card">
        <div class="ui-card-heading"><div><span>Outputs</span><small>Curated playlist and combined XMLTV guide.</small></div></div>
        <div class="ui-output-summary">
          <div><span>M3U Playlist</span><code>/playlist/channels.m3u</code></div>
          <div><span>Combined EPG</span><code>/epg/epg.xml</code></div>
        </div>
        <button class="btn ui-btn-secondary" id="uiOverviewOutputsBtn" type="button">Open Outputs</button>
        <div class="ui-overview-resource-links">
          <a href="/user-guide" target="_blank" rel="noopener">${icons.external}<span>User Guide</span></a>
          <a href="https://github.com/zschmook/m3u-web-picker" target="_blank" rel="noopener noreferrer">${icons.external}<span>GitHub</span></a>
        </div>
      </section>`;
    overview.appendChild(overviewGrid);
    const firstPage = providerCard || channelShell || sportsCard;
    if (firstPage?.parentNode) firstPage.parentNode.insertBefore(overview, firstPage);
    else root.prepend(overview);

    const masterPanel = document.querySelector(".master-update-panel");
    if (masterPanel) {
      el("uiOverviewMasterSlot")?.appendChild(masterPanel);
      masterPanel.classList.add("ui-overview-master-panel");
      el("masterUpdateNowBtn")?.classList.add("ui-legacy-hidden");
      el("masterUpdateRunning")?.classList.add("ui-legacy-hidden");
    }

    const devicesPanel = document.createElement("div");
    devicesPanel.className = "ui-settings-panel ui-settings-collection";
    devicesPanel.dataset.settingsPanelContent = "devices";
    devicesPanel.innerHTML = '<div class="ui-settings-section-heading"><div class="ui-page-eyebrow">Playback targets</div><h2>Devices</h2><p>HDHomeRun support, saved Roku targets, and active remote playback sessions.</p></div>';
    const devicesGrid = document.createElement("div");
    devicesGrid.className = "ui-devices-grid";
    devicesGrid.innerHTML = `
      <section class="ui-modern-card" id="uiHdhrDeviceCard">
        <div class="ui-card-heading"><div><span>HDHomeRun</span><small>Virtual tuner discovery and HTTP playback surface.</small></div></div>
        <div id="uiHdhrDeviceSlot"></div>
      </section>
      <section class="ui-modern-card">
        <div class="ui-card-heading"><div><span>Roku</span><small>Saved targets are identified by stable Roku identity, not IP address.</small></div><span class="ui-count-badge" id="uiRokuDeviceCount">0 saved</span></div>
        <div id="uiRokuDeviceList" class="ui-roku-device-list"><div class="ui-empty-state">Loading saved devices…</div></div>
        <a class="btn ui-btn-secondary ui-inline-action" href="/guide">Open TV Guide</a>
      </section>
      <section class="ui-modern-card">
        <div class="ui-card-heading"><div><span>Remote Playback</span><small>Live HLS relays currently serving Roku or Cast receivers.</small></div></div>
        <div class="ui-big-metric"><strong id="uiDeviceStreams">0</strong><span>active streams</span></div>
      </section>`;
    devicesPanel.appendChild(devicesGrid);

    const settings = makePage("settings", "Settings", "Manage optional integrations and application behavior after initial setup.");
    settings.innerHTML += `
      <div class="ui-settings-tabs" role="tablist">
        <button class="ui-settings-tab is-active" type="button" data-settings-panel="encoding">Encoding</button>
        <button class="ui-settings-tab" type="button" data-settings-panel="dvr">DVR</button>
        <button class="ui-settings-tab" type="button" data-settings-panel="network">Network</button>
        <button class="ui-settings-tab" type="button" data-settings-panel="jellyfin">Jellyfin Cache</button>
        <button class="ui-settings-tab" type="button" data-settings-panel="epg">EPG</button>
        <button class="ui-settings-tab" type="button" data-settings-panel="devices">Devices</button>
      </div>
      <div class="ui-settings-grid">
        <section class="ui-modern-card ui-settings-panel is-active" data-settings-panel-content="encoding" aria-labelledby="uiEncodingTitle">
          <div class="ui-card-heading">
            <div><span id="uiEncodingTitle">Live Stream Encoding</span><small>Optional compatibility encoding for browser, M3U, HDHomeRun, Roku, and Cast playback.</small></div>
            <span class="ui-count-badge" id="uiEncodingBadge">Loading</span>
          </div>
          <div class="ui-settings-form">
            <label class="ui-settings-toggle" for="uiEncodingEnabled">
              <input id="uiEncodingEnabled" type="checkbox" role="switch">
              <span><strong>Enable live-stream encoding</strong><small>When off, channels play directly from the provider. When on, M3U Web Picker normalizes live video for compatible playback.</small></span>
            </label>
            <div class="ui-settings-runtime-note"><strong>Live playback only.</strong> DVR H.265 conversion is configured separately on the DVR tab.</div>
            <div class="ui-settings-warning" id="uiEncodingWarning">Run the hardware check before enabling encoding.</div>
            <div class="ui-settings-runtime" id="uiEncodingRuntime">FFmpeg has not been checked yet.</div>
            <button class="btn ui-btn-secondary ui-inline-action" id="uiEncodingTest" type="button">Check Encoding Hardware</button>
            <details class="ui-settings-details" id="uiEncodingAdvanced">
              <summary>Advanced encoding controls</summary>
              <div class="ui-settings-details-body">
                <label class="ui-settings-toggle" for="uiEncodingAcknowledge">
                  <input id="uiEncodingAcknowledge" type="checkbox" role="switch">
                  <span><strong>I understand the performance risk</strong><small>Required when encoding is enabled. CPU fallback may buffer or fail, and hardware acceleration can still be overloaded.</small></span>
                </label>
                <label class="ui-settings-field" for="uiEncodingEncoder"><span>Encoder</span><select id="uiEncodingEncoder" class="form-select"><option value="auto">Automatic (recommended)</option><option value="h264_nvenc">NVIDIA NVENC</option><option value="h264_qsv">Intel Quick Sync</option><option value="h264_vaapi">VA-API</option><option value="libx264">CPU (libx264)</option></select></label>
                <label class="ui-settings-field" for="uiEncodingMaxSessions"><span>Maximum simultaneous encoded streams</span><input id="uiEncodingMaxSessions" class="form-control" type="number" min="1" max="16" value="2"></label>
                <div class="ui-output-summary"><div><span>Encoded playlist</span><code>/playlist/channels.m3u</code></div><div><span>Direct fallback</span><code>/playlist/channels.direct.m3u</code></div></div>
              </div>
            </details>
            <div class="ui-settings-actions"><button class="btn ui-btn-primary" id="uiEncodingSave" type="button">Save Encoding Settings</button></div>
            <div class="ui-settings-status" id="uiEncodingStatus" role="status" aria-live="polite"></div>
          </div>
        </section>
        <section class="ui-modern-card ui-settings-panel" data-settings-panel-content="dvr" aria-labelledby="uiDvrTitle">
          <div class="ui-card-heading">
            <div><span id="uiDvrTitle">In-App DVR</span><small>Schedule individual programs or entire series from the TV Guide.</small></div>
            <span class="ui-count-badge" id="uiDvrBadge">Loading</span>
          </div>
          <div class="ui-settings-form">
            <label class="ui-settings-toggle" for="uiDvrEnabled">
              <input id="uiDvrEnabled" type="checkbox" role="switch" autocomplete="off">
              <span><strong>Enable in-app DVR</strong><small>Recording buttons remain unavailable until storage is mounted and validated.</small></span>
            </label>
            <label class="ui-settings-field" for="uiDvrPath">
              <span>Local recording folder</span>
              <input id="uiDvrPath" class="form-control" autocomplete="off" placeholder="C:\\Media\\DVR">
            </label>
            <div class="ui-settings-warning">Docker must mount this exact host folder through <code>M3U_DVR_DIR</code>. Changing it requires updating <code>.env</code> and restarting the container.</div>
            <label class="ui-settings-field" for="uiDvrPlexPath">
              <span>Plex folder</span>
              <input id="uiDvrPlexPath" class="form-control" autocomplete="off" placeholder="C:\\DVR\\PLEX">
              <small>Successful H.265 recordings move here using Plex-friendly show and episode names. Leave blank to keep them in the DVR converted folder.</small>
            </label>
            <label class="ui-settings-toggle" for="uiDvrHevc">
              <input id="uiDvrHevc" type="checkbox" role="switch" autocomplete="off">
              <span><strong>Convert completed recordings to H.265/MKV</strong><small>Each file is checked to confirm recording has finished before conversion. The original capture is kept if conversion fails.</small></span>
            </label>
            <label class="ui-settings-field" for="uiDvrProcessingPolicy">
              <span>Process completed recordings</span>
              <select id="uiDvrProcessingPolicy" class="form-select">
                <option value="immediate">Immediately</option>
                <option value="scheduled">During scheduled or manual app updates</option>
                <option value="manual">Manual only</option>
              </select>
              <small>Immediate processing uses one conversion at a time and queues recordings in completion order.</small>
            </label>
            <label class="ui-settings-toggle" for="uiDvrRemoveCommercials">
              <input id="uiDvrRemoveCommercials" type="checkbox" role="switch" autocomplete="off">
              <span><strong>Remove detected commercials</strong><small>Comskip finds commercial breaks before conversion. Suspicious or failed detection falls back to an uncut recording.</small></span>
            </label>
            <label class="ui-settings-field" for="uiDvrPaddingBefore"><span>Start early (minutes)</span><input id="uiDvrPaddingBefore" class="form-control" type="number" min="0" max="30" value="1"></label>
            <label class="ui-settings-field" for="uiDvrPaddingAfter"><span>End late (minutes)</span><input id="uiDvrPaddingAfter" class="form-control" type="number" min="0" max="60" value="2"></label>
            <label class="ui-settings-field" for="uiDvrMaxConcurrent"><span>Maximum simultaneous recordings</span><input id="uiDvrMaxConcurrent" class="form-control" type="number" min="1" max="8" value="2"></label>
            <div class="ui-settings-runtime" id="uiDvrRuntime">Loading recording mount status…</div>
            <div class="ui-settings-actions"><button class="btn ui-btn-secondary" id="uiDvrValidate" type="button">Validate Path</button><button class="btn ui-btn-primary" id="uiDvrSave" type="button">Save DVR Settings</button></div>
            <div class="ui-settings-status" id="uiDvrStatus" role="status" aria-live="polite"></div>
          </div>
        </section>
        <section class="ui-modern-card ui-settings-panel" data-settings-panel-content="network" aria-labelledby="uiNetworkTitle">
          <div class="ui-card-heading">
            <div><span id="uiNetworkTitle">Network URLs</span><small>Control the address advertised to guide clients and devices.</small></div>
          </div>
          <div class="ui-settings-form">
            <label class="ui-settings-field" for="uiNetworkPort"><span>Public URL port</span><input id="uiNetworkPort" class="form-control" type="number" min="1" max="65535" value="9999"></label>
            <div class="ui-settings-runtime">Advertised address: <code id="uiNetworkAddress">Loading…</code></div>
            <div class="ui-settings-warning">This must match the host port published by Docker. Saving changes generated URLs but cannot remap a running container's Docker port.</div>
            <div class="ui-settings-actions"><button class="btn ui-btn-primary" id="uiNetworkSave" type="button">Save Network Setting</button></div>
            <div class="ui-settings-status" id="uiNetworkStatus" role="status" aria-live="polite"></div>
          </div>
        </section>
        <section class="ui-modern-card ui-jellyfin-settings-card ui-settings-panel" data-settings-panel-content="jellyfin" aria-labelledby="uiJellyfinSettingsTitle">
          <div class="ui-card-heading">
            <div><span id="uiJellyfinSettingsTitle">Jellyfin Cache Cleanup</span><small>Clear Jellyfin's mounted cache only after a successful Picker update.</small></div>
            <span class="ui-count-badge" id="uiJellyfinSettingsBadge">Loading</span>
          </div>
          <div class="ui-settings-form">
            <label class="ui-settings-toggle" for="uiJellyfinUsing">
              <input id="uiJellyfinUsing" type="checkbox" role="switch" autocomplete="off">
              <span><strong>I use Jellyfin</strong><small>Keep this integration available in the normal app.</small></span>
            </label>
            <label class="ui-settings-field" for="uiJellyfinCachePath">
              <span>Local Jellyfin cache directory</span>
              <input id="uiJellyfinCachePath" class="form-control" autocomplete="off" placeholder="C:\\path\\to\\Jellyfin\\cache">
            </label>
            <div class="ui-settings-warning">
              Cache cleanup may affect cached information for downloaded movies, downloaded TV shows, and DVR recordings. The path must match the directory mounted into the container through <code>M3U_JELLYFIN_CACHE_DIR</code>.
            </div>
            <label class="ui-settings-toggle" for="uiJellyfinAcknowledge">
              <input id="uiJellyfinAcknowledge" type="checkbox" role="switch" autocomplete="off">
              <span><strong>I understand the risks</strong><small>Required before automatic cleanup can be enabled.</small></span>
            </label>
            <label class="ui-settings-toggle" for="uiJellyfinCleanupEnabled">
              <input id="uiJellyfinCleanupEnabled" type="checkbox" role="switch" autocomplete="off">
              <span><strong>Clear cache after successful updates</strong><small>Never runs after a failed update.</small></span>
            </label>
            <div class="ui-settings-runtime" id="uiJellyfinRuntime">Loading mount status…</div>
            <div class="ui-settings-actions">
              <button class="btn ui-btn-secondary" id="uiJellyfinValidate" type="button">Validate Path</button>
              <button class="btn ui-btn-primary" id="uiJellyfinSave" type="button">Save Settings</button>
            </div>
            <div class="ui-settings-status" id="uiJellyfinStatus" role="status" aria-live="polite"></div>
          </div>
        </section>
      </div>`;
    settings.querySelector(".ui-settings-grid")?.append(epgPanel, devicesPanel);
    root.appendChild(settings);

    settings.querySelectorAll("[data-settings-panel]").forEach(button => button.addEventListener("click", () => {
      showSettingsPanel(button.dataset.settingsPanel);
    }));

    const hdhrPanel = document.querySelector(".hdhr-support-panel");
    if (hdhrPanel) {
      el("uiHdhrDeviceSlot")?.appendChild(hdhrPanel);
      hdhrPanel.classList.remove("ui-top-hdhr-panel", "mb-0");
      hdhrPanel.classList.add("ui-device-native-panel");
    }

    document.querySelector(".playlist-toolbar")?.classList.add("ui-legacy-hidden");
    el("uiOverviewOutputsBtn")?.addEventListener("click", openOutputsModal);
  }

  function buildModals() {
    const outputs = document.createElement("div");
    outputs.className = "modal fade ui-modern-modal";
    outputs.id = "uiOutputsModal";
    outputs.tabIndex = -1;
    outputs.setAttribute("aria-hidden", "true");
    outputs.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <div><div class="ui-modal-eyebrow">Playlist Outputs</div><h2 class="modal-title">Copy output URLs</h2></div>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="ui-output-copy-row">
              <label for="uiM3uOutputUrl">FFmpeg Enabled M3U Playlist</label>
              <div class="ui-copy-control"><input id="uiM3uOutputUrl" readonly><button class="btn ui-btn-primary" type="button" data-ui-copy="uiM3uOutputUrl">Copy</button></div>
            </div>
            <div class="ui-output-copy-row">
              <label for="uiDirectM3uOutputUrl">Direct Fallback M3U</label>
              <div class="ui-copy-control"><input id="uiDirectM3uOutputUrl" readonly><button class="btn ui-btn-primary" type="button" data-ui-copy="uiDirectM3uOutputUrl">Copy</button></div>
            </div>
            <div class="ui-output-copy-row">
              <label for="uiEpgOutputUrl">Combined EPG</label>
              <div class="ui-copy-control"><input id="uiEpgOutputUrl" readonly><button class="btn ui-btn-primary" type="button" data-ui-copy="uiEpgOutputUrl">Copy</button></div>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(outputs);

    const details = document.createElement("div");
    details.className = "modal fade ui-modern-modal";
    details.id = "uiUpdateDetailsModal";
    details.tabIndex = -1;
    details.setAttribute("aria-hidden", "true");
    details.innerHTML = `
      <div class="modal-dialog modal-dialog-centered modal-lg">
        <div class="modal-content">
          <div class="modal-header">
            <div><div class="ui-modal-eyebrow">Master Update</div><h2 class="modal-title">Update details</h2></div>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div id="uiUpdateDetailsSummary" class="ui-update-modal-summary"></div>
            <div id="uiUpdateStageList" class="ui-update-stage-list"></div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(details);

    outputs.addEventListener("click", async event => {
      const button = event.target.closest("[data-ui-copy]");
      if (!button) return;
      const input = el(button.dataset.uiCopy);
      if (!input) return;
      try {
        await navigator.clipboard.writeText(input.value);
      } catch {
        input.select();
        document.execCommand("copy");
      }
      const old = button.textContent;
      button.textContent = "✓ Copied";
      setTimeout(() => { button.textContent = old; }, 1400);
    });
  }

  function showPage(page, {replaceHash = false} = {}) {
    const settingsPanel = SETTINGS_PANEL_IDS.includes(page) ? page : "";
    const target = settingsPanel ? "settings" : (PAGE_IDS.includes(page) ? page : "overview");
    state.activePage = target;
    document.querySelectorAll("[data-ui-page]").forEach(node => node.classList.toggle("ui-page-hidden", node.dataset.uiPage !== target));
    document.querySelectorAll("[data-ui-page-target]").forEach(button => {
      const active = button.dataset.uiPageTarget === target;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
    document.body.classList.remove("ui-sidebar-open");
    if (settingsPanel) showSettingsPanel(settingsPanel);
    const hash = `#${target}`;
    if (location.hash !== hash) {
      if (replaceHash) history.replaceState(null, "", hash);
      else history.pushState(null, "", hash);
    }
    window.scrollTo({top: 0, behavior: "auto"});
  }

  function showSettingsPanel(panel) {
    if (!SETTINGS_PANEL_IDS.includes(panel)) return;
    document.querySelectorAll("#uiPage-settings [data-settings-panel]").forEach(item => {
      item.classList.toggle("is-active", item.dataset.settingsPanel === panel);
    });
    document.querySelectorAll("#uiPage-settings [data-settings-panel-content]").forEach(item => {
      item.classList.toggle("is-active", item.dataset.settingsPanelContent === panel);
    });
  }

  function bindNavigation() {
    document.querySelectorAll("[data-ui-page-target]").forEach(button => {
      button.addEventListener("click", () => showPage(button.dataset.uiPageTarget));
    });
    window.addEventListener("hashchange", () => showPage(location.hash.slice(1), {replaceHash: true}));
    const requested = location.hash.slice(1);
    const initial = PAGE_IDS.includes(requested) || SETTINGS_PANEL_IDS.includes(requested) ? requested : "overview";
    showPage(initial, {replaceHash: true});
  }

  function setHealthClass(node, status) {
    if (!node) return;
    node.classList.remove("is-success", "is-warning", "is-failed", "is-running", "is-setup", "is-loading");
    const mapped = status === "success" ? "is-success"
      : status === "warning" ? "is-warning"
      : status === "failed" || status === "error" ? "is-failed"
      : status === "running" ? "is-running"
      : status === "setup" ? "is-setup" : "is-loading";
    node.classList.add(mapped);
  }

  function renderRokuDevices(devices) {
    const list = el("uiRokuDeviceList");
    const rows = Array.isArray(devices) ? devices : [];
    if (el("uiRokuDeviceCount")) el("uiRokuDeviceCount").textContent = `${rows.length} saved`;
    if (!list) return;
    if (!rows.length) {
      list.innerHTML = '<div class="ui-empty-state">No Roku devices saved yet. Discover and add them from TV Guide.</div>';
      return;
    }
    list.innerHTML = rows.map(device => `
      <div class="ui-roku-device-row">
        <span class="ui-device-dot"></span>
        <div><strong>${escapeHtml(device.name || "Roku TV")}</strong><small>${escapeHtml([device.model || device.model_number, device.host].filter(Boolean).join(" · "))}</small></div>
      </div>`).join("");
  }

  function renderStatus(data) {
    state.status = data;
    const provider = data.provider || {};
    const counts = data.counts || {};
    const devices = data.devices || {};
    const master = data.master_update || {};
    const update = data.update || {};
    const elapsed = formatElapsed(master.elapsed_seconds);

    el("uiProviderStatus").textContent = provider.label || "—";
    el("uiAllChannels").textContent = formatNumber(counts.all_channels);
    el("uiIndexedChannels").textContent = formatNumber(counts.indexed_channels);
    el("uiSportsChannels").textContent = formatNumber(counts.sports_channels);
    const activeRecordings = Math.max(0, Number(counts.active_recordings || 0));
    const dvrBadge = el("uiDvrRecordingCount");
    if (dvrBadge) {
      dvrBadge.textContent = String(activeRecordings);
      dvrBadge.classList.toggle("d-none", activeRecordings === 0);
      dvrBadge.setAttribute("aria-label", `${activeRecordings} recording${activeRecordings === 1 ? "" : "s"} in progress`);
    }
    el("uiHdhrStatus").textContent = devices.hdhr?.enabled ? `On · ${formatNumber(devices.hdhr.tuners)} tuners` : "Off";
    el("uiRokuStatus").textContent = `${formatNumber(devices.roku_saved)} saved`;
    el("uiStreamsStatus").textContent = `${formatNumber(devices.active_streams)} active`;
    el("uiDeviceStreams").textContent = formatNumber(devices.active_streams);
    el("uiLastUpdate").textContent = formatTime(master.last_update, "Never");
    el("uiNextUpdate").textContent = master.enabled ? formatTime(master.next_update, "—") : "Disabled";

    const overall = master.running ? "running" : update.status;
    const overallText = master.running ? `Updating · ${elapsed}` : (provider.status === "setup" ? "Setup needed" : update.status === "failed" ? "Attention needed" : update.status === "warning" ? "Needs review" : "Ready");
    el("uiSystemHealth").textContent = overallText;
    setHealthClass(el("uiSystemHealthDot"), overall);

    const result = el("uiUpdateResult");
    setHealthClass(result, update.status);
    el("uiUpdateResultText").textContent = master.running ? `Update in progress · ${elapsed}` : (update.label || "—");
    const detailsButton = el("uiUpdateDetailsBtn");
    const issueCount = Number(update.error_count || 0) + Number(update.warning_count || 0);
    detailsButton.classList.toggle("d-none", master.running || (issueCount === 0 && update.status !== "failed" && update.status !== "warning"));

    const updateButton = el("uiUpdateNowBtn");
    if (master.running) {
      updateButton.disabled = true;
      updateButton.textContent = `Updating · ${elapsed}`;
    } else {
      updateButton.disabled = false;
      updateButton.textContent = "Update Now";
    }

    renderRokuDevices(devices.roku_devices);
  }

  function renderUpdateDetails() {
    const data = state.status || {};
    const update = data.update || {};
    const master = data.master_update || {};
    const summary = el("uiUpdateDetailsSummary");
    if (summary) {
      summary.innerHTML = `
        <div class="ui-update-modal-state ${escapeHtml(update.status || "setup")}"><span></span><strong>${escapeHtml(update.label || "No update status")}</strong></div>
        <div class="ui-update-meta">
          <span><small>Last update</small><strong>${escapeHtml(formatTime(master.last_update, "Never"))}</strong></span>
          <span><small>Duration</small><strong>${master.last_duration_seconds == null ? "—" : escapeHtml(formatElapsed(master.last_duration_seconds))}</strong></span>
          <span><small>Trigger</small><strong>${escapeHtml(master.last_trigger || "—")}</strong></span>
        </div>`;
    }
    const list = el("uiUpdateStageList");
    const stages = Array.isArray(update.stages) ? update.stages : [];
    if (!list) return;
    list.innerHTML = stages.map(stage => {
      const icon = stage.status === "success" ? "✓" : stage.status === "error" ? "×" : stage.status === "warning" ? "!" : "·";
      return `<div class="ui-update-stage is-${escapeHtml(stage.status)}"><span class="ui-stage-icon">${icon}</span><div><strong>${escapeHtml(stage.name)}</strong><small>${escapeHtml(stage.detail || "")}</small></div></div>`;
    }).join("") || '<div class="ui-empty-state">No update detail is available yet.</div>';
  }

  function openOutputsModal() {
    const outputs = state.status?.outputs || {m3u: "/playlist/channels.m3u", epg: "/epg/epg.xml"};
    el("uiM3uOutputUrl").value = `${location.origin}${outputs.m3u || "/playlist/channels.m3u"}`;
    el("uiDirectM3uOutputUrl").value = `${location.origin}${outputs.m3u_direct || "/playlist/channels.direct.m3u"}`;
    el("uiEpgOutputUrl").value = `${location.origin}${outputs.epg || "/epg/epg.xml"}`;
    bootstrap.Modal.getOrCreateInstance(el("uiOutputsModal")).show();
  }

  function openUpdateDetails() {
    renderUpdateDetails();
    bootstrap.Modal.getOrCreateInstance(el("uiUpdateDetailsModal")).show();
  }

  async function refreshStatus() {
    if (state.refreshInFlight) {
      state.refreshQueued = true;
      return;
    }
    state.refreshInFlight = true;
    try {
      const response = await fetch(`/api/ui/status?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "Status request failed.");
      renderStatus(data);
    } catch (error) {
      el("uiSystemHealth").textContent = "Status unavailable";
      setHealthClass(el("uiSystemHealthDot"), "failed");
      const result = el("uiUpdateResult");
      setHealthClass(result, "failed");
      el("uiUpdateResultText").textContent = error?.message || "Could not load status";
      el("uiUpdateDetailsBtn")?.classList.add("d-none");
    } finally {
      state.refreshInFlight = false;
      if (state.refreshQueued) {
        state.refreshQueued = false;
        void refreshStatus();
      }
    }
  }

  function bindActions() {
    el("uiOutputsBtn")?.addEventListener("click", openOutputsModal);
    el("uiUpdateDetailsBtn")?.addEventListener("click", openUpdateDetails);
    el("uiUpdateNowBtn")?.addEventListener("click", () => {
      const legacy = el("masterUpdateNowBtn");
      if (!legacy || legacy.disabled) return;
      const button = el("uiUpdateNowBtn");
      button.disabled = true;
      button.textContent = "Starting update…";
      legacy.click();
      setTimeout(refreshStatus, 300);
    });

    const statusNode = el("masterUpdateStatus");
    if (statusNode) {
      new MutationObserver(() => refreshStatus()).observe(statusNode, {childList: true, characterData: true, subtree: true});
    }
    const sportsStatus = el("sportsScanStatus");
    if (sportsStatus) {
      new MutationObserver(() => refreshStatus()).observe(sportsStatus, {childList: true, characterData: true, subtree: true, attributes: true});
    }
  }

  function install() {
    if (!buildShell()) return;
    buildModals();
    preparePages();
    bindNavigation();
    bindActions();
    refreshStatus();
    state.pollTimer = setInterval(refreshStatus, 10000);
    state.elapsedTimer = setInterval(() => {
      if (state.status?.master_update?.running) refreshStatus();
    }, 1000);
    document.getElementById("uiModernRoot")?.classList.remove("ui-modern-pending");
  }

  install();
})();
