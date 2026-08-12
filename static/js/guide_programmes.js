(() => {
  "use strict";

  const GUIDE_WINDOW_HOURS = 8;
  const GUIDE_SLOT_MINUTES = 30;
  const GUIDE_PX_PER_MINUTE = 5;
  let timelineRoot = null;

  function formatGuideClock(value) {
    if (!value) return "";
    const parsed = value instanceof Date ? value : new Date(value);
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
    const upcoming = Array.isArray(channel.upcoming) ? channel.upcoming : [];
    const programmeBits = programme => [
      programme?.title,
      programme?.subtitle,
      programme?.description,
      ...(Array.isArray(programme?.categories) ? programme.categories : []),
    ];
    return [
      channel.number,
      channel.name,
      channel.group,
      channel.subtitle,
      ...programmeBits(now),
      ...programmeBits(next),
      ...upcoming.flatMap(programmeBits),
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

  function floorToGuideSlot(date) {
    const value = new Date(date);
    value.setSeconds(0, 0);
    value.setMinutes(
      Math.floor(value.getMinutes() / GUIDE_SLOT_MINUTES) * GUIDE_SLOT_MINUTES
    );
    return value;
  }

  function timelineBounds() {
    const now = new Date();
    const start = floorToGuideSlot(now);
    const end = new Date(start.getTime() + GUIDE_WINDOW_HOURS * 60 * 60 * 1000);
    const totalMinutes = (end.getTime() - start.getTime()) / 60000;
    return {
      now,
      start,
      end,
      totalMinutes,
      width: totalMinutes * GUIDE_PX_PER_MINUTE,
      slotWidth: GUIDE_SLOT_MINUTES * GUIDE_PX_PER_MINUTE,
    };
  }

  function ensureTimelineShell() {
    if (timelineRoot?.isConnected) return timelineRoot;
    const wrap = document.querySelector(".guide-list-wrap");
    const table = wrap?.querySelector(".guide-table");
    if (!wrap || !table) return null;

    table.classList.add("d-none");
    wrap.classList.add("guide-timeline-wrap");
    timelineRoot = document.createElement("div");
    timelineRoot.id = "guideTimeline";
    timelineRoot.className = "guide-timeline";
    wrap.insertBefore(timelineRoot, guideEls.empty);

    timelineRoot.addEventListener("click", event => {
      const target = event.target.closest("[data-guide-play-url]");
      if (!target) return;
      event.preventDefault();
      const channel = actualGuideChannel(target.dataset.guidePlayUrl);
      if (channel) playChannel(channel);
    });
    return timelineRoot;
  }

  function channelProgrammes(channel) {
    const source = [
      channel.now,
      ...(Array.isArray(channel.upcoming) ? channel.upcoming : []),
    ].filter(Boolean);
    const seen = new Set();
    return source
      .filter(programme => {
        const key = `${programme.start || ""}|${programme.stop || ""}|${programme.title || ""}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .sort((left, right) => new Date(left.start || 0) - new Date(right.start || 0));
  }

  function programmeGeometry(programme, bounds, index, programmes) {
    const rawStart = new Date(programme.start || "");
    let rawStop = new Date(programme.stop || "");
    if (Number.isNaN(rawStart.getTime())) return null;
    if (Number.isNaN(rawStop.getTime()) || rawStop <= rawStart) {
      const nextStart = new Date(programmes[index + 1]?.start || "");
      rawStop = !Number.isNaN(nextStart.getTime()) && nextStart > rawStart
        ? nextStart
        : new Date(rawStart.getTime() + GUIDE_SLOT_MINUTES * 60 * 1000);
    }
    if (rawStop <= bounds.start || rawStart >= bounds.end) return null;

    const clippedStart = rawStart < bounds.start ? bounds.start : rawStart;
    const clippedStop = rawStop > bounds.end ? bounds.end : rawStop;
    const leftMinutes = (clippedStart.getTime() - bounds.start.getTime()) / 60000;
    const durationMinutes = Math.max(
      5,
      (clippedStop.getTime() - clippedStart.getTime()) / 60000
    );
    return {
      left: leftMinutes * GUIDE_PX_PER_MINUTE,
      width: durationMinutes * GUIDE_PX_PER_MINUTE,
      current: bounds.now >= rawStart && bounds.now < rawStop,
      rawStart,
      rawStop,
    };
  }

  function renderProgrammeBlocks(channel, bounds) {
    const programmes = channelProgrammes(channel);
    const blocks = programmes.map((programme, index) => {
      const geometry = programmeGeometry(programme, bounds, index, programmes);
      if (!geometry) return "";
      const time = formatProgrammeRange(programme);
      const subtitle = String(programme.subtitle || "").trim();
      const description = String(programme.description || "").trim();
      const tooltip = [programme.title, time, subtitle, description]
        .filter(Boolean)
        .join(" — ");
      return `<button type="button"
        class="guide-programme-block${geometry.current ? " is-current" : ""}"
        style="left:${geometry.left}px;width:${geometry.width}px"
        data-guide-play-url="${escapeHtml(channel.play_url)}"
        title="${escapeHtml(tooltip)}">
          <span class="guide-programme-block-title">${escapeHtml(programme.title || "Untitled")}</span>
          ${time ? `<span class="guide-programme-block-time">${escapeHtml(time)}</span>` : ""}
          ${subtitle ? `<span class="guide-programme-block-subtitle">${escapeHtml(subtitle)}</span>` : ""}
      </button>`;
    }).join("");

    if (blocks) return blocks;
    return '<div class="guide-track-empty">No guide data</div>';
  }

  function renderTimeHeader(bounds) {
    const slots = [];
    for (
      let slot = new Date(bounds.start), index = 0;
      slot < bounds.end;
      slot = new Date(slot.getTime() + GUIDE_SLOT_MINUTES * 60 * 1000), index += 1
    ) {
      slots.push(
        `<div class="guide-time-slot" style="left:${index * bounds.slotWidth}px;width:${bounds.slotWidth}px">${escapeHtml(formatGuideClock(slot))}</div>`
      );
    }
    const nowLeft = (bounds.now.getTime() - bounds.start.getTime()) / 60000 * GUIDE_PX_PER_MINUTE;
    const nowMarker = nowLeft >= 0 && nowLeft <= bounds.width
      ? `<div class="guide-now-marker" style="left:${nowLeft}px" aria-hidden="true"></div>`
      : "";
    return `<div class="guide-timeline-head">
      <div class="guide-station-head">Channel</div>
      <div class="guide-time-head" style="width:${bounds.width}px">
        ${slots.join("")}
        ${nowMarker}
      </div>
    </div>`;
  }

  function renderChannelRow(channel, bounds) {
    const logo = channel.logo
      ? `<img class="guide-logo" src="${escapeHtml(channel.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : "";
    const generated = channel.generated
      ? '<span class="badge text-bg-primary guide-generated-badge">Auto</span>'
      : "";
    const isPlaying = guideState.currentChannel?.play_url === channel.play_url;
    const nowLeft = (bounds.now.getTime() - bounds.start.getTime()) / 60000 * GUIDE_PX_PER_MINUTE;
    const nowMarker = nowLeft >= 0 && nowLeft <= bounds.width
      ? `<div class="guide-now-marker" style="left:${nowLeft}px" aria-hidden="true"></div>`
      : "";

    return `<div class="guide-grid-row${isPlaying ? " guide-current-row" : ""}">
      <div class="guide-station-cell">
        <div class="guide-station-number">${escapeHtml(channel.number)}</div>
        ${logo}
        <div class="guide-station-copy">
          <div class="guide-station-name">${escapeHtml(channel.name)}${generated}</div>
          <div class="guide-station-group">${escapeHtml(channel.group || "")}</div>
        </div>
        <button type="button" class="btn ${isPlaying ? "btn-outline-light" : "btn-success"} btn-sm guide-station-play"
          data-guide-play-url="${escapeHtml(channel.play_url)}">${isPlaying ? "Playing" : "Play"}</button>
      </div>
      <div class="guide-programme-track" style="width:${bounds.width}px;--guide-slot-width:${bounds.slotWidth}px">
        ${renderProgrammeBlocks(channel, bounds)}
        ${nowMarker}
      </div>
    </div>`;
  }

  filteredGuideChannels = function() {
    const query = guideEls.search.value.trim().toLowerCase();
    if (!query) return guideState.channels;
    return guideState.channels.filter(channel => programmeSearchText(channel).includes(query));
  };

  renderGuide = function() {
    const root = ensureTimelineShell();
    if (!root) return;
    const visible = filteredGuideChannels();
    const bounds = timelineBounds();
    root.style.setProperty("--guide-timeline-width", `${bounds.width}px`);
    root.innerHTML = `${renderTimeHeader(bounds)}<div class="guide-timeline-body">${visible.map(channel => renderChannelRow(channel, bounds)).join("")}</div>`;

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
    const programmes = Number(epg.programme_count || 0);
    guideEls.status.textContent = `${count.toLocaleString()} served • ${matched.toLocaleString()} with guide data • ${current.toLocaleString()} on now • ${programmes.toLocaleString()} shows loaded`;
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

  guideEls.search.addEventListener("input", event => {
    event.stopImmediatePropagation();
    renderGuide();
  }, true);
  const searchLabel = document.querySelector('label[for="guideSearch"]');
  if (searchLabel) searchLabel.textContent = "Search guide";
  guideEls.search.placeholder = "Channel, show, group, sports event…";

  document.getElementById("guideRefreshBtn")?.addEventListener("click", event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    loadGuide();
  }, true);

  window.setInterval(() => loadGuide({silent: true}), 60_000);
  ensureTimelineShell();
  loadGuide();
})();
