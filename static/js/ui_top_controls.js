(() => {
  "use strict";

  const el = id => document.getElementById(id);

  function shortTime(value) {
    if (!value) return "Never";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
      weekday: "short",
      hour: "numeric",
      minute: "2-digit"
    });
  }

  function latestApiTime(api, entries) {
    const values = [api?.last_fetch_at, ...entries.map(entry => entry?.last_fetch_at)]
      .map(value => ({value, time: Date.parse(value || "")}))
      .filter(item => Number.isFinite(item.time))
      .sort((a, b) => b.time - a.time);
    return values[0]?.value || null;
  }

  function ensureLayout() {
    const hdhrPanel = document.querySelector(".ui-top-hdhr-panel");
    const updateButton = el("masterUpdateNowBtn");
    if (!hdhrPanel) return false;

    const row = hdhrPanel.firstElementChild || hdhrPanel;
    row.classList.add("ui-system-controls-row");

    if (updateButton && updateButton.parentElement !== row) {
      updateButton.classList.remove("ui-top-update-btn");
      updateButton.classList.add("ui-system-update-btn");
      row.appendChild(updateButton);
    }

    let apiStatus = el("uiSportsApiStatus");
    if (!apiStatus) {
      apiStatus = document.createElement("div");
      apiStatus.id = "uiSportsApiStatus";
      apiStatus.className = "ui-sports-api-status d-none";
      apiStatus.setAttribute("role", "status");
      apiStatus.setAttribute("aria-live", "polite");
      hdhrPanel.insertAdjacentElement("afterend", apiStatus);
    }
    return true;
  }

  function renderSportsApiStatus() {
    if (!ensureLayout()) return;
    const target = el("uiSportsApiStatus");
    if (!target) return;

    const api = sportsState?.schedule_api || {};
    if (!api.enabled) {
      target.className = "ui-sports-api-status d-none";
      target.textContent = "";
      return;
    }

    const entries = Array.isArray(api.apis) ? api.apis : [];
    const latest = latestApiTime(api, entries);
    const totalGames = entries.length
      ? entries.reduce((sum, entry) => sum + Number(entry.cached_event_count || 0), 0)
      : Number(api.cached_event_count || 0);
    const codes = entries.map(entry => String(entry.status_code || "").toLowerCase());
    const hasError = codes.includes("error") || entries.some(entry => entry.last_error);
    const hasWarning = codes.some(code => code === "stale" || code === "partial");
    const currentEntries = entries.filter(entry => entry.cache_current);
    const allCurrent = entries.length > 0 && currentEntries.length === entries.length;

    let state = "neutral";
    let label = "Enabled";
    if (!api.effective) {
      state = "warning";
      label = api.key_configured ? "Enabled · inactive" : "Enabled · needs API key";
    } else if (hasError) {
      state = "error";
      label = "Last refresh failed";
    } else if (hasWarning) {
      state = "warning";
      label = "Last refresh needs attention";
    } else if (allCurrent || api.cache_current) {
      state = "success";
      label = "Current";
    }

    const bits = [
      "Sports API",
      label,
      latest ? `Last ${shortTime(latest)}` : "No refresh yet",
      api.effective ? `${totalGames.toLocaleString()} game${totalGames === 1 ? "" : "s"}` : ""
    ].filter(Boolean);

    target.className = `ui-sports-api-status is-${state}`;
    target.innerHTML = `<span class="ui-sports-api-dot" aria-hidden="true"></span><span>${bits.join(" · ")}</span>`;
  }

  function install() {
    if (!ensureLayout()) {
      const observer = new MutationObserver(() => {
        if (ensureLayout()) {
          observer.disconnect();
          renderSportsApiStatus();
        }
      });
      observer.observe(document.body, {childList: true, subtree: true});
      return;
    }
    renderSportsApiStatus();
  }

  install();

  if (typeof renderSportsScheduleApi === "function") {
    const base = renderSportsScheduleApi;
    renderSportsScheduleApi = function() {
      base();
      renderSportsApiStatus();
    };
  }

  if (typeof applySportsState === "function") {
    const base = applySportsState;
    applySportsState = function() {
      base();
      renderSportsApiStatus();
    };
  }
})();
