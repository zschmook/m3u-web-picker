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
      <select id="uiThemeSelect" class="form-select form-select-sm" aria-label="Theme">
        ${THEMES.map(([value, name]) => `<option value="${value}">${name}</option>`).join("")}
      </select>`;
    host.appendChild(label);

    const select = document.getElementById("uiThemeSelect");
    select.value = selectedTheme();
    select.addEventListener("change", () => applyTheme(select.value));
  }

  applyTheme(selectedTheme());
  installThemePicker();
})();
