(() => {
  const root = document.getElementById("multiviewDirector");
  if (!root) return;
  const message = document.getElementById("directorMessage");
  const ticker = document.getElementById("scoreTicker");
  let state = null;
  let draggedId = "";

  const slotName = (index) => index === 0 ? "PRIMARY · SUBSCRIBED" : `${index}${index === 1 ? "ST" : index === 2 ? "ND" : "RD"}`;
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

  async function patch(payload) {
    message.textContent = "Applying layout…";
    const response = await fetch("/api/sports/multiview", {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not update multiview");
    state = data;
    render();
    message.textContent = "Layout applied. The stable Jellyfin channel will rebuild with this selection.";
  }

  function renderPane(pane, game, index) {
    pane.innerHTML = `
      <div class="mv-pane-head"><span>${slotName(index)}</span><span>Weight ${game.weight}</span></div>
      <div class="mv-game"><div class="mv-matchup">${escapeHtml(game.score_text)}</div><div class="mv-game-meta">${escapeHtml(game.status_text)} · ${escapeHtml(game.away)} @ ${escapeHtml(game.home)}</div></div>
      <div class="mv-pane-actions"><button class="mv-audio ${state.audio_slot === index ? "mv-on" : ""}" type="button">${state.audio_slot === index ? "🔊 Audio" : "Audio"}</button><button class="mv-lock ${state.locked[index] ? "mv-locked" : ""}" type="button">${state.locked[index] ? "🔒 Locked" : "Lock"}</button></div>`;
    pane.querySelector(".mv-audio").addEventListener("click", () => patch({audio_slot:index}).catch(showError));
    pane.querySelector(".mv-lock").addEventListener("click", () => {
      const locked = [...state.locked]; locked[index] = !locked[index]; patch({locked}).catch(showError);
    });
  }

  function renderTicker() {
    ticker.innerHTML = "";
    state.ticker.forEach(game => {
      const item = document.createElement("article");
      item.className = `mv-score${game.upset_alert ? " mv-upset" : ""}`;
      item.draggable = true;
      item.dataset.gameId = game.id;
      item.innerHTML = `<div>${game.upset_alert ? '<span class="mv-alert-label">UPSET ALERT</span>' : ""}<strong>${escapeHtml(game.score_text)}</strong><small>${escapeHtml(game.status_text)}</small></div><div class="mv-score-actions"><button class="mv-open" type="button">Open</button><button class="mv-upset-toggle" type="button">${game.upset_alert ? "Clear alert" : "Test upset"}</button></div>`;
      item.addEventListener("dragstart", () => { draggedId = game.id; });
      item.querySelector(".mv-open").addEventListener("click", () => window.open(`/sports/multiview/game/${encodeURIComponent(game.id)}`, `multiview-${game.id}`, "popup,width=1100,height=700"));
      item.querySelector(".mv-upset-toggle").addEventListener("click", () => {
        const upsetIds = state.ticker.filter(candidate => candidate.upset_alert).map(candidate => candidate.id);
        const next = game.upset_alert ? upsetIds.filter(id => id !== game.id) : [...upsetIds, game.id];
        patch({upset_ids:next}).catch(showError);
      });
      ticker.appendChild(item);
    });
  }

  function render() {
    root.querySelectorAll(".mv-pane").forEach((pane, index) => renderPane(pane, state.slots[index], index));
    renderTicker();
    document.getElementById("connectionStatus").textContent = `${state.connections.visible} visible · ${state.connections.spare} spare${state.active ? " · output active" : ""}`;
  }

  function showError(error) { message.textContent = error.message || String(error); }

  root.querySelectorAll(".mv-pane").forEach(pane => {
    pane.addEventListener("dragover", event => { event.preventDefault(); pane.classList.add("mv-dragover"); });
    pane.addEventListener("dragleave", () => pane.classList.remove("mv-dragover"));
    pane.addEventListener("drop", event => {
      event.preventDefault(); pane.classList.remove("mv-dragover");
      const index = Number(pane.dataset.slot);
      if (!draggedId || state.locked[index]) { message.textContent = state.locked[index] ? "That slot is locked." : "No game selected."; return; }
      const slots = state.slots.map(game => game.id);
      slots[index] = draggedId;
      patch({slots}).catch(showError);
    });
  });

  document.getElementById("resetDirector").addEventListener("click", async () => {
    try {
      const response = await fetch("/api/sports/multiview", {method:"POST"});
      state = await response.json(); render(); message.textContent = "Automatic Week 5 layout restored.";
    } catch (error) { showError(error); }
  });

  fetch("/api/sports/multiview").then(response => response.json()).then(data => { state = data; render(); }).catch(showError);
})();
