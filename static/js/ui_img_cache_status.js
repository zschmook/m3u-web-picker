(() => {
  "use strict";

  let timer = null;
  let elapsedTimer = null;
  let updateButtonObserver = null;
  let elapsedBaseSeconds = null;
  let elapsedBaseAt = 0;

  function formatNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString() : "0";
  }

  function formatElapsed(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(value / 60);
    const remainder = value % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  function installRow() {
    if (document.getElementById("uiImgCache")) return true;
    const sportsValue = document.getElementById("uiSportsChannels");
    const metrics = sportsValue?.closest(".ui-system-metrics");
    const sportsRow = sportsValue?.closest("div");
    if (!metrics || !sportsRow) return false;

    const row = document.createElement("div");
    row.innerHTML = '<span>IMG Cache</span><strong id="uiImgCache">—</strong>';
    sportsRow.insertAdjacentElement("afterend", row);
    return true;
  }

  function parsedElapsed(text) {
    const match = String(text || "").match(/Updating\s*·\s*(\d+):(\d{2})/i);
    if (!match) return null;
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function currentElapsed() {
    if (elapsedBaseSeconds == null) return null;
    return elapsedBaseSeconds + Math.floor((Date.now() - elapsedBaseAt) / 1000);
  }

  function syncElapsedFromButton() {
    const button = document.getElementById("uiUpdateNowBtn");
    if (!button) return false;

    const parsed = parsedElapsed(button.textContent);
    if (parsed != null) {
      const local = currentElapsed();
      // Treat backend status as authoritative when it moves forward, but never
      // let a delayed/stalled status response drag the visible clock backward.
      if (local == null || parsed > local) {
        elapsedBaseSeconds = parsed;
        elapsedBaseAt = Date.now();
      }
      return true;
    }

    if (!String(button.textContent || "").startsWith("Updating")) {
      elapsedBaseSeconds = null;
      elapsedBaseAt = 0;
    }
    return true;
  }

  function installElapsedCounter() {
    const button = document.getElementById("uiUpdateNowBtn");
    if (!button) return false;

    if (!updateButtonObserver) {
      updateButtonObserver = new MutationObserver(syncElapsedFromButton);
      updateButtonObserver.observe(button, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    syncElapsedFromButton();
    return true;
  }

  function tickElapsedCounter() {
    if (!installElapsedCounter()) return;
    const button = document.getElementById("uiUpdateNowBtn");
    const elapsed = currentElapsed();
    if (!button || elapsed == null || !button.disabled) return;
    button.textContent = `Updating · ${formatElapsed(elapsed)}`;
  }

  async function refresh() {
    installElapsedCounter();
    if (!installRow()) return;
    try {
      const response = await fetch("/api/logo-cache/status", {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const value = document.getElementById("uiImgCache");
      if (value) value.textContent = formatNumber(data.images);
    } catch {
      const value = document.getElementById("uiImgCache");
      if (value) value.textContent = "—";
    }
  }

  function start() {
    refresh();
    if (timer) clearInterval(timer);
    timer = window.setInterval(refresh, 10000);
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = window.setInterval(tickElapsedCounter, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
