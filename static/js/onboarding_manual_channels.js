(() => {
  "use strict";

  const state = {
    channels: [],
    selected: new Set(),
    loading: false,
    checkedSportsStep: false,
    manualDone: false,
    replacing: false,
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

  function body() {
    return document.getElementById("devOnboardingBody");
  }

  function heading() {
    return body()?.querySelector("h2")?.textContent?.trim() || "";
  }

  function setStepCount() {
    const target = document.getElementById("devOnboardingStepCount");
    if (!target) return;
    const displaySteps = {
      "Primary Provider": 1,
      "Manual Channels": 2,
      "Enable Sports Automation?": 3,
      "Pick Teams / Leagues": 4,
      "Use Sports API Integration?": 5,
      "Sports API Information": 6,
      "Are You Using Jellyfin?": 7,
      "Jellyfin Cache Directory": 8,
    };
    const step = displaySteps[heading()];
    if (!step) return;
    const text = `Step ${step} of 8`;
    if (target.textContent !== text) target.textContent = text;
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
  }

  function visibleChannels(query = "") {
    const needle = String(query || "").trim().toLowerCase();
    return state.channels.filter(channel => {
      if (!needle) return true;
      return `${channel.name || ""} ${channel.group || ""} ${channel.tvg_id || ""}`
        .toLowerCase()
        .includes(needle);
    });
  }

  function updateSelectedCount() {
    const target = document.getElementById("devManualSelectedCount");
    if (target) target.textContent = `${state.selected.size} selected`;
  }

  function renderManualList(query = "") {
    const target = document.getElementById("devManualChannelList");
    if (!target) return;
    const matches = visibleChannels(query);
    const shown = matches.slice(0, 400);
    target.dataset.visibleIds = shown.map(channel => String(channel.id)).join(",");
    target.innerHTML = shown.length
      ? shown.map(channel => {
          const id = Number(channel.id);
          const checked = state.selected.has(id) ? "checked" : "";
          const meta = [channel.group, channel.tvg_chno ? `Provider #${channel.tvg_chno}` : ""]
            .filter(Boolean)
            .join(" • ");
          return `
            <label class="dev-onboarding-catalog-item">
              <input class="form-check-input dev-manual-channel-check" type="checkbox" data-id="${id}" ${checked}>
              <span>
                <strong>${esc(channel.name || `Channel ${id}`)}</strong>
                <span class="meta">${esc(meta || channel.tvg_id || "Provider channel")}</span>
              </span>
            </label>`;
        }).join("")
      : '<div class="p-3 dev-onboarding-muted">No matching channels.</div>';

    const note = document.getElementById("devManualResultNote");
    if (note) {
      note.textContent = matches.length > shown.length
        ? `Showing first ${shown.length} of ${matches.length} matches. Narrow the search to see more.`
        : `${matches.length} matching channel${matches.length === 1 ? "" : "s"}.`;
    }
    updateSelectedCount();
  }

  function visibleIds() {
    const raw = document.getElementById("devManualChannelList")?.dataset.visibleIds || "";
    return raw.split(",").filter(Boolean).map(value => Number(value)).filter(Number.isFinite);
  }

  async function renderManualChannels() {
    if (state.replacing || heading() !== "Enable Sports Automation?") return;
    state.replacing = true;
    const target = body();
    if (!target) {
      state.replacing = false;
      return;
    }

    target.innerHTML = `
      <h2>Manual Channels</h2>
      <div class="dev-onboarding-help">Choose any regular provider channels you always want in the lineup. Sports Automation channels are added separately on the next steps. You can leave this empty for a sports-only setup.</div>
      <div class="dev-onboarding-catalog-toolbar">
        <input id="devManualChannelSearch" placeholder="Search channels or groups…" autocomplete="off">
        <button class="dev-onboarding-btn" id="devManualSelectVisible" type="button">Select visible</button>
        <button class="dev-onboarding-btn" id="devManualClearVisible" type="button">Clear visible</button>
      </div>
      <div class="dev-onboarding-summary" style="display:flex;justify-content:space-between;gap:12px;align-items:center;">
        <span id="devManualSelectedCount">0 selected</span>
        <span class="dev-onboarding-muted" id="devManualResultNote"></span>
      </div>
      <div id="devManualChannelList" class="dev-onboarding-catalog"><div class="p-3 dev-onboarding-muted">Loading provider channels…</div></div>
      <div class="dev-onboarding-actions">
        <button class="dev-onboarding-btn" id="devManualBack" type="button">Back</button>
        <div class="dev-onboarding-actions-right">
          <button class="dev-onboarding-btn primary" id="devManualContinue" type="button">Save & Continue</button>
        </div>
      </div>
      <div class="dev-onboarding-status" id="devOnboardingStatus" role="status" aria-live="polite"></div>
    `;
    setStepCount();

    try {
      const payload = await api("/api/channels");
      if (heading() !== "Manual Channels") return;
      state.channels = (payload.channels || []).filter(channel => {
        const id = Number(channel.id);
        return Number.isFinite(id) && id >= 0 && !channel.is_sports_generated;
      });
      state.selected = new Set((payload.selected_ids || []).map(Number).filter(Number.isFinite));
      renderManualList();
    } catch (error) {
      setStatus(error.message || "Could not load provider channels.", "error");
    } finally {
      state.replacing = false;
    }

    const search = document.getElementById("devManualChannelSearch");
    search?.addEventListener("input", event => renderManualList(event.target.value));

    document.getElementById("devManualChannelList")?.addEventListener("change", event => {
      const check = event.target.closest(".dev-manual-channel-check");
      if (!check) return;
      const id = Number(check.dataset.id);
      if (!Number.isFinite(id)) return;
      if (check.checked) state.selected.add(id);
      else state.selected.delete(id);
      updateSelectedCount();
    });

    document.getElementById("devManualSelectVisible")?.addEventListener("click", () => {
      for (const id of visibleIds()) state.selected.add(id);
      renderManualList(search?.value || "");
    });

    document.getElementById("devManualClearVisible")?.addEventListener("click", () => {
      for (const id of visibleIds()) state.selected.delete(id);
      renderManualList(search?.value || "");
    });

    document.getElementById("devManualBack")?.addEventListener("click", async () => {
      if (state.loading) return;
      state.loading = true;
      try {
        await api("/api/onboarding", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({current_step: 1, answers: {manual_channels_done: false}}),
        });
        location.reload();
      } catch (error) {
        state.loading = false;
        setStatus(error.message, "error");
      }
    });

    document.getElementById("devManualContinue")?.addEventListener("click", async () => {
      if (state.loading) return;
      state.loading = true;
      const button = document.getElementById("devManualContinue");
      if (button) {
        button.disabled = true;
        button.textContent = "Saving…";
      }
      setStatus("Saving manual channel selections…");
      try {
        await api("/api/selection", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ids: [...state.selected]}),
        });
        await api("/api/onboarding", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            current_step: 2,
            answers: {
              manual_channels_done: true,
              manual_channel_count: state.selected.size,
            },
          }),
        });
        location.reload();
      } catch (error) {
        state.loading = false;
        if (button) {
          button.disabled = false;
          button.textContent = "Save & Continue";
        }
        setStatus(error.message || "Could not save manual channels.", "error");
      }
    });
  }

  async function maybeInsertManualStep() {
    setStepCount();
    if (heading() !== "Enable Sports Automation?" || state.checkedSportsStep || state.loading) return;
    state.checkedSportsStep = true;
    try {
      const payload = await api("/api/onboarding");
      state.manualDone = Boolean(payload.state?.answers?.manual_channels_done);
      if (!state.manualDone && heading() === "Enable Sports Automation?") {
        await renderManualChannels();
      }
    } catch (error) {
      state.checkedSportsStep = false;
      console.warn("Could not determine manual-channel onboarding state:", error);
    }
  }

  document.addEventListener("click", event => {
    const back = event.target.closest("#devOnboardingBack");
    if (!back || heading() !== "Enable Sports Automation?" || !state.manualDone) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void (async () => {
      try {
        await api("/api/onboarding", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({current_step: 2, answers: {manual_channels_done: false}}),
        });
        location.reload();
      } catch (error) {
        console.warn("Could not return to manual-channel setup:", error);
      }
    })();
  }, true);

  const observer = new MutationObserver(() => {
    state.checkedSportsStep = heading() === "Enable Sports Automation?" ? state.checkedSportsStep : false;
    void maybeInsertManualStep();
  });
  observer.observe(document.body, {childList: true, subtree: true});
  void maybeInsertManualStep();
})();
