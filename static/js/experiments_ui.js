(() => {
  "use strict";

  function formatApiTimestamp(value) {
    if (!value) return "Never";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString([], {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    });
  }

  function formatApiProduct(value) {
    const text = String(value || "").replaceAll("_", " ").trim();
    if (!text) return "—";
    return text.replace(/\b\w/g, character => character.toUpperCase());
  }

  function inferredDatasetStatus(entry, api) {
    if (!entry.configured && !api.configured) {
      return {code: "needs_key", label: "Needs key"};
    }
    if (!entry.enabled && !api.enabled) {
      return {code: "disabled", label: "Disabled"};
    }
    if (entry.last_fetch_at) {
      return {code: "cached", label: "Cached"};
    }
    return {code: "no_cache", label: "No successful cache"};
  }

  function datasetStatus(entry, api) {
    const code = String(entry.status_code || "").trim();
    const label = String(entry.status_label || "").trim();
    if (code && label) return {code, label};
    return inferredDatasetStatus(entry, api);
  }

  function statusBadgeClass(code) {
    if (code === "cached") return "text-bg-success";
    if (code === "error") return "text-bg-danger";
    if (code === "stale" || code === "partial") return "text-bg-warning text-dark";
    return "text-bg-secondary";
  }

  function scheduleApiEntries(api) {
    return Array.isArray(api.apis) ? api.apis : [];
  }

  function scheduleApiSummary(api) {
    const entries = scheduleApiEntries(api);
    const supplied = api.dataset_summary || {};
    return {
      planned: Number(supplied.planned ?? entries.length),
      cached: Number(supplied.cached ?? entries.filter(entry => Boolean(entry.last_fetch_at)).length),
      issues: Number(
        supplied.issues ?? entries.filter(entry => {
          const code = datasetStatus(entry, api).code;
          return ["error", "stale", "partial"].includes(code);
        }).length
      ),
      noCache: Number(
        supplied.no_cache ?? entries.filter(entry => datasetStatus(entry, api).code === "no_cache").length
      )
    };
  }

  function polishScheduleApiCredentialControls() {
    const keyInput = document.getElementById("sportsScheduleApiKey");
    const saveButton = document.getElementById("sportsScheduleApiSave");
    const removeButton = document.getElementById("sportsScheduleApiRemove");
    const status = document.getElementById("sportsScheduleApiStatus");
    if (!keyInput || !saveButton || !status) return;

    const keyColumn = keyInput.closest("[class*='col-lg-']");
    const saveColumn = saveButton.closest("[class*='col-lg-']");
    if (keyColumn) {
      keyColumn.classList.remove("col-lg-8");
      keyColumn.classList.add("col-lg-9");
    }
    if (saveColumn) {
      saveColumn.classList.remove("col-lg-2");
      saveColumn.classList.add("col-lg-3");
    }

    let meta = document.getElementById("sportsScheduleApiCredentialMeta");
    if (!meta) {
      meta = document.createElement("div");
      meta.id = "sportsScheduleApiCredentialMeta";
      meta.className = "schedule-api-credential-meta mt-2";
      status.parentNode.insertBefore(meta, status);
      meta.appendChild(status);
    }

    if (removeButton && removeButton.parentElement !== meta) {
      const previousParent = removeButton.parentElement;
      meta.appendChild(removeButton);
      if (previousParent && previousParent.children.length === 0) previousParent.remove();
    }
    if (removeButton) {
      removeButton.className = "btn btn-link btn-sm text-danger text-decoration-none p-0 flex-shrink-0 d-none";
      removeButton.textContent = "Remove saved key";
    }

    let health = document.getElementById("sportsScheduleApiHealth");
    if (!health) {
      health = document.createElement("div");
      health.id = "sportsScheduleApiHealth";
      health.className = "schedule-api-health small mt-1";
      meta.insertAdjacentElement("afterend", health);
    }

    if (!keyInput.dataset.apiUiBound) {
      keyInput.dataset.apiUiBound = "true";
      keyInput.addEventListener("input", syncScheduleApiSaveButton);
    }
  }

  function syncScheduleApiSaveButton() {
    const api = sportsState.schedule_api || {};
    const keyInput = document.getElementById("sportsScheduleApiKey");
    const saveButton = document.getElementById("sportsScheduleApiSave");
    if (!keyInput || !saveButton) return;
    const configured = Boolean(api.key_configured || api.configured);
    const hasPendingKey = Boolean(keyInput.value.trim());
    saveButton.textContent = configured ? "Replace API key" : "Save API key";
    saveButton.disabled = !api.enabled || !hasPendingKey;
  }

  function renderScheduleApiSummary(api) {
    const status = document.getElementById("sportsScheduleApiStatus");
    const health = document.getElementById("sportsScheduleApiHealth");
    const refreshButton = document.getElementById("sportsScheduleApiRefresh");
    const removeButton = document.getElementById("sportsScheduleApiRemove");
    if (!status || !health) return;

    const configured = Boolean(api.key_configured || api.configured);
    const entries = scheduleApiEntries(api);
    const summary = scheduleApiSummary(api);

    if (removeButton) {
      removeButton.classList.toggle("d-none", !configured);
      removeButton.disabled = !configured;
    }

    if (refreshButton) {
      refreshButton.textContent = "Refresh API schedules";
      refreshButton.title = "Bypass the same-day cache and refetch every planned API-backed schedule dataset.";
    }

    if (!api.enabled) {
      health.className = "schedule-api-health small mt-1 small-muted";
      health.textContent = configured
        ? "API key is saved but API schedule matching is disabled. Provider/EPG matching remains active."
        : "Provider/EPG matching remains active.";
      syncScheduleApiSaveButton();
      return;
    }

    if (!configured) {
      health.className = "schedule-api-health small mt-1 text-warning";
      health.textContent = "Enter an API-SPORTS key to enable canonical schedule data. Provider/EPG matching remains active until then.";
      syncScheduleApiSaveButton();
      return;
    }

    if (!entries.length) {
      status.textContent = "API key saved ✓ • API-SPORTS enabled • no API-backed dataset required by the current selections";
      health.className = "schedule-api-health small mt-1 small-muted";
      health.textContent = "Unsupported sports continue through the provider/EPG matcher without consuming API requests.";
      syncScheduleApiSaveButton();
      return;
    }

    const bits = [
      "API key saved ✓",
      `${summary.cached} of ${summary.planned} dataset${summary.planned === 1 ? "" : "s"} cached`
    ];
    if (summary.issues) bits.push(`${summary.issues} need attention`);
    if (api.last_fetch_at) bits.push(`Last success ${formatApiTimestamp(api.last_fetch_at)}`);
    if (api.remaining_quota !== null && api.remaining_quota !== undefined) {
      bits.push(`${api.remaining_quota} requests remaining`);
    }
    status.textContent = bits.join(" • ");

    const issueEntries = entries.filter(entry => {
      const code = datasetStatus(entry, api).code;
      return ["error", "stale", "partial"].includes(code);
    });
    const noCacheEntries = entries.filter(entry => datasetStatus(entry, api).code === "no_cache");

    if (issueEntries.length) {
      const labels = issueEntries.map(entry => entry.scope || entry.id).filter(Boolean);
      health.className = "schedule-api-health small mt-1 text-warning";
      health.textContent = `${labels.join(", ")} ${issueEntries.length === 1 ? "has" : "have"} a refresh problem. Cached data is kept when available; otherwise that sport falls back to provider/EPG matching.`;
    } else if (noCacheEntries.length) {
      const labels = noCacheEntries.map(entry => entry.scope || entry.id).filter(Boolean);
      health.className = "schedule-api-health small mt-1 text-warning";
      health.textContent = `${labels.join(", ")} ${noCacheEntries.length === 1 ? "has" : "have"} no successful API cache yet. Provider/EPG matching remains active until a refresh succeeds.`;
    } else {
      health.className = "schedule-api-health small mt-1 text-success";
      health.textContent = "Every planned API-backed dataset has a successful cache.";
    }

    syncScheduleApiSaveButton();
  }

  function renderScheduleApiDatasetTable(api) {
    const target = document.getElementById("sportsScheduleApiList");
    if (!target) return;
    const entries = scheduleApiEntries(api);
    if (!entries.length) return;

    target.innerHTML = entries.map(entry => {
      const state = datasetStatus(entry, api);
      const lastUpdated = entry.last_fetch_at ? formatApiTimestamp(entry.last_fetch_at) : "Never";
      const cacheCount = Number(entry.cached_event_count || 0);
      const cache = entry.last_fetch_at ? `${cacheCount.toLocaleString()} games` : "No cache";
      const details = [];
      if (entry.last_error) details.push(entry.last_error);
      if (entry.reference_error) details.push(entry.reference_error);
      if (entry.last_attempt_at && ["error", "stale", "partial"].includes(state.code)) {
        details.push(`Last attempt ${formatApiTimestamp(entry.last_attempt_at)}`);
      }
      const detailHtml = details.length
        ? `<div class="schedule-api-row-detail">${details.map(value => escapeHtml(value)).join(" · ")}</div>`
        : "";

      return `
        <tr data-api-dataset="${escapeHtml(entry.id || "")}">
          <td>${escapeHtml(entry.provider || "API-SPORTS")}</td>
          <td>${escapeHtml(formatApiProduct(entry.product))}</td>
          <td>${escapeHtml(entry.scope || "—")}</td>
          <td>
            <span class="badge ${statusBadgeClass(state.code)}">${escapeHtml(state.label)}</span>
            ${detailHtml}
          </td>
          <td>${escapeHtml(lastUpdated)}</td>
          <td>${escapeHtml(cache)}</td>
        </tr>`;
    }).join("");
  }

  function enhanceScheduleApiUi() {
    const api = sportsState.schedule_api || {};
    polishScheduleApiCredentialControls();
    renderScheduleApiSummary(api);
    renderScheduleApiDatasetTable(api);

    const label = document.querySelector("label[for='sportsScheduleApiEnabled']");
    if (label) label.textContent = "Use API-SPORTS schedules";
  }

  function installSportsStatusHeader() {
    const brand = document.querySelector(".app-brand-block");
    const status = document.getElementById("sportsScanStatus");
    if (!brand || !status) return;
    brand.style.flex = "1 1 620px";
    status.classList.remove("mb-3");
    status.classList.add("mt-4");
    status.style.width = "100%";
    brand.appendChild(status);

    if (typeof renderSportsScanStatus !== "function") return;
    const baseRenderSportsScanStatus = renderSportsScanStatus;
    renderSportsScanStatus = function() {
      baseRenderSportsScanStatus();
      const scan = sportsState.scan || {running: false};
      const running = Boolean(masterUpdateBusy || masterUpdateState.running || scan.running);
      if (!running) return;
      const details = document.getElementById("sportsScanStatusDetails");
      if (!details) return;
      details.textContent = details.textContent
        .split(" • ")
        .filter(part => !/^Elapsed\b/i.test(part.trim()))
        .join(" • ");
    };
  }

  installSportsStatusHeader();

  if (typeof renderSportsScheduleApi === "function") {
    const baseRenderSportsScheduleApi = renderSportsScheduleApi;
    renderSportsScheduleApi = function() {
      baseRenderSportsScheduleApi();
      enhanceScheduleApiUi();
    };
  }

  enhanceScheduleApiUi();
})();
