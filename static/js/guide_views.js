(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const tabs = [el("guideChannelsTab"), el("guideSportsTab")];
  const panel = el("guideSportsPanel");
  const map = el("sports-app");
  if (!panel || !map || tabs.some(tab => !tab)) return;
  let view = "guide";
  let dvrOpen = el("guideDvrBtn")?.getAttribute("aria-pressed") === "true";

  function select(next, {focus = false} = {}) {
    if (next === "sports" && dvrOpen) el("guideDvrCloseBtn")?.click();
    view = next === "sports" ? "sports" : "guide";
    const sports = view === "sports";
    panel.hidden = !sports;
    for (const id of ["guideBrowseControls", "guideBrowseList"]) {
      el(id).hidden = sports || dvrOpen;
    }
    tabs.forEach((tab, index) => {
      const selected = index === (sports ? 1 : 0);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    if (sports) window.GuideSportsMap?.show({focus});
    else window.GuideSportsMap?.hide();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(index ? "sports" : "guide"));
    tab.addEventListener("keydown", event => {
      let next;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") next = 1-index;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = 1;
      else if (event.key === "ArrowDown" && index === 1) {
        event.preventDefault();
        select("sports", {focus: true});
        return;
      } else return;
      event.preventDefault();
      select(next ? "sports" : "guide");
      tabs[next].focus();
    });
  });

  window.addEventListener("m3u-guide-dvr-panel", event => {
    dvrOpen = Boolean(event.detail?.open);
    select(dvrOpen ? "guide" : view);
  });
  // The guide's normal Refresh handlers remain responsible for its channel list.
  document.addEventListener("click", event => {
    if (view !== "sports" || !event.target.closest("#guideRefreshBtn")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void window.GuideSportsMap?.refresh();
  }, true);

  map.addEventListener("picker:watch-channel", async event => {
    const {feed, event: game} = event.detail || {};
    const identity = String(feed?.id || "");
    if (view !== "sports" || feed?.available === false
        || !/^m3u-picker-sports-[a-f0-9]{1,64}$/.test(identity)) return;
    const message = el("feed-message");
    const programme = feed.programmes?.[0];
    const channel = {
      number: feed.number,
      name: `${game.title || game.name} — ${feed.name}`,
      group: `${game.league} · Sports`,
      generated: true,
      tvg_id: identity,
      generated_id: identity,
      // Never hand the player a reusable channel number from a cached map.
      play_url: `/guide/play/sports/id/${identity}`,
      now: programme?.start > 0 && programme?.stop > 0 ? {
        ...programme,
        start: new Date(programme.start * 1000).toISOString(),
        stop: new Date(programme.stop * 1000).toISOString(),
      } : null,
      upcoming: [],
    };
    message.textContent = "Opening feed…";
    try {
      await playChannel(channel);
      message.textContent = "Playback opened in the guide player.";
    } catch (error) {
      message.textContent = error.message || "This feed could not start. Try another feed or refresh the map.";
    }
  });
  window.GuideBrowseViews = {select, current: () => view};
  select("guide");
})();
