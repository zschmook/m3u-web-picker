(() => {
  "use strict";

  function install() {
    const grid = document.querySelector("#uiPage-overview .ui-overview-grid");
    if (!grid || document.getElementById("uiPhilliesAlertTestBtn")) return false;

    const card = document.createElement("section");
    card.className = "ui-modern-card";
    card.innerHTML = `
      <div class="ui-card-heading">
        <div>
          <span>Phillies Alert Test</span>
          <small>Grab the current Phillies score and force the Phanatic animation onto channel 3.</small>
        </div>
      </div>
      <button class="btn ui-btn-primary" id="uiPhilliesAlertTestBtn" type="button">Show Phillies Score</button>
      <div class="small-muted mt-2" id="uiPhilliesAlertTestStatus">Tune channel 3 first, then hit the button whenever you want.</div>`;
    grid.appendChild(card);

    const button = document.getElementById("uiPhilliesAlertTestBtn");
    const status = document.getElementById("uiPhilliesAlertTestStatus");
    button.addEventListener("click", async () => {
      button.disabled = true;
      status.textContent = "Fetching current Phillies score…";
      try {
        const response = await fetch("/api/sports/mlb-score-alerts/3/phillies-test", {
          method: "POST",
          headers: { "Accept": "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        const away = payload.away || {};
        const home = payload.home || {};
        status.textContent = `${away.abbr || away.name} ${away.score} - ${home.score} ${home.abbr || home.name} • sent to channel ${payload.channel_number}`;
      } catch (error) {
        status.textContent = error?.message || "Could not trigger Phillies alert.";
      } finally {
        button.disabled = false;
      }
    });
    return true;
  }

  if (install()) return;
  let attempts = 0;
  const timer = window.setInterval(() => {
    attempts += 1;
    if (install() || attempts >= 100) window.clearInterval(timer);
  }, 50);
})();
