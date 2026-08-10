(() => {
  "use strict";

  function formatGuideClock(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "";
    return parsed.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
  }

  function formatProgrammeRange(programme) {
    if (!programme) return "";
    const start = formatGuideClock(programme.start);
    const stop = formatGuideClock(programme.stop);
    if (start && stop) return `${start}–${stop}`;
    return start || stop || "";
  }

  function programmeSearchText(channel) {
    const now = channel.now || {};
    const next = channel.next || {};
    return [
      channel.number,
      channel.name,
      channel.group,
      channel.subtitle,
      now.title,
      now.subtitle,
      now.description,
      ...(Array.isArray(now.categories) ? now.categories : []),
      next.title,
      next.subtitle,
      next.description,
      ...(Array.isArray(next.categories) ? next.categories : []),
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function actualGuideChannel(playUrl) {
    const target = String(playUrl || "");
    return guideState.channels.find(channel => String(channel.play_url || "") === target) || null;
  }

  function programmePlayerCopy(channel) {
    if (!channel) return;
    const now = channel.now || null;
    guideEls.playerTitle.textContent = now?.title || channel.name || "Channel";
    const meta = [];
    if (channel.name) meta.push(channel.name);
    const range = formatProgrammeRange(now);
    if (range) meta.push(range);
    if (channel.group) meta.push(channel.group);
    guideEls.playerMeta.textContent = meta.join(" • ");
  }

  function remoteProgrammeCopy(channel) {
    if (!channel) return "";
    const title = String(channel.now?.title || "").trim();
    if (!title) return channel.name || "";
    return `${title} · ${channel.name || "Live TV"}`;
  }

  filteredGuideChannels = function() {
    const query = guideEls.search.value.trim().toLowerCase();
    if (!query) return guideState.channels;
    return guideState.channels.filter(channel => programmeSearchText(channel).includes(query));
  };

  renderGuide = function() {
    const visible = filteredGuideChannels();
    guideEls.rows.innerHTML = visible.map(channel => {
      const logo = channel.logo
        ? `<img class="guide-logo" src="${escapeHtml(channel.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : "";
      const generated = channel.generated
        ? `<span class="badge text-bg-primary guide-generated-badge">Auto</span>`
        : "";
      const now = channel.now || null;
      const next = channel.next || null;
      const nowRange = formatProgrammeRange(now);
      const nextStart = formatGuideClock(next?.start);
      const programme = now
        ? `
          <div class="guide-programme-now">
            <span class="guide-programme-now-badge">Now</span>
            <span class="guide-programme-title">${escapeHtml(now.title || "Untitled")}</span>
            ${nowRange ? `<span class="guide-programme-time">${escapeHtml(nowRange)}</span>` : ""}
          </div>
          ${now.subtitle ? `<div class="guide-programme-subtitle">${escapeHtml(now.subtitle)}</div>` : ""}`
        : next
          ? `<div class="guide-programme-empty">No programme airing now.</div>`
          : `<div class="guide-programme-empty">No guide data for this channel.</div>`;
      const nextLine = next
        ? `<div class="guide-programme-next"><span class="guide-programme-next-label">Next:</span> ${escapeHtml(next.title || "Untitled")}${nextStart ? ` · ${escapeHtml(nextStart)}` : ""}</div>`
        : "";
      const subtitle = channel.subtitle
        ? `<div class="guide-channel-subtitle">${escapeHtml(channel.subtitle)}</div>`
        : "";
      const isCurrent = guideState.currentChannel?.play_url === channel.play_url;
      return `
        <tr class="${isCurrent ? "guide-current-row" : ""}">
          <td>${escapeHtml(channel.number)}</td>
          <td class="guide-channel-cell">
            <div class="guide-channel-main">
              ${logo}
              <div class="guide-channel-copy">
                <div class="guide-channel-identity">
                  <span class="guide-channel-identity-name">${escapeHtml(channel.name)}</span>${generated}
                </div>
                ${programme}
                ${nextLine}
                ${subtitle}
              </div>
            </div>
          </td>
          <td>${escapeHtml(channel.group || "—")}</td>
          <td class="text-end">
            <button class="btn ${isCurrent ? "btn-outline-light" : "btn-success"} btn-sm guide-play-btn" type="button"
              data-play-url="${escapeHtml(channel.play_url)}">${isCurrent ? "Playing" : "Play"}</button>
          </td>
        </tr>`;
    }).join("");

    guideEls.visibleCount.textContent = `${visible.length.toLocaleString()} channel${visible.length === 1 ? "" : "s"}`;
    guideEls.empty.classList.toggle("d-none", visible.length !== 0);
  };

  function renderGuideStatus(data) {
    const count = Number(data?.count ?? guideState.channels.length);
    const epg = data?.epg || {};
    guideEls.status.classList.remove("guide-epg-warning");

    if (!epg.available) {
      guideEls.status.classList.add("guide-epg-warning");
      guideEls.status.textContent = `${count.toLocaleString()} served channels • ${epg.error || "served EPG unavailable"}`;
      return;
    }

    const matched = Number(epg.matched_channels || 0);
    const current = Number(epg.current_channels || 0);
    guideEls.status.textContent = `${count.toLocaleString()} served • ${matched.toLocaleString()} with guide data • ${current.toLocaleString()} on now`;
  }

  loadGuide = async function(options = {}) {
    const silent = options === true || Boolean(options?.silent);
    if (!silent) guideEls.status.textContent = "Loading curated lineup and guide…";
    try {
      const response = await fetch(`/api/guide/channels?_=${Date.now()}`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load curated lineup.");

      const previousPlayUrl = guideState.currentChannel?.play_url || "";
      guideState.channels = Array.isArray(data.channels) ? data.channels : [];
      if (previousPlayUrl) {
        const refreshed = actualGuideChannel(previousPlayUrl);
        if (refreshed) {
          guideState.currentChannel = refreshed;
          programmePlayerCopy(refreshed);
          if (guideState.mode === "cast" || guideState.mode === "roku") {
            guideEls.castScreenChannel.textContent = remoteProgrammeCopy(refreshed);
          }
        }
      }
      renderGuide();
      renderGuideStatus(data);
    } catch (error) {
      if (!silent) {
        guideState.channels = [];
        renderGuide();
      }
      guideEls.status.classList.add("guide-epg-warning");
      guideEls.status.textContent = error.message || String(error);
    }
  };

  const baseSetCurrentChannel = setCurrentChannel;
  setCurrentChannel = function(channel) {
    const canonical = actualGuideChannel(channel?.play_url) || channel;
    baseSetCurrentChannel(canonical);
    programmePlayerCopy(canonical);
  };

  const baseShowCastPlayer = showCastPlayer;
  showCastPlayer = function() {
    baseShowCastPlayer();
    guideEls.castScreenChannel.textContent = remoteProgrammeCopy(guideState.currentChannel);
  };

  const baseShowRokuPlayer = showRokuPlayer;
  showRokuPlayer = function() {
    baseShowRokuPlayer();
    guideEls.castScreenChannel.textContent = remoteProgrammeCopy(guideState.currentChannel);
  };

  // The original guide click handler reconstructs a minimal channel object from
  // button data attributes. Intercept it so playback keeps the Now/Next metadata
  // attached to the canonical channel object.
  guideEls.rows.addEventListener("click", event => {
    const button = event.target.closest(".guide-play-btn");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const channel = actualGuideChannel(button.dataset.playUrl);
    if (channel) playChannel(channel);
  }, true);

  // Replace the old search/render and manual refresh listeners without changing
  // guide.js, which still owns the proven Cast/Roku playback state machines.
  guideEls.search.addEventListener("input", event => {
    event.stopImmediatePropagation();
    renderGuide();
  }, true);

  document.getElementById("guideRefreshBtn")?.addEventListener("click", event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    loadGuide();
  }, true);

  // Programme transitions happen even when epg.xml itself has not changed.
  // Re-select Now/Next once per minute while preserving active playback.
  window.setInterval(() => loadGuide({silent: true}), 60_000);

  // guide.js starts its initial request before this overlay loads. Run one
  // enriched request now so the first paint settles on programme-aware rows.
  loadGuide();
})();
