(() => {
  "use strict";

  const KEYS = {
    outputs: "m3u-picker.ui.outputs-open",
    advanced: "m3u-picker.ui.sports-advanced-open",
    generated: "m3u-picker.ui.sports-generated-open",
    fallback: "m3u-picker.ui.provider-fallback-open"
  };

  const ui = {
    providerReady: false,
    sportsReady: false,
    previewLeague: "all",
    previewTime: "all",
    toastTimer: null
  };

  const el = id => document.getElementById(id);

  function shortTime(value) {
    if (!value) return "Never";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {weekday: "short", hour: "numeric", minute: "2-digit"});
  }

  function bindDetails(details, key, defaultOpen = false) {
    if (!details || details.dataset.uiBound) return;
    const saved = localStorage.getItem(key);
    details.open = saved === null ? defaultOpen : saved === "true";
    details.dataset.uiBound = "true";
    details.addEventListener("toggle", () => localStorage.setItem(key, String(details.open)));
  }

  function makeDetails(id, title, className = "") {
    const details = document.createElement("details");
    details.id = id;
    details.className = className;
    details.innerHTML = `<summary class="ui-details-summary"><span>${title}</span><span class="ui-details-chevron" aria-hidden="true">⌄</span></summary>`;
    return details;
  }

  function installTop() {
    const topbar = document.querySelector(".app-topbar");
    const brand = document.querySelector(".app-brand-block");
    const toolbar = document.querySelector(".playlist-toolbar");
    if (!topbar || !brand || !toolbar || el("uiStatusStrip")) return;

    document.body.classList.add("ui-refactor");
    const badge = brand.querySelector("h1 .badge");
    if (badge) {
      badge.textContent = "experients-ui";
      badge.classList.remove("text-bg-warning");
      badge.classList.add("text-bg-info");
    }

    const strip = document.createElement("div");
    strip.id = "uiStatusStrip";
    strip.className = "ui-status-strip";
    strip.innerHTML = `
      <span class="ui-health-pill" id="uiHealthPill"><span class="ui-health-dot"></span><span id="uiHealthText">Loading</span></span>
      <span class="ui-status-item"><span class="ui-status-label">Last</span><span id="uiMasterLast">—</span></span>
      <span class="ui-status-item"><span class="ui-status-label">Next</span><span id="uiMasterNext">—</span></span>
      <span class="ui-status-item ui-hdhr-mirror"><span class="ui-status-label">HDHR</span><span id="uiHdhrMirror">Loading…</span></span>`;
    brand.appendChild(strip);

    const running = el("masterUpdateRunning");
    const update = el("masterUpdateNowBtn");
    if (running) strip.appendChild(running);
    if (update) {
      update.classList.add("ui-top-update-btn");
      strip.appendChild(update);
    }

    const outputs = makeDetails("uiOutputsDetails", "Outputs & automatic update", "ui-output-details");
    topbar.insertAdjacentElement("afterend", outputs);
    outputs.appendChild(toolbar);
    bindDetails(outputs, KEYS.outputs, false);

    const nav = document.createElement("nav");
    nav.className = "ui-jump-nav";
    nav.setAttribute("aria-label", "Page sections");
    nav.innerHTML = `<a href="#providersSection">Providers</a><a href="#channelsSection">Channels</a><a href="#epgSection">EPG</a><a href="#sportsSection">Sports</a>`;
    outputs.insertAdjacentElement("afterend", nav);
  }

  function installAnchors() {
    const provider = el("providerSources")?.closest(".card");
    if (provider && !provider.id) provider.id = "providersSection";

    const channelHeader = el("channelManagerHeader");
    if (channelHeader && !el("channelsSection")) {
      const anchor = document.createElement("span");
      anchor.id = "channelsSection";
      anchor.className = "ui-section-anchor";
      channelHeader.before(anchor);
    }

    const epg = el("publicEpgCard");
    if (epg && !el("epgSection")) {
      const anchor = document.createElement("span");
      anchor.id = "epgSection";
      anchor.className = "ui-section-anchor";
      epg.before(anchor);
    }

    const sports = el("sportsSectionTitle")?.closest(".sports-card");
    if (sports && !sports.id) sports.id = "sportsSection";
  }

  function installProvider() {
    if (ui.providerReady) return;
    const list = el("providerSources");
    const fieldset = el("primaryProviderFieldset");
    const fallback = document.querySelector(".provider-fallback-panel");
    const body = list?.closest(".card-body");
    const table = fallback?.querySelector(".table-responsive");
    if (!list || !fieldset || !fallback || !body || !table) return;

    const heading = body.querySelector("h2");
    const intro = heading?.nextElementSibling;
    const compact = document.createElement("div");
    compact.id = "uiProviderCompactSummary";
    compact.className = "ui-provider-compact-summary";
    (intro || heading)?.insertAdjacentElement("afterend", compact);

    table.classList.add("ui-provider-table");
    compact.insertAdjacentElement("afterend", table);

    const primaryDetails = makeDetails("uiPrimaryProviderDetails", "Add primary provider", "ui-provider-add-details");
    table.insertAdjacentElement("afterend", primaryDetails);
    primaryDetails.appendChild(fieldset);
    const fileActions = el("filePrimaryActions");
    if (fileActions) primaryDetails.appendChild(fileActions);

    const fallbackDetails = makeDetails("uiFallbackProviderDetails", "+ Add fallback provider", "ui-provider-add-details");
    primaryDetails.insertAdjacentElement("afterend", fallbackDetails);
    fallbackDetails.appendChild(fallback);
    bindDetails(fallbackDetails, KEYS.fallback, false);

    const progress = el("providerOperationStatus");
    if (progress) table.insertAdjacentElement("afterend", progress);
    ui.providerReady = true;
  }

  function providerKind(source) {
    if (source?.kind === "xtream") return source.xtream_api ? "Xtream API" : "Xtream-compatible";
    return source ? "Direct M3U" : "—";
  }

  function syncProvider() {
    installProvider();
    const compact = el("uiProviderCompactSummary");
    const primaryDetails = el("uiPrimaryProviderDetails");
    const fallbackDetails = el("uiFallbackProviderDetails");
    const sources = Array.isArray(providerSources) ? providerSources : [];
    const primary = sources.find(source => source.role === "primary");
    const fallbacks = sources.filter(source => source.role === "fallback");

    if (compact) {
      if (!primary) {
        compact.className = "ui-provider-compact-summary is-empty";
        compact.innerHTML = `<strong>No primary provider loaded</strong><span>Load one below to populate Channel Manager.</span>`;
      } else {
        const bits = [
          providerKind(primary),
          `${Number(primary.channel_count || 0).toLocaleString()} channels`,
          primary.account_status || "Ready",
          primary.expires_at ? `Expires ${formatProviderExpiry(primary.expires_at)}` : ""
        ].filter(Boolean);
        compact.className = "ui-provider-compact-summary";
        compact.innerHTML = `<span class="badge text-bg-primary">Primary</span><strong>${escapeHtml(primary.name || primary.source_label || "Primary")}</strong><span>${bits.map(escapeHtml).join(" · ")}</span>${fallbacks.length ? `<span class="ui-provider-fallback-count">${fallbacks.length} fallback${fallbacks.length === 1 ? "" : "s"}</span>` : ""}`;
      }
    }

    if (primaryDetails) {
      const text = primaryDetails.querySelector("summary span:first-child");
      if (text) text.textContent = primary ? "Primary provider settings" : "Add primary provider";
      const had = primaryDetails.dataset.uiHadPrimary === "true";
      if (primary && !had) primaryDetails.open = false;
      if (!primary && had) primaryDetails.open = true;
      if (primaryDetails.dataset.uiHadPrimary === undefined) primaryDetails.open = !primary;
      primaryDetails.dataset.uiHadPrimary = String(Boolean(primary));
    }

    if (fallbackDetails) {
      const text = fallbackDetails.querySelector("summary span:first-child");
      if (text) text.textContent = `+ Add fallback provider${fallbacks.length ? ` (${fallbacks.length} configured)` : ""}`;
    }
  }

  function installChannels() {
    const header = el("channelManagerHeader");
    const collapse = el("channelManagerCollapseBtn");
    const manage = el("manageOrderBtn");
    if (!header || !collapse || !manage || el("uiSelectedBadge")) return;

    const titleRow = header.querySelector(".d-flex.align-items-center.gap-2");
    titleRow?.querySelector(".badge")?.remove();
    const badge = document.createElement("span");
    badge.id = "uiSelectedBadge";
    badge.className = "badge text-bg-secondary";
    badge.textContent = "0 saved";
    titleRow?.appendChild(badge);

    const actions = document.createElement("div");
    actions.className = "ui-channel-header-actions";
    manage.classList.remove("btn-outline-light");
    manage.classList.add("btn-outline-secondary");
    actions.append(manage, collapse);
    header.appendChild(actions);

    if (el("search")) el("search").placeholder = "Search channels…";

    const removeAll = el("clearVisibleBtn");
    if (removeAll && !removeAll.dataset.uiConfirmBound) {
      removeAll.dataset.uiConfirmBound = "true";
      let bypass = false;
      removeAll.addEventListener("click", event => {
        if (bypass || removeAll.disabled) {
          bypass = false;
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        const count = removableVisibleChannels().length;
        if (!count || !window.confirm(`Remove all ${count} visible manual channels from the saved playlist?`)) return;
        bypass = true;
        removeAll.click();
      }, true);
    }
  }

  function syncChannels() {
    installChannels();
    const badge = el("uiSelectedBadge");
    if (!badge) return;
    const count = channels.filter(channel => !isGeneratedSportsChannel(channel) && selected.has(Number(channel.id))).length;
    badge.textContent = `${count} saved`;
  }

  function installSports() {
    if (ui.sportsReady) return;
    const body = el("sportsBody");
    const options = el("sportsIncludeReplays")?.closest(".sports-subsection");
    const rules = el("sportsRules")?.closest(".sports-subsection");
    const block = el("sportsStartChannel")?.closest(".sports-subsection");
    const api = el("sportsScheduleApiEnabled")?.closest(".sports-subsection");
    const preview = el("sportsPreview")?.closest(".sports-subsection");
    if (!body || !options || !rules || !block || !api || !preview) return;

    options.classList.add("ui-sports-global-options");
    const heading = document.createElement("div");
    heading.className = "ui-subsection-heading";
    heading.innerHTML = `<h3 class="h6 mb-1">Game behavior</h3><div class="small-muted">These settings apply to every sports selection below.</div>`;
    options.prepend(heading);
    body.insertBefore(options, body.firstElementChild);
    options.after(rules);

    const advanced = makeDetails("uiSportsAdvancedDetails", "Advanced Sports Settings", "ui-sports-details");
    rules.after(advanced);
    advanced.append(block, api);
    bindDetails(advanced, KEYS.advanced, false);

    const generated = makeDetails("uiSportsGeneratedDetails", "Generated Sports Channels", "ui-sports-details ui-generated-details");
    advanced.after(generated);
    const count = document.createElement("span");
    count.id = "uiGeneratedSummaryCount";
    count.className = "badge text-bg-secondary ms-auto";
    count.textContent = "0 channels";
    const summary = generated.querySelector("summary");
    summary.insertBefore(count, summary.lastElementChild);
    generated.appendChild(preview);
    bindDetails(generated, KEYS.generated, false);

    const filters = document.createElement("div");
    filters.id = "uiSportsPreviewFilters";
    filters.className = "ui-sports-preview-filters";
    preview.querySelector(".d-flex")?.after(filters);
    filters.addEventListener("click", event => {
      const button = event.target.closest("button[data-ui-filter]");
      if (!button) return;
      if (button.dataset.uiFilter === "league") ui.previewLeague = button.dataset.value || "all";
      if (button.dataset.uiFilter === "time") ui.previewTime = button.dataset.value || "all";
      renderPreviewFilters();
      applyPreviewFilter();
    });

    body.querySelector(".sports-footer")?.classList.add("ui-sports-footer");
    ui.sportsReady = true;
  }

  function leagueFor(row) {
    const text = `${row?.display_name || ""} ${row?.subtitle || ""}`;
    if (/\bNCAA\b|college football/i.test(text)) return "NCAA";
    if (/\bNFL\b/i.test(text)) return "NFL";
    if (/\bMLB\b/i.test(text)) return "MLB";
    const prefix = String(row?.display_name || "").split("·", 1)[0].trim();
    if (prefix && prefix.length <= 20 && !/\b(at|vs\.?|@)\b/i.test(prefix)) return prefix;
    return "Other";
  }

  function timeFor(row) {
    const start = Date.parse(row?.event_start || "");
    if (!Number.isFinite(start)) return "unknown";
    const now = Date.now();
    if (start > now) return "upcoming";
    if (now - start <= 6 * 60 * 60 * 1000) return "live";
    return "past";
  }

  function renderPreviewFilters() {
    const target = el("uiSportsPreviewFilters");
    if (!target) return;
    const rows = Array.isArray(sportsState.generated) ? sportsState.generated : [];
    const leagues = [...new Set(rows.map(leagueFor).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    if (ui.previewLeague !== "all" && !leagues.includes(ui.previewLeague)) ui.previewLeague = "all";
    const leagueButtons = ["all", ...leagues].map(value => `<button type="button" class="btn btn-sm ${ui.previewLeague === value ? "btn-secondary" : "btn-outline-secondary"}" data-ui-filter="league" data-value="${escapeHtml(value)}">${escapeHtml(value === "all" ? "All" : value)}</button>`).join("");
    const timeButtons = [["all", "All"], ["upcoming", "Upcoming"], ["live", "Live"]].map(([value, label]) => `<button type="button" class="btn btn-sm ${ui.previewTime === value ? "btn-secondary" : "btn-outline-secondary"}" data-ui-filter="time" data-value="${value}">${label}</button>`).join("");
    target.innerHTML = `<div class="btn-group btn-group-sm" role="group">${leagueButtons}</div><div class="btn-group btn-group-sm" role="group">${timeButtons}</div>`;
  }

  function annotatePreview() {
    const dom = [...document.querySelectorAll("#sportsPreview .sports-preview-row")];
    const rows = Array.isArray(sportsState.generated) ? sportsState.generated : [];
    dom.forEach((node, index) => {
      node.dataset.uiLeague = leagueFor(rows[index] || {});
      node.dataset.uiTime = timeFor(rows[index] || {});
    });
  }

  function applyPreviewFilter() {
    const rows = [...document.querySelectorAll("#sportsPreview .sports-preview-row")];
    let visible = 0;
    rows.forEach(row => {
      const show = (ui.previewLeague === "all" || row.dataset.uiLeague === ui.previewLeague)
        && (ui.previewTime === "all" || row.dataset.uiTime === ui.previewTime);
      row.classList.toggle("d-none", !show);
      if (show) visible += 1;
    });
    const total = Array.isArray(sportsState.generated) ? sportsState.generated.length : 0;
    const count = el("uiGeneratedSummaryCount");
    if (count) count.textContent = visible === total ? `${total} channel${total === 1 ? "" : "s"}` : `${visible} of ${total}`;
  }

  function sportsSummary() {
    installSports();
    const target = el("sportsHeaderSummary");
    if (!target) return;
    const generated = Array.isArray(sportsState.generated) ? sportsState.generated.length : 0;
    const enabled = Boolean(sportsState.settings?.enabled);
    const last = sportsState.last_scan || {};
    const events = Number(last.event_count || 0);
    const duration = last.started_at && last.finished_at ? formatScanDuration(last.started_at, last.finished_at) : "";
    const next = masterUpdateState?.enabled ? shortTime(masterUpdateState.next_update) : "Auto off";
    target.textContent = enabled
      ? [`${events} event${events === 1 ? "" : "s"}`, `${generated} channel${generated === 1 ? "" : "s"}`, duration ? `Last scan ${duration}` : "", `Next ${next}`].filter(Boolean).join(" · ")
      : ["Disabled", generated ? `${generated} cached channel${generated === 1 ? "" : "s"}` : "No generated channels"].join(" · ");
    const count = el("uiGeneratedSummaryCount");
    if (count) count.textContent = `${generated} channel${generated === 1 ? "" : "s"}`;
  }

  function installToast() {
    let stack = el("uiToastStack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "uiToastStack";
      stack.className = "ui-toast-stack";
      document.body.appendChild(stack);
    }
    const status = el("sportsScanStatus");
    if (status && status.parentElement !== stack) stack.appendChild(status);
  }

  function autoDismissToast() {
    clearTimeout(ui.toastTimer);
    const scan = sportsState.last_scan || {};
    const panel = el("sportsScanStatus");
    if (!panel || sportsState.scan?.running || masterUpdateState?.running) return;
    if (String(scan.status || "").toLowerCase() !== "success" || panel.classList.contains("d-none")) return;
    ui.toastTimer = setTimeout(() => {
      const signature = typeof scanResultSignature === "function" ? scanResultSignature(scan) : String(scan.id || "");
      if (signature) localStorage.setItem("m3u-picker.sports-scan-dismissed", signature);
      panel.classList.add("d-none");
    }, 10000);
  }

  function polishApi() {
    const api = sportsState.schedule_api || {};
    const entries = Array.isArray(api.apis) ? api.apis : [];
    const section = document.querySelector(".sports-schedule-api");
    if (!section) return;
    const headers = [...section.querySelectorAll(".schedule-api-table thead th")];
    headers.forEach(header => {
      if (header.textContent.trim() === "Status") header.textContent = "Cache state";
      if (header.textContent.trim() === "Last Updated") header.textContent = "Last success";
      if (header.textContent.trim() === "Cache") header.textContent = "Games";
    });

    const rows = [...section.querySelectorAll("#sportsScheduleApiList tr[data-api-dataset], #sportsScheduleApiList tr")];
    rows.forEach((row, index) => {
      const entry = entries.find(item => String(item.id || "") === String(row.dataset.apiDataset || "")) || entries[index];
      if (!entry) return;
      const count = Number(entry.cached_event_count || 0);
      const current = Boolean(entry.cache_current);
      const cells = row.children;
      if (cells.length < 6) return;
      if (current && count === 0) {
        cells[3].innerHTML = `<span class="badge text-bg-secondary">Current · 0 games</span>`;
        cells[5].textContent = "0 games";
      } else if (current) {
        const badge = cells[3].querySelector(".badge");
        if (badge && /cached/i.test(badge.textContent)) badge.textContent = "Current";
      }
    });

    const empty = entries.filter(entry => entry.cache_current && Number(entry.cached_event_count || 0) === 0);
    const health = el("sportsScheduleApiHealth");
    if (health && empty.length && !entries.some(entry => ["error", "stale", "partial"].includes(String(entry.status_code || "")))) {
      health.className = "schedule-api-health small mt-1 small-muted";
      health.textContent = `${empty.map(entry => entry.scope || entry.id).join(", ")} returned 0 games in the current window. That is a successful fetch, not proof of a matching failure.`;
    }
  }

  function globalStatus() {
    const sources = Array.isArray(providerSources) ? providerSources : [];
    const primary = sources.find(source => source.role === "primary");
    const pill = el("uiHealthPill");
    if (pill) {
      const running = Boolean(masterUpdateBusy || masterUpdateState?.running || sportsState?.scan?.running);
      pill.classList.toggle("is-running", running);
      pill.classList.toggle("is-ready", Boolean(primary) && !running);
      pill.classList.toggle("is-setup", !primary && !running);
      if (el("uiHealthText")) el("uiHealthText").textContent = running ? "Updating" : primary ? "Ready" : "Setup needed";
    }
    if (el("uiMasterLast")) el("uiMasterLast").textContent = shortTime(masterUpdateState?.last_update);
    if (el("uiMasterNext")) el("uiMasterNext").textContent = masterUpdateState?.enabled ? shortTime(masterUpdateState?.next_update) : "Disabled";
    sportsSummary();
  }

  function mirrorHdhr() {
    const source = el("hdhrSupportStatus");
    const target = el("uiHdhrMirror");
    if (!source || !target) return;
    const text = source.textContent.trim();
    if (/^Enabled/i.test(text)) {
      const tuners = text.match(/(\d+) tuners?/i)?.[1];
      target.textContent = tuners ? `On · ${tuners} tuners` : "On";
      target.className = "text-success";
    } else if (/^Disabled/i.test(text)) {
      target.textContent = "Off";
      target.className = "small-muted";
    } else {
      target.textContent = text || "Loading…";
      target.className = source.className.includes("danger") ? "text-danger" : "small-muted";
    }
  }

  function watchHdhr() {
    const attach = () => {
      const source = el("hdhrSupportStatus");
      if (!source || source.dataset.uiMirrorBound) return Boolean(source);
      source.dataset.uiMirrorBound = "true";
      mirrorHdhr();
      new MutationObserver(mirrorHdhr).observe(source, {childList: true, characterData: true, subtree: true, attributes: true});
      return true;
    };
    if (attach()) return;
    const observer = new MutationObserver(() => { if (attach()) observer.disconnect(); });
    observer.observe(document.body, {childList: true, subtree: true});
  }

  function install() {
    installTop();
    installAnchors();
    installProvider();
    installChannels();
    installSports();
    installToast();
    watchHdhr();
    syncProvider();
    syncChannels();
    sportsSummary();
    renderPreviewFilters();
    annotatePreview();
    applyPreviewFilter();
    polishApi();
    globalStatus();
  }

  install();

  if (typeof renderProviderSources === "function") {
    const base = renderProviderSources;
    renderProviderSources = function() { base(); syncProvider(); globalStatus(); };
  }

  if (typeof render === "function") {
    const base = render;
    render = function() { base(); syncChannels(); };
  }

  if (typeof renderMasterUpdate === "function") {
    const base = renderMasterUpdate;
    renderMasterUpdate = function() { base(); globalStatus(); };
  }

  if (typeof renderSportsScheduleApi === "function") {
    const base = renderSportsScheduleApi;
    renderSportsScheduleApi = function() { base(); polishApi(); };
  }

  if (typeof renderSportsPreview === "function") {
    const base = renderSportsPreview;
    renderSportsPreview = function() {
      base();
      renderPreviewFilters();
      annotatePreview();
      applyPreviewFilter();
    };
  }

  if (typeof renderSportsScanStatus === "function") {
    const base = renderSportsScanStatus;
    renderSportsScanStatus = function() {
      base();
      installToast();
      autoDismissToast();
    };
  }

  if (typeof applySportsState === "function") {
    const base = applySportsState;
    applySportsState = function() {
      base();
      installSports();
      sportsSummary();
      polishApi();
      renderPreviewFilters();
      annotatePreview();
      applyPreviewFilter();
      globalStatus();
    };
  }
})();
