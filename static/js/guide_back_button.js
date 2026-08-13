(() => {
  "use strict";

  const STORAGE_KEY = "m3u-picker.ui.theme";
  const THEMES = [
    ["midnight", "Midnight"],
    ["slate", "Slate"],
    ["oled-black", "OLED Black"],
    ["carbon", "Carbon"],
    ["light", "Light"],
    ["ice", "Ice"],
    ["terminal-amber", "Terminal Amber"],
    ["terminal-green", "Terminal Green"],
    ["cornfield", "Cornfield"],
    ["ketchup-mustard", "Ketchup & Mustard"]
  ];
  const VALID_THEMES = new Set(THEMES.map(([value]) => value));

  function selectedTheme() {
    const queryTheme = new URLSearchParams(window.location.search).get("theme");
    if (VALID_THEMES.has(queryTheme)) return queryTheme;
    const saved = localStorage.getItem(STORAGE_KEY);
    return VALID_THEMES.has(saved) ? saved : "midnight";
  }

  function applyTheme(theme) {
    const value = VALID_THEMES.has(theme) ? theme : "midnight";
    document.body.dataset.uiTheme = value;
    localStorage.setItem(STORAGE_KEY, value);
    const select = document.getElementById("guideThemeSelect");
    if (select && select.value !== value) select.value = value;
  }

  function installThemePicker() {
    if (document.getElementById("guideThemeSelect")) return;
    const actions = document.querySelector(".guide-header > .d-flex");
    if (!actions) return;

    const wrap = document.createElement("label");
    wrap.className = "guide-theme-control";
    wrap.setAttribute("for", "guideThemeSelect");
    wrap.innerHTML = `
      <span class="visually-hidden">Theme</span>
      <select id="guideThemeSelect" class="form-select form-select-sm" aria-label="TV Guide theme">
        ${THEMES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
      </select>`;

    const refresh = document.getElementById("guideRefreshBtn");
    actions.insertBefore(wrap, refresh || actions.firstChild);

    const select = document.getElementById("guideThemeSelect");
    select.value = document.body.dataset.uiTheme || selectedTheme();
    select.addEventListener("change", () => applyTheme(select.value));
  }

  applyTheme(selectedTheme());
  installThemePicker();

  const button = document.getElementById("guideCloseBtn");
  if (!button) return;

  button.textContent = "Back";
  button.title = "Return to the previous app page";

  button.addEventListener("click", event => {
    event.preventDefault();
    event.stopImmediatePropagation();

    let sameOriginReferrer = false;
    try {
      sameOriginReferrer = Boolean(document.referrer)
        && new URL(document.referrer).origin === window.location.origin;
    } catch (_) {
      sameOriginReferrer = false;
    }

    if (sameOriginReferrer && window.history.length > 1) {
      window.history.back();
      return;
    }

    window.location.assign("/");
  }, true);
})();
