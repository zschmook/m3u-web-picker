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
          <small>Grab the current Phillies score and force the Phanatic animation onto active generated sports channels.</small>
        </div>
      </div>
      <div class="d-flex gap-2 flex-wrap">
        <button class="btn ui-btn-primary" id="uiPhilliesAlertTestBtn" type="button">Show Phillies Score</button>
        <button class="btn ui-btn-secondary" id="uiRandomAlertTestBtn" type="button">Show Random Team Score</button>
      </div>
      <div class="small-muted mt-2" id="uiPhilliesAlertTestStatus">Tune a generated sports base channel first, then hit the button whenever you want.</div>`;
    grid.appendChild(card);

    const button = document.getElementById("uiPhilliesAlertTestBtn");
    const randomButton = document.getElementById("uiRandomAlertTestBtn");
    const status = document.getElementById("uiPhilliesAlertTestStatus");
    async function trigger(endpoint, loadingText) {
      button.disabled = true;
      randomButton.disabled = true;
      status.textContent = loadingText;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Accept": "application/json" },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        const away = payload.away || {};
        const home = payload.home || {};
        const channels = Array.isArray(payload.active_channels) ? payload.active_channels.join(", ") : "active streams";
        const team = payload.selected_team ? ` • ${payload.selected_team.abbr || payload.selected_team.name} selected` : "";
        status.textContent = `${away.abbr || away.name} ${away.score} - ${home.score} ${home.abbr || home.name}${team} • sent to channels ${channels}`;
      } catch (error) {
        status.textContent = error?.message || "Could not trigger score alert.";
      } finally {
        button.disabled = false;
        randomButton.disabled = false;
      }
    }

    button.addEventListener("click", () => {
      trigger(
        "/api/sports/generated-alerts/phillies-test",
        "Fetching current Phillies score…",
      );
    });
    randomButton.addEventListener("click", () => {
      trigger(
        "/api/sports/generated-alerts/random-test",
        "Fetching a random team’s latest score…",
      );
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
