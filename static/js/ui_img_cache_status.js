(() => {
  "use strict";

  let timer = null;

  function formatNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString() : "0";
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

  async function refresh() {
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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
