(() => {
  "use strict";

  const STEP_COUNT = 8;
  const ctx = {
    payload: null,
    step: 1,
    catalog: [],
    selectedRules: new Set(),
    busy: false,
  };

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status}).`);
    return data;
  }

  function overlay() {
    return document.getElementById("devOnboardingOverlay");
  }

  function body() {
    return document.getElementById("devOnboardingBody");
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
  }

  function setBusy(busy, message = "") {
    ctx.busy = Boolean(busy);
    overlay()?.querySelectorAll("button, input, select").forEach(element => {
      if (element.dataset.allowBusy === "true") return;
      element.disabled = ctx.busy;
    });
    if (message) setStatus(message);
  }

  async function saveProgress(step, answers = {}) {
    const data = await api("/api/onboarding", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({current_step: step, answers}),
    });
    if (ctx.payload?.state) ctx.payload.state = data.state;
    ctx.step = Number(data.state?.current_step || step || 1);
  }

  function shell() {
    let target = overlay();
    if (target) return target;
    target = document.createElement("div");
    target.id = "devOnboardingOverlay";
    target.innerHTML = `
      <div class="dev-onboarding-shell" role="dialog" aria-modal="true" aria-labelledby="devOnboardingTitle">
        <div class="dev-onboarding-header">
          <div>
            <div class="dev-onboarding-kicker">First-run setup</div>
            <div class="dev-onboarding-title" id="devOnboardingTitle">M3U Web Picker</div>
          </div>
          <div class="dev-onboarding-step-count" id="devOnboardingStepCount"></div>
        </div>
        <div class="dev-onboarding-body" id="devOnboardingBody"></div>
      </div>`;
    document.body.appendChild(target);
    document.documentElement.classList.remove("onboarding-pending");
    document.documentElement.classList.add("onboarding-active");
    return target;
  }

  function actions(back = true, nextLabel = "Continue") {
    return `
      <div class="dev-onboarding-actions">
        ${back ? '<button class="dev-onboarding-btn" id="devOnboardingBack" type="button">Back</button>' : "<span></span>"}
        <div class="dev-onboarding-actions-right">
          <button class="dev-onboarding-btn primary" id="devOnboardingNext" type="button">${esc(nextLabel)}</button>
        </div>
      </div>
      <div class="dev-onboarding-status" id="devOnboardingStatus" role="status" aria-live="polite"></div>`;
  }

  function bindBack(defaultStep) {
    document.getElementById("devOnboardingBack")?.addEventListener("click", async () => {
      if (ctx.busy) return;
      await saveProgress(defaultStep);
      render();
    });
  }

  function updateStepCount() {
    const target = document.getElementById("devOnboardingStepCount");
    if (target) target.textContent = `Step ${ctx.step} of ${STEP_COUNT}`;
  }

  async function renderProvider() {
    const configured = Boolean(ctx.payload?.provider_configured);
    body().innerHTML = `
      <h2>Primary Provider</h2>
      <div class="dev-onboarding-help">Start with the provider that supplies the catalog shown in Channel Manager. Xtream users can paste the server/base URL and credentials separately.</div>
      ${configured ? `
        <div class="dev-onboarding-summary"><strong>Primary provider configured.</strong><br>Continue to Sports Automation setup.</div>
        ${actions(false, "Continue")}` : `
        <div class="dev-onboarding-grid">
          <div class="dev-onboarding-field">
            <label for="devProviderName">Provider name</label>
            <input id="devProviderName" value="Primary" autocomplete="off">
          </div>
          <div class="dev-onboarding-field wide">
            <label for="devProviderUrl">Provider or M3U URL</label>
            <input id="devProviderUrl" placeholder="https://provider.example:8080" autocomplete="url">
          </div>
          <div class="dev-onboarding-field">
            <label for="devProviderUsername">Xtream username</label>
            <input id="devProviderUsername" placeholder="Optional" autocomplete="username">
          </div>
          <div class="dev-onboarding-field">
            <label for="devProviderPassword">Xtream password</label>
            <input id="devProviderPassword" type="password" placeholder="Optional" autocomplete="current-password">
          </div>
        </div>
        ${actions(false, "Load Primary")}`}
    `;

    document.getElementById("devOnboardingNext")?.addEventListener("click", async () => {
      if (ctx.busy) return;
      if (configured) {
        await saveProgress(2, {provider_configured: true});
        render();
        return;
      }
      const url = document.getElementById("devProviderUrl")?.value.trim() || "";
      if (!url) {
        setStatus("Enter a provider or M3U URL.", "error");
        return;
      }
      const payload = {
        name: document.getElementById("devProviderName")?.value.trim() || "Primary",
        url,
        username: document.getElementById("devProviderUsername")?.value || "",
        password: document.getElementById("devProviderPassword")?.value || "",
      };
      setBusy(true, "Loading primary provider… this can take a while on large catalogs.");
      let progressTimer = null;
      try {
        progressTimer = setInterval(async () => {
          try {
            const progress = await api("/api/providers/progress");
            const text = [progress.stage, progress.detail].filter(Boolean).join(" • ");
            if (text) setStatus(text);
          } catch {}
        }, 1200);
        await api("/api/load-url", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        ctx.payload.provider_configured = true;
        await saveProgress(2, {provider_configured: true});
        render();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      } finally {
        if (progressTimer) clearInterval(progressTimer);
      }
    });
  }

  async function chooseSports(enabled) {
    const excludeSd = Boolean(document.getElementById("devExcludeSdChannels")?.checked);
    setBusy(true, enabled ? "Enabling Sports Automation…" : "Leaving Sports Automation disabled…");
    try {
      await api("/api/sports/settings", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled, exclude_sd: excludeSd}),
      });
      ctx.payload.sports.settings.enabled = enabled;
      ctx.payload.sports.settings.exclude_sd = excludeSd;
      const next = enabled ? 3 : 6;
      await saveProgress(next, {
        sports_enabled: enabled,
        schedule_api_enabled: false,
        exclude_sd_channels: excludeSd,
      });
      render();
    } catch (error) {
      setBusy(false);
      setStatus(error.message, "error");
    }
  }

  function renderSportsChoice() {
    const excludeSd = Boolean(ctx.payload?.sports?.settings?.exclude_sd);
    body().innerHTML = `
      <h2>Enable Sports Automation?</h2>
      <div class="dev-onboarding-help">Sports Automation creates temporary event channels from your provider and keeps them updated on the Master Update schedule.</div>
      <div class="dev-onboarding-choice-row">
        <button class="dev-onboarding-choice" id="devSportsYes" type="button">
          <strong>Yes, enable sports</strong>
          <span>Pick teams and leagues next.</span>
        </button>
        <button class="dev-onboarding-choice" id="devSportsNo" type="button">
          <strong>No, not right now</strong>
          <span>Skip sports-specific setup and continue to Jellyfin.</span>
        </button>
      </div>
      <label class="dev-onboarding-ack" for="devExcludeSdChannels">
        <input id="devExcludeSdChannels" class="form-check-input" type="checkbox" ${excludeSd ? "checked" : ""}>
        <span><strong>Hide SD / Low Bandwidth Channels</strong><br><span class="dev-onboarding-muted">Hide low-bandwidth provider channels from the catalog and sports-generated feeds.</span></span>
      </label>
      ${actions(true, "")}
    `;
    document.getElementById("devOnboardingNext")?.remove();
    bindBack(1);
    document.getElementById("devSportsYes")?.addEventListener("click", () => chooseSports(true));
    document.getElementById("devSportsNo")?.addEventListener("click", () => chooseSports(false));
  }

  async function loadCatalog() {
    if (ctx.catalog.length) return;
    const [leagues, teams] = await Promise.all([
      api("/api/sports/catalog?type=league"),
      api("/api/sports/catalog?type=team"),
    ]);
    ctx.catalog = [...(leagues.items || []), ...(teams.items || [])];
    ctx.selectedRules = new Set(
      (ctx.payload?.sports?.rules || []).map(rule => `${rule.scope_type}:${rule.scope_id}`)
    );
  }

  function renderCatalogItems(query = "") {
    const target = document.getElementById("devSportsCatalog");
    if (!target) return;
    const needle = String(query || "").trim().toLowerCase();
    const items = ctx.catalog
      .filter(item => {
        if (!needle) return true;
        return `${item.name || ""} ${item.subtitle || ""} ${item.id || ""}`.toLowerCase().includes(needle);
      })
      .slice(0, 300);
    target.innerHTML = items.length ? items.map(item => {
      const key = `${item.scope_type}:${item.id}`;
      const checked = ctx.selectedRules.has(key) ? "checked" : "";
      const kind = item.scope_type === "team" ? "Team" : "League";
      return `
        <label class="dev-onboarding-catalog-item">
          <input class="form-check-input dev-sports-rule-check" type="checkbox" data-key="${esc(key)}" ${checked}>
          <span>
            <strong>${esc(item.name)}</strong>
            <span class="meta">${esc(kind)}${item.subtitle ? ` • ${item.subtitle}` : ""}</span>
          </span>
        </label>`;
    }).join("") : '<div class="p-3 dev-onboarding-muted">No matching teams or leagues.</div>';
  }

  async function renderSportsRules() {
    body().innerHTML = `
      <h2>Pick Teams / Leagues</h2>
      <div class="dev-onboarding-help">Choose the teams and leagues you want Sports Automation to follow. You can change these later from the normal Sports section.</div>
      <div class="dev-onboarding-catalog-toolbar">
        <input id="devSportsCatalogSearch" placeholder="Search teams or leagues…" autocomplete="off">
      </div>
      <div id="devSportsCatalog" class="dev-onboarding-catalog"><div class="p-3 dev-onboarding-muted">Loading sports catalog…</div></div>
      ${actions(true, "Save & Continue")}
    `;
    bindBack(2);
    try {
      await loadCatalog();
      renderCatalogItems();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }

    document.getElementById("devSportsCatalogSearch")?.addEventListener("input", event => {
      renderCatalogItems(event.target.value);
    });
    document.getElementById("devSportsCatalog")?.addEventListener("change", event => {
      const check = event.target.closest(".dev-sports-rule-check");
      if (!check) return;
      if (check.checked) ctx.selectedRules.add(check.dataset.key);
      else ctx.selectedRules.delete(check.dataset.key);
    });
    document.getElementById("devOnboardingNext")?.addEventListener("click", async () => {
      const selected = ctx.catalog.filter(item => ctx.selectedRules.has(`${item.scope_type}:${item.id}`));
      if (!selected.length) {
        setStatus("Choose at least one team or league, or go Back and disable Sports Automation.", "error");
        return;
      }
      setBusy(true, "Saving sports selections…");
      try {
        const data = await api("/api/sports/rules", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            items: selected.map(item => ({
              scope_type: item.scope_type,
              scope_id: item.id,
              feed_preference: "best",
            })),
          }),
        });
        ctx.payload.sports.rules = data.rules || [];
        await saveProgress(4, {sports_selection_count: selected.length});
        render();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      }
    });
  }

  async function chooseSportsApi(enabled) {
    setBusy(true, enabled ? "Enabling Sports API integration…" : "Using provider/EPG matching only…");
    try {
      await api("/api/sports/schedule-api", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled}),
      });
      const next = enabled ? 5 : 6;
      await saveProgress(next, {schedule_api_enabled: enabled});
      render();
    } catch (error) {
      setBusy(false);
      setStatus(error.message, "error");
    }
  }

  function renderSportsApiChoice() {
    body().innerHTML = `
      <h2>Use Sports API Integration?</h2>
      <div class="dev-onboarding-help">API-SPORTS can provide canonical schedules for supported MLB, NFL, and NCAA Football selections. Unsupported sports continue using provider/EPG matching.</div>
      <div class="dev-onboarding-choice-row">
        <button class="dev-onboarding-choice" id="devSportsApiYes" type="button">
          <strong>Yes, use Sports API</strong>
          <span>Enter the API-SPORTS key next.</span>
        </button>
        <button class="dev-onboarding-choice" id="devSportsApiNo" type="button">
          <strong>No</strong>
          <span>Use the provider and XMLTV matcher only.</span>
        </button>
      </div>
      ${actions(true, "")}
    `;
    document.getElementById("devOnboardingNext")?.remove();
    bindBack(3);
    document.getElementById("devSportsApiYes")?.addEventListener("click", () => chooseSportsApi(true));
    document.getElementById("devSportsApiNo")?.addEventListener("click", () => chooseSportsApi(false));
  }

  function renderSportsApiInfo() {
    const configured = Boolean(ctx.payload?.sports?.schedule_api?.key_configured);
    body().innerHTML = `
      <h2>Sports API Information</h2>
      <div class="dev-onboarding-help">The current integration uses API-SPORTS. One key is shared by the supported baseball and American-football adapters.</div>
      <div class="dev-onboarding-grid">
        <div class="dev-onboarding-field wide">
          <label for="devSportsApiKey">API-SPORTS key</label>
          <input id="devSportsApiKey" type="password" autocomplete="new-password" placeholder="${configured ? "Key already saved - enter a new value only to replace it" : "Paste API key"}">
        </div>
      </div>
      ${actions(true, configured ? "Continue" : "Save & Continue")}
    `;
    bindBack(4);
    document.getElementById("devOnboardingNext")?.addEventListener("click", async () => {
      const key = document.getElementById("devSportsApiKey")?.value.trim() || "";
      if (!configured && !key) {
        setStatus("Enter the API-SPORTS key.", "error");
        return;
      }
      setBusy(true, key ? "Saving API key…" : "Continuing with saved API key…");
      try {
        if (key) {
          const data = await api("/api/sports/schedule-api", {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: true, api_key: key}),
          });
          ctx.payload.sports.schedule_api = data.schedule_api || ctx.payload.sports.schedule_api;
        }
        await saveProgress(6, {schedule_api_configured: true});
        render();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      }
    });
  }

  async function completeSetup() {
    setBusy(true, "Finishing setup…");
    try {
      await api("/api/onboarding/complete", {method: "POST"});
      const wizardBody = body();
      if (wizardBody) {
        wizardBody.innerHTML = `
          <h2>Starting Your First Update</h2>
          <div class="dev-onboarding-help">Your settings are saved. M3U Web Picker is now downloading provider and guide data and publishing the first outputs.</div>
          <div class="dev-initial-refresh-progress" aria-hidden="true">
            <span class="dev-initial-refresh-spinner"></span>
            <div><strong>Setup is still working</strong><span>This first update commonly takes 5–10 minutes. Please leave this page open.</span></div>
          </div>
          <div class="dev-onboarding-status" role="status" aria-live="polite">Preparing the first Master Update…</div>`;
      }
      document.documentElement.classList.add("onboarding-initial-refresh-pending");
      setTimeout(() => location.reload(), 150);
    } catch (error) {
      setBusy(false);
      setStatus(error.message, "error");
    }
  }

  async function continueToEncoding() {
    await saveProgress(8, {});
    await render();
  }

  async function chooseJellyfin(usingJellyfin) {
    setBusy(true, usingJellyfin ? "Configuring Jellyfin integration…" : "Leaving Jellyfin integration disabled…");
    try {
      const data = await api("/api/jellyfin-cache", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          using_jellyfin: usingJellyfin,
          cleanup_enabled: false,
          acknowledged: false,
        }),
      });
      ctx.payload.jellyfin = data.jellyfin;
      if (usingJellyfin) {
        await saveProgress(7, {using_jellyfin: true});
        render();
      } else {
        await saveProgress(7, {using_jellyfin: false, jellyfin_cache_cleanup: false});
        await continueToEncoding();
      }
    } catch (error) {
      setBusy(false);
      setStatus(error.message, "error");
    }
  }

  function renderJellyfinChoice() {
    const sportsEnabled = Boolean(ctx.payload?.sports?.settings?.enabled);
    body().innerHTML = `
      <h2>Are You Using Jellyfin?</h2>
      <div class="dev-onboarding-help">The optional cache integration can clear Jellyfin's local cache only after a successful Picker update. It is completely optional.</div>
      <div class="dev-onboarding-choice-row">
        <button class="dev-onboarding-choice" id="devJellyfinYes" type="button">
          <strong>Yes, I use Jellyfin</strong>
          <span>Configure the local Jellyfin cache directory next.</span>
        </button>
        <button class="dev-onboarding-choice" id="devJellyfinNo" type="button">
          <strong>No</strong>
          <span>Finish setup without cache integration.</span>
        </button>
      </div>
      ${actions(true, "")}
    `;
    document.getElementById("devOnboardingNext")?.remove();
    bindBack(sportsEnabled ? (ctx.payload?.sports?.schedule_api?.enabled ? 5 : 4) : 2);
    document.getElementById("devJellyfinYes")?.addEventListener("click", () => chooseJellyfin(true));
    document.getElementById("devJellyfinNo")?.addEventListener("click", () => chooseJellyfin(false));
  }

  function mountCommand(path) {
    const quoted = String(path || "/path/to/jellyfin/cache").replaceAll('"', '\\"');
    return `M3U_JELLYFIN_CACHE_DIR="${quoted}" docker compose up -d --build`;
  }

  function renderJellyfinCache() {
    const jellyfin = ctx.payload?.jellyfin || {};
    const runtime = jellyfin.runtime || {};
    const savedPath = jellyfin.host_path || runtime.configured_host_path || "";
    body().innerHTML = `
      <h2>Jellyfin Cache Directory</h2>
      <div class="dev-onboarding-help">Paste the local Jellyfin cache directory. The M3U Web Picker container must be started with that same host path mounted through <code>M3U_JELLYFIN_CACHE_DIR</code>.</div>
      <div class="dev-onboarding-grid">
        <div class="dev-onboarding-field wide">
          <label for="devJellyfinCachePath">Local Jellyfin cache directory</label>
          <input id="devJellyfinCachePath" value="${esc(savedPath)}" placeholder="/path/to/jellyfin/cache" autocomplete="off">
        </div>
      </div>
      <div class="dev-onboarding-warning">
        <strong>Jellyfin cache integration</strong><br>
        M3U Web Picker can clear the configured Jellyfin cache after a successful update to help prevent stale Live TV logos and metadata.<br><br>
        <strong>Warning:</strong> This may affect cached information for downloaded movies, downloaded TV shows, and DVR recordings.
      </div>
      <label class="dev-onboarding-ack" for="devJellyfinUnderstand">
        <input id="devJellyfinUnderstand" type="checkbox" role="switch" ${jellyfin.acknowledged ? "checked" : ""}>
        <span><strong>I understand the risks</strong><br><span class="dev-onboarding-muted">Required before automatic cache cleanup can be enabled.</span></span>
      </label>
      <div class="dev-onboarding-summary">
        <strong>Container mount:</strong> ${runtime.mount_configured ? "configured" : "not configured yet"}<br>
        <span class="dev-onboarding-muted">If validation says the mount is missing, restart the container with the pasted path:</span>
        <code class="dev-onboarding-code" id="devJellyfinMountCommand">${esc(mountCommand(savedPath))}</code>
      </div>
      ${actions(true, "Validate, Enable & Finish")}
    `;
    bindBack(6);
    const pathInput = document.getElementById("devJellyfinCachePath");
    const ack = document.getElementById("devJellyfinUnderstand");
    const button = document.getElementById("devOnboardingNext");
    const command = document.getElementById("devJellyfinMountCommand");
    const updateButton = () => {
      if (button) button.disabled = !ack?.checked;
      if (command) command.textContent = mountCommand(pathInput?.value.trim() || "");
    };
    ack?.addEventListener("change", updateButton);
    pathInput?.addEventListener("input", updateButton);
    updateButton();

    button?.addEventListener("click", async () => {
      const hostPath = pathInput?.value.trim() || "";
      if (!ack?.checked) {
        setStatus("Turn on ‘I understand the risks’ before enabling cache cleanup.", "error");
        return;
      }
      if (!hostPath) {
        setStatus("Paste the Jellyfin cache directory.", "error");
        return;
      }
      setBusy(true, "Validating Jellyfin cache path…");
      try {
        await api("/api/jellyfin-cache/validate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({host_path: hostPath}),
        });
        const data = await api("/api/jellyfin-cache", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            using_jellyfin: true,
            cleanup_enabled: true,
            acknowledged: true,
            host_path: hostPath,
          }),
        });
        ctx.payload.jellyfin = data.jellyfin;
        await saveProgress(7, {
          using_jellyfin: true,
          jellyfin_cache_cleanup: true,
          jellyfin_cache_path: hostPath,
        });
        setStatus("Jellyfin cache path validated. Continuing to encoding…", "success");
        await continueToEncoding();
      } catch (error) {
        setBusy(false);
        setStatus(error.message, "error");
      }
    });
  }

  function renderEncoding() {
    const pipeline = ctx.payload?.media_pipeline || {};
    const capability = pipeline.capability || {};
    body().innerHTML = `
      <h2>FFmpeg Encoding</h2>
      <div class="dev-onboarding-help">Encoding can normalize every curated channel for browser, M3U, HDHomeRun, Roku, and Cast clients. Direct streaming is the safest default and encoding can be enabled later under Settings → Encoding.</div>
      <div class="dev-onboarding-warning" id="devEncodingWarning">${capability.tested_at
        ? (capability.hardware_available
          ? `Hardware encoding passed using <code>${esc(capability.active_encoder)}</code>. Multiple simultaneous streams may still exceed this system's capacity.`
          : "GPU acceleration was not detected or failed its test. CPU encoding may cause buffering or playback failures, especially with multiple clients.")
        : "Run the hardware check before enabling FFmpeg encoding."}</div>
      <label class="dev-onboarding-ack" for="devEncodingUnderstand">
        <input id="devEncodingUnderstand" type="checkbox" role="switch">
        <span><strong>I understand the performance risk</strong><br><span class="dev-onboarding-muted">Required before enabling application-wide encoding.</span></span>
      </label>
      <div class="dev-onboarding-summary">The direct fallback playlist always remains available at <code>/playlist/channels.direct.m3u</code>.</div>
      <div class="dev-onboarding-actions"><button class="dev-onboarding-btn" id="devEncodingBack" type="button">Back</button><span></span><button class="dev-onboarding-btn" id="devEncodingTest" type="button">Run Hardware Check</button><button class="dev-onboarding-btn" id="devEncodingDirect" type="button">Keep Direct Streaming</button><button class="dev-onboarding-btn primary" id="devEncodingEnable" type="button" disabled>Enable Encoding</button></div>`;
    const encodingAck = document.getElementById("devEncodingUnderstand");
    const enableEncoding = document.getElementById("devEncodingEnable");
    const syncEnableEncoding = () => { if (enableEncoding) enableEncoding.disabled = !encodingAck?.checked; };
    encodingAck?.addEventListener("change", syncEnableEncoding);
    syncEnableEncoding();
    document.getElementById("devEncodingBack")?.addEventListener("click", async () => { await saveProgress(6, {}); render(); });
    document.getElementById("devEncodingTest")?.addEventListener("click", async () => {
      setBusy(true, "Testing FFmpeg and available encoders…");
      try {
        ctx.payload.media_pipeline = await api("/api/media-pipeline/test", {method: "POST"});
        setBusy(false);
        renderEncoding();
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
    document.getElementById("devEncodingDirect")?.addEventListener("click", async () => {
      setBusy(true, "Saving direct-streaming preference…");
      try {
        await api("/api/media-pipeline", {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: false})});
        await completeSetup();
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
    enableEncoding?.addEventListener("click", async () => {
      if (!encodingAck?.checked) { setStatus("Acknowledge the performance warning before enabling encoding.", "error"); return; }
      setBusy(true, "Enabling FFmpeg encoding…");
      try {
        await api("/api/media-pipeline", {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({enabled: true, warning_acknowledged: true, encoder: "auto"})});
        await completeSetup();
      } catch (error) { setBusy(false); setStatus(error.message, "error"); }
    });
  }

  async function render() {
    shell();
    ctx.step = Math.min(STEP_COUNT, Math.max(1, Number(ctx.step || 1)));
    updateStepCount();
    try {
      if (ctx.step === 1) await renderProvider();
      else if (ctx.step === 2) renderSportsChoice();
      else if (ctx.step === 3) await renderSportsRules();
      else if (ctx.step === 4) renderSportsApiChoice();
      else if (ctx.step === 5) renderSportsApiInfo();
      else if (ctx.step === 6) renderJellyfinChoice();
      else if (ctx.step === 7) renderJellyfinCache();
      else renderEncoding();
      updateStepCount();
    } catch (error) {
      body().innerHTML = `
        <h2>Setup could not continue</h2>
        <div class="dev-onboarding-warning">${esc(error.message)}</div>
        <div class="dev-onboarding-actions"><span></span><button class="dev-onboarding-btn primary" id="devOnboardingRetry" type="button">Retry</button></div>`;
      document.getElementById("devOnboardingRetry")?.addEventListener("click", () => location.reload());
    }
  }

  async function start() {
    try {
      ctx.payload = await api("/api/onboarding");
      const enabled = ctx.payload.enabled;
      if (!enabled || !ctx.payload.state?.required || ctx.payload.state?.completed) {
        document.documentElement.classList.remove("onboarding-pending", "onboarding-active");
        return;
      }
      ctx.step = Number(ctx.payload.state.current_step || 1);
      await render();
    } catch (error) {
      document.documentElement.classList.remove("onboarding-pending", "onboarding-active");
      console.error("Onboarding failed to initialize:", error);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
