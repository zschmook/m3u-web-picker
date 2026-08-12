(() => {
  "use strict";

  const status = document.getElementById("masterUpdateStatus");
  if (!status) return;

  let rewriting = false;

  function renderThreeLines() {
    if (rewriting) return;
    const text = String(status.textContent || "").trim();
    if (!text.startsWith("Next:")) return;

    const parts = text.split(" • ").map(part => part.trim()).filter(Boolean);
    if (parts.length < 3) return;

    const next = parts.find(part => part.startsWith("Next:")) || "";
    const last = parts.find(part => part.startsWith("Last:")) || "";
    const took = parts.find(part => part.startsWith("Took ")) || "";
    const timezone = parts.find(part => part && part !== next && part !== last && part !== took) || "";

    if (!next || !last) return;

    rewriting = true;
    status.classList.add("ui-overview-update-lines");
    status.replaceChildren();

    const nextLine = document.createElement("span");
    nextLine.className = "ui-overview-update-line";
    nextLine.textContent = next;

    const lastLine = document.createElement("span");
    lastLine.className = "ui-overview-update-line";
    lastLine.textContent = took ? `${last} · ${took}` : last;

    const timezoneLine = document.createElement("span");
    timezoneLine.className = "ui-overview-update-line";
    timezoneLine.textContent = timezone ? `Timezone: ${timezone}` : "Timezone: —";

    status.append(nextLine, lastLine, timezoneLine);
    rewriting = false;
  }

  const observer = new MutationObserver(() => renderThreeLines());
  observer.observe(status, {childList: true, characterData: true, subtree: true});
  renderThreeLines();
})();
