(() => {
  "use strict";

  const elements = {
    guide: document.getElementById("remoteGuideButton"),
    alerts: document.getElementById("remoteAlertsButton"),
    disableAlerts: document.getElementById("remoteDisableAlertsButton"),
    search: document.getElementById("remoteSearch"),
    listLabel: document.getElementById("remoteListLabel"),
    count: document.getElementById("remoteCount"),
    status: document.getElementById("remoteStatus"),
    current: document.getElementById("remoteCurrent"),
    channels: document.getElementById("remoteChannels"),
    empty: document.getElementById("remoteEmpty"),
    heading: document.getElementById("remoteAvailableHeading"),
    sportsFilters: document.getElementById("remoteSportsFilters"),
    modes: [...document.querySelectorAll("[data-remote-mode]")],
  };

  let state = { channels: [], sports: [], selected_id: "", switching: false };
  let busyChannel = "";
  let mode = localStorage.getItem("m3u-remote-mode") === "sports" ? "sports" : "tv";
  let selectedSport = localStorage.getItem("m3u-remote-sport") || "all";
  let alertRegistration = null;
  let alertSubscription = null;
  let alertBusy = false;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function parseTime(value) {
    const date = new Date(String(value || ""));
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatClock(value) {
    const date = parseTime(value);
    if (!date) return "";
    return new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(date);
  }

  function programmeText(channel) {
    const programme = channel.now || null;
    if (!programme) {
      return {
        title: "No guide data",
        meta: "Currently playing programme unavailable",
        next: channel.next?.title ? `Next: ${channel.next.title}` : "",
      };
    }
    const start = formatClock(programme.start);
    const stop = formatClock(programme.stop);
    return {
      title: programme.title || "Untitled programme",
      meta: start && stop ? `${start}–${stop}` : "Now playing",
      next: channel.next?.title ? `Next: ${channel.next.title}${formatClock(channel.next.start) ? ` · ${formatClock(channel.next.start)}` : ""}` : "",
    };
  }

  function logoMarkup(channel) {
    if (channel.logo) {
      return `<img class="remote-logo" src="${escapeHtml(channel.logo)}" alt="" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span class="remote-logo remote-logo-placeholder" hidden>TV</span>`;
    }
    return '<span class="remote-logo remote-logo-placeholder">TV</span>';
  }

  function channelCard(channel, { current = false } = {}) {
    const programme = programmeText(channel);
    const id = escapeHtml(channel.id);
    const isBusy = busyChannel === channel.id;
    const button = current
      ? '<button class="btn btn-danger btn-sm remote-action" type="button" data-stop>Stop</button>'
      : `<button class="btn btn-success btn-sm remote-action" type="button" data-channel-id="${id}" ${isBusy ? "disabled" : ""}>${isBusy ? "Warming…" : "Play"}</button>`;
    const cardAction = current ? "" : ` data-channel-id="${id}"`;
    return `
      <article class="remote-channel-card"${cardAction} data-searchable="${escapeHtml([channel.number, channel.name, channel.group, programme.title, programme.next].join(" ").toLowerCase())}">
        <div class="remote-station">
          <div class="remote-number">${escapeHtml(channel.number)}</div>
          ${logoMarkup(channel)}
          <div class="remote-station-copy">
            <div class="remote-station-name">${escapeHtml(channel.name || "Unnamed channel")}</div>
            <div class="remote-station-group">${escapeHtml(channel.group || "Enabled channel")}</div>
          </div>
        </div>
        <div class="remote-programme">
          <div class="remote-programme-title">${escapeHtml(programme.title)}</div>
          <div class="remote-programme-meta">${escapeHtml(programme.meta)}</div>
          ${programme.next ? `<div class="remote-next">${escapeHtml(programme.next)}</div>` : ""}
        </div>
        ${button}
      </article>`;
  }

  function sportsTeam(team = {}) {
    const rank = Number(team.ap_rank || 0);
    const rankMarkup = rank > 0 && rank <= 25 ? `<span class="remote-ap-rank" title="AP Top 25">AP #${rank}</span>` : "";
    const logo = team.logo
      ? `<img class="remote-team-logo" src="${escapeHtml(team.logo)}" alt="" loading="lazy" onerror="this.hidden=true">`
      : "";
    return `
      <div class="remote-sports-team">
        ${logo}
        <div class="remote-sports-team-copy">
          <div class="remote-sports-team-name">${rankMarkup}${escapeHtml(team.name || team.abbr || "Team")}</div>
          ${team.record ? `<div class="remote-sports-record">${escapeHtml(team.record)}</div>` : ""}
        </div>
        <div class="remote-sports-score">${escapeHtml(team.score ?? "-")}</div>
      </div>`;
  }

  function sportsCard(game, { current = false } = {}) {
    const isBusy = busyChannel === game.id;
    const isUpcoming = game.phase === "upcoming";
    const isFinal = game.phase === "final";
    const isLive = game.phase === "live";
    const playable = Boolean(game.selectable) && !isUpcoming && !isFinal;
    const id = escapeHtml(game.id);
    const start = formatClock(game.start);
    const action = current
      ? '<button class="btn btn-danger btn-sm remote-action" type="button" data-stop>Stop</button>'
      : `<button class="btn ${playable ? "btn-success" : "btn-outline-danger"} btn-sm remote-action" type="button" ${playable ? `data-channel-id="${id}"` : "disabled"} ${isBusy ? "disabled" : ""}>${isBusy ? "Warming…" : (isUpcoming ? (start || "Upcoming") : (isFinal ? "Final" : (playable ? "Play" : "Unavailable")))}</button>`;
    const cardAction = !current && playable ? ` data-channel-id="${id}"` : "";
    const classes = [
      "remote-channel-card",
      "remote-sports-card",
      isUpcoming ? "is-upcoming" : "",
      isFinal ? "is-final" : "",
      isLive ? "is-live" : "",
      game.top_25 ? "has-top-25" : "",
    ].filter(Boolean).join(" ");
    const searchable = [
      game.sport_label, game.title, game.feed, game.network, game.status,
      game.last_play, game.away?.name, game.home?.name,
    ].join(" ").toLowerCase();
    return `
      <article class="${classes}"${cardAction} data-searchable="${escapeHtml(searchable)}">
        <div class="remote-sports-heading">
          ${game.logo ? `<img class="remote-event-logo" src="${escapeHtml(game.logo)}" alt="" loading="lazy" onerror="this.hidden=true">` : ""}
          <span class="remote-sport-label">${escapeHtml(game.sport_label || "Sports")}</span>
          ${isLive ? '<span class="remote-live-badge"><span aria-hidden="true"></span>Live</span>' : ""}
          ${game.top_25 ? '<span class="remote-top-25-badge">AP Top 25</span>' : ""}
          <span class="remote-sports-network">${escapeHtml(game.network || game.feed || "")}</span>
        </div>
        <div class="remote-matchup">
          ${sportsTeam(game.away)}
          ${sportsTeam(game.home)}
        </div>
        <div class="remote-game-state">
          <div class="remote-game-status">${escapeHtml(game.status || (isUpcoming ? `Upcoming${start ? ` · ${start}` : ""}` : "Live"))}</div>
          ${game.last_play ? `<div class="remote-last-play">${escapeHtml(game.last_play)}</div>` : ""}
          ${game.feed ? `<div class="remote-game-feed">${escapeHtml(game.feed)}</div>` : ""}
        </div>
        ${action}
      </article>`;
  }

  function sportsOrder(left, right) {
    const phase = { live: 0, upcoming: 1, final: 2 };
    const phaseDifference = (phase[left.phase] ?? 3) - (phase[right.phase] ?? 3);
    if (phaseDifference) return phaseDifference;
    if (Boolean(left.top_25) !== Boolean(right.top_25)) return left.top_25 ? -1 : 1;
    if (left.phase === "live") {
      const leftMargin = Number.isFinite(Number(left.score_margin)) ? Number(left.score_margin) : Number.MAX_SAFE_INTEGER;
      const rightMargin = Number.isFinite(Number(right.score_margin)) ? Number(right.score_margin) : Number.MAX_SAFE_INTEGER;
      if (leftMargin !== rightMargin) return leftMargin - rightMargin;
    }
    return String(left.start || "").localeCompare(String(right.start || ""));
  }

  function renderSportsFilters(games) {
    if (mode !== "sports") {
      elements.sportsFilters.classList.add("d-none");
      return;
    }
    const groups = [...new Map(games.map(game => [game.sport, game.sport_label])).entries()];
    if (selectedSport !== "all" && !groups.some(([id]) => id === selectedSport)) selectedSport = "all";
    elements.sportsFilters.innerHTML = [
      ["all", "All"],
      ...groups,
    ].map(([id, label]) => `<button type="button" class="btn btn-sm ${selectedSport === id ? "btn-primary" : "btn-outline-light"}" data-sport="${escapeHtml(id)}">${escapeHtml(label)}</button>`).join("");
    elements.sportsFilters.classList.remove("d-none");
  }

  function render() {
    elements.modes.forEach(button => button.classList.toggle("active", button.dataset.remoteMode === mode));
    const selectedGame = state.sports.find(game => game.id === state.selected_id) || null;
    const selectedChannel = state.channels.find(channel => channel.id === state.selected_id) || null;
    const selected = selectedGame || selectedChannel || state.selected || null;
    elements.current.classList.toggle("is-idle", !selected);
    elements.current.innerHTML = selected
      ? `<div class="remote-current-label">Playing on channel 0.2</div>${selectedGame ? sportsCard(selectedGame, { current: true }) : channelCard(selected, { current: true })}`
      : '<div class="remote-current-label">Channel 0.2 is idle</div><div class="remote-idle-card">Choose a channel below. The television can stay tuned to 0.2.</div>';

    const query = String(elements.search.value || "").trim().toLowerCase();
    let visible;
    if (mode === "sports") {
      const games = [...state.sports].sort(sportsOrder);
      renderSportsFilters(games);
      visible = games.filter(game => {
        if (game.id === state.selected_id) return false;
        if (selectedSport !== "all" && game.sport !== selectedSport) return false;
        return !query || [game.sport_label, game.title, game.feed, game.network, game.status, game.last_play, game.away?.name, game.home?.name]
          .join(" ").toLowerCase().includes(query);
      });
      elements.channels.innerHTML = visible.map(game => sportsCard(game)).join("");
      elements.listLabel.textContent = "Games";
      elements.heading.textContent = "Live and upcoming games";
      elements.search.placeholder = "Team, sport, network, game state…";
      elements.count.textContent = `(${visible.length} shown · ${state.sports.length} games)`;
    } else {
      renderSportsFilters([]);
      visible = state.channels.filter(channel => {
        if (channel.id === state.selected_id) return false;
        const programme = programmeText(channel);
        return !query || [channel.number, channel.name, channel.group, programme.title, programme.next]
          .join(" ").toLowerCase().includes(query);
      });
      elements.channels.innerHTML = visible.map(channel => channelCard(channel)).join("");
      elements.listLabel.textContent = "Channels";
      elements.heading.textContent = "Available channels";
      elements.search.placeholder = "Channel or current show…";
      elements.count.textContent = `(${visible.length} shown · ${state.channels.length} enabled)`;
    }
    elements.empty.classList.toggle("d-none", visible.length > 0);

    if (!busyChannel) {
      if (state.last_error) {
        setStatus(state.last_error, true);
      } else if (selected) {
        setStatus(`${selected.name} is streaming directly on 0.2`);
      } else {
        setStatus("Channel 0.2 is ready. Choose something to watch.");
      }
    }
  }

  function setStatus(message, error = false) {
    elements.status.textContent = message;
    elements.status.classList.toggle("is-error", error);
  }

  function pushSupported() {
    return window.isSecureContext
      && "serviceWorker" in navigator
      && "PushManager" in window
      && "Notification" in window;
  }

  function applicationServerKey(value) {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const decoded = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(decoded, character => character.charCodeAt(0));
  }

  async function alertRequest(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Alert request failed (${response.status})`);
    return payload;
  }

  function renderAlertControls() {
    if (!pushSupported()) {
      elements.alerts.textContent = window.isSecureContext ? "Alerts unavailable" : "HTTPS required";
      elements.alerts.disabled = true;
      elements.disableAlerts.classList.add("d-none");
      return;
    }
    elements.alerts.disabled = alertBusy;
    elements.alerts.textContent = alertBusy
      ? "Working…"
      : (alertSubscription ? "Test alert" : "Enable alerts");
    elements.alerts.classList.toggle("is-enabled", Boolean(alertSubscription));
    elements.disableAlerts.classList.toggle("d-none", !alertSubscription);
  }

  async function initializeAlerts() {
    renderAlertControls();
    if (!pushSupported()) return;
    try {
      alertRegistration = await navigator.serviceWorker.register("/remote-sw.js", {
        scope: "/",
        updateViaCache: "none",
      });
      await navigator.serviceWorker.ready;
      alertSubscription = await alertRegistration.pushManager.getSubscription();
      if (alertSubscription) {
        await alertRequest("/api/remote/alerts/subscribe", {
          method: "POST",
          body: JSON.stringify({ subscription: alertSubscription.toJSON() }),
        });
      }
    } catch (error) {
      console.warn("Could not initialize phone alerts", error);
    }
    renderAlertControls();
  }

  function testMovieChannel() {
    return state.channels.find(channel => /AMC.*WEST/i.test(channel.name || ""))
      || state.channels.find(channel => /AMC|HBO/i.test(channel.name || ""))
      || state.channels[0]
      || null;
  }

  async function enableOrTestAlerts() {
    if (alertBusy || !pushSupported()) return;
    alertBusy = true;
    renderAlertControls();
    try {
      if (!alertRegistration) {
        alertRegistration = await navigator.serviceWorker.register("/remote-sw.js", {
          scope: "/",
          updateViaCache: "none",
        });
      }
      const config = await alertRequest("/api/remote/alerts");
      if (!alertSubscription) {
        const permission = Notification.permission === "default"
          ? await Notification.requestPermission()
          : Notification.permission;
        if (permission !== "granted") {
          throw new Error("Phone alerts were not allowed.");
        }
        alertSubscription = await alertRegistration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey(config.public_key),
        });
        await alertRequest("/api/remote/alerts/subscribe", {
          method: "POST",
          body: JSON.stringify({ subscription: alertSubscription.toJSON() }),
        });
      }
      const movieChannel = testMovieChannel();
      const result = await alertRequest("/api/remote/alerts/test", {
        method: "POST",
        body: JSON.stringify({
          endpoint: alertSubscription.endpoint,
          channel_id: movieChannel?.id || "",
        }),
      });
      setStatus(`Alert sent. “Play on 0.2” will switch to ${result.channel?.name || "the movie channel"}.`);
    } catch (error) {
      setStatus(error.message || "Could not enable phone alerts.", true);
    } finally {
      alertBusy = false;
      renderAlertControls();
    }
  }

  async function disableAlerts() {
    if (alertBusy || !alertSubscription) return;
    alertBusy = true;
    renderAlertControls();
    const subscription = alertSubscription;
    try {
      await alertRequest("/api/remote/alerts/subscribe", {
        method: "DELETE",
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
      alertSubscription = null;
      setStatus("Phone alerts are off.");
    } catch (error) {
      setStatus(error.message || "Could not turn off phone alerts.", true);
    } finally {
      alertBusy = false;
      renderAlertControls();
    }
  }

  async function requestState(options = {}) {
    const response = await fetch(`/api/remote?mode=${encodeURIComponent(mode)}`, {
      cache: "no-store",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Remote request failed (${response.status})`);
    state = payload;
    return payload;
  }

  async function refresh({ quiet = false } = {}) {
    try {
      await requestState();
      render();
    } catch (error) {
      if (!quiet) setStatus(error.message || "Could not load the remote.", true);
    }
  }

  async function selectChannel(channelId) {
    if (busyChannel) return;
    const channel = [...state.channels, ...state.sports].find(item => item.id === channelId);
    busyChannel = channelId;
    setStatus(`Warming ${channel?.name || "channel"} before the handoff…`);
    render();
    try {
      await requestState({ method: "POST", body: JSON.stringify({ channel_id: channelId }) });
      busyChannel = "";
      render();
    } catch (error) {
      busyChannel = "";
      setStatus(error.message || "The channel switch failed.", true);
      render();
    }
  }

  async function stopChannel() {
    if (busyChannel) return;
    busyChannel = "__stop__";
    setStatus("Returning channel 0.2 to its idle screen…");
    try {
      await requestState({ method: "DELETE" });
      busyChannel = "";
      render();
    } catch (error) {
      busyChannel = "";
      setStatus(error.message || "Could not stop channel 0.2.", true);
    }
  }

  elements.guide.addEventListener("click", () => window.location.replace("/guide"));
  elements.alerts.addEventListener("click", enableOrTestAlerts);
  elements.disableAlerts.addEventListener("click", disableAlerts);
  elements.search.addEventListener("input", render);
  elements.modes.forEach(button => button.addEventListener("click", () => {
    const nextMode = button.dataset.remoteMode === "sports" ? "sports" : "tv";
    if (mode === nextMode) return;
    mode = nextMode;
    localStorage.setItem("m3u-remote-mode", mode);
    elements.search.value = "";
    render();
    refresh({ quiet: true });
    if (mode === "sports") {
      window.setTimeout(() => refresh({ quiet: true }), 3000);
    }
  }));
  elements.sportsFilters.addEventListener("click", event => {
    const button = event.target.closest("[data-sport]");
    if (!button) return;
    selectedSport = button.dataset.sport || "all";
    localStorage.setItem("m3u-remote-sport", selectedSport);
    render();
  });
  elements.channels.addEventListener("click", event => {
    const button = event.target.closest("[data-channel-id]");
    if (button) selectChannel(button.dataset.channelId);
  });
  elements.current.addEventListener("click", event => {
    if (event.target.closest("[data-stop]")) stopChannel();
  });

  refresh();
  initializeAlerts();
  window.setInterval(() => {
    if (!busyChannel && document.visibilityState === "visible") refresh({ quiet: true });
  }, 5000);
})();
