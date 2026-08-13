(() => {
  "use strict";

  const STORAGE_KEY = "m3u-picker.ui.theme";
  const THEMES = [
    ["midnight", "Midnight"],
    ["slate", "Slate"],
    ["light", "Light"],
    ["ketchup-mustard", "Ketchup & Mustard"]
  ];
  const VALID = new Set(THEMES.map(([value]) => value));

  function selectedTheme() {
    const saved = localStorage.getItem(STORAGE_KEY) || "midnight";
    return VALID.has(saved) ? saved : "midnight";
  }

  function applyTheme(theme) {
    const value = VALID.has(theme) ? theme : "midnight";
    document.body.dataset.uiTheme = value;
    localStorage.setItem(STORAGE_KEY, value);
    const select = document.getElementById("uiThemeSelect");
    if (select && select.value !== value) select.value = value;
  }

  function installThemePicker() {
    if (document.getElementById("uiThemeSelect")) return;
    const host = document.querySelector(".ui-jump-nav") || document.querySelector(".app-brand-meta");
    if (!host) return;

    const label = document.createElement("label");
    label.className = "ui-theme-control";
    label.setAttribute("for", "uiThemeSelect");
    label.innerHTML = `
      <span>Theme</span>
      <span class="ui-theme-select-wrap">
        <select id="uiThemeSelect" class="form-select form-select-sm" aria-label="Theme">
          ${THEMES.map(([value, name]) => `<option value="${value}">${name}</option>`).join("")}
        </select>
        <span class="ui-theme-chevron" aria-hidden="true">▾</span>
      </span>`;
    host.appendChild(label);

    const select = document.getElementById("uiThemeSelect");
    select.value = selectedTheme();
    select.addEventListener("change", () => applyTheme(select.value));
  }

  function placeThemePickerInOverview() {
    const grid = document.querySelector("#uiPage-overview .ui-overview-grid");
    const picker = document.querySelector(".ui-theme-control");
    const outputsButton = document.getElementById("uiOverviewOutputsBtn");
    const outputsCard = outputsButton?.closest(".ui-modern-card");
    if (!grid || !picker || !outputsCard) return false;

    let stack = grid.querySelector(":scope > .ui-overview-right-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "ui-overview-right-stack";
      grid.insertBefore(stack, outputsCard);
      stack.appendChild(outputsCard);
    }

    let themeCard = stack.querySelector(".ui-overview-theme-card");
    if (!themeCard) {
      themeCard = document.createElement("section");
      themeCard.className = "ui-modern-card ui-overview-theme-card";
      themeCard.innerHTML = `
        <div class="ui-card-heading">
          <div>
            <span>Themes</span>
            <small>Choose the appearance used throughout M3U Web Picker.</small>
          </div>
        </div>
        <div id="uiOverviewThemeSlot"></div>`;
      stack.insertBefore(themeCard, outputsCard);
    }

    document.getElementById("uiOverviewThemeSlot")?.appendChild(picker);
    return true;
  }

  applyTheme(selectedTheme());
  installThemePicker();

  if (!placeThemePickerInOverview()) {
    const observer = new MutationObserver(() => {
      if (placeThemePickerInOverview()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();