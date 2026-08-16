(() => {
  "use strict";

  const DEMO_PLAYLISTS = [
    {
      id: "iptv-org-us",
      name: "iptv-org U.S. Demo",
      label: "iptv-org U.S.",
      detail: "Recommended • larger U.S. public-stream catalog",
      url: "https://iptv-org.github.io/iptv/countries/us.m3u",
      recommended: true,
    },
    {
      id: "free-tv-us",
      name: "Free-TV U.S. Demo",
      label: "Free-TV U.S.",
      detail: "Smaller alternate U.S. public-stream list",
      url: "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_usa.m3u8",
    },
    {
      id: "iptv-org-categories",
      name: "iptv-org Global Demo",
      label: "iptv-org Global / Categories",
      detail: "Large mixed international list • useful as a heavier stress test",
      url: "https://iptv-org.github.io/iptv/index.category.m3u",
    },
  ];

  const state = {
    manual: null,
  };

  function wizardBody() {
    return document.getElementById("devOnboardingBody");
  }

  function isPrimaryProviderStep() {
    return wizardBody()?.querySelector("h2")?.textContent?.trim() === "Primary Provider";
  }

  function field(id) {
    return document.getElementById(id);
  }

  function providerGrid() {
    const url = field("devProviderUrl");
    return url?.closest(".dev-onboarding-grid") || null;
  }

  function dispatchProviderChange(input) {
    if (!input) return;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
  }

  function selectedDemo() {
    const selected = document.querySelector('input[name="devDemoProviderChoice"]:checked');
    return DEMO_PLAYLISTS.find(item => item.id === selected?.value) || DEMO_PLAYLISTS[0];
  }

  function captureManualValues() {
    state.manual = {
      name: field("devProviderName")?.value || "Primary",
      url: field("devProviderUrl")?.value || "",
      username: field("devProviderUsername")?.value || "",
      password: field("devProviderPassword")?.value || "",
    };
  }

  function setProviderValues(values) {
    const name = field("devProviderName");
    const url = field("devProviderUrl");
    const username = field("devProviderUsername");
    const password = field("devProviderPassword");

    if (name) name.value = values.name || "Primary";
    if (url) url.value = values.url || "";
    if (username) username.value = values.username || "";
    if (password) password.value = values.password || "";

    dispatchProviderChange(url);
    dispatchProviderChange(username);
    dispatchProviderChange(password);
  }

  function applyMode() {
    const toggle = field("devDemoProviderMode");
    const options = field("devDemoProviderOptions");
    const grid = providerGrid();
    if (!toggle || !options || !grid) return;

    const enabled = Boolean(toggle.checked);
    options.hidden = !enabled;
    grid.hidden = enabled;

    if (enabled) {
      captureManualValues();
      const demo = selectedDemo();
      setProviderValues({name: demo.name, url: demo.url, username: "", password: ""});
    } else if (state.manual) {
      setProviderValues(state.manual);
    } else {
      dispatchProviderChange(field("devProviderUrl"));
    }
  }

  function applySelectedDemo() {
    if (!field("devDemoProviderMode")?.checked) return;
    const demo = selectedDemo();
    setProviderValues({name: demo.name, url: demo.url, username: "", password: ""});
  }

  function installStyles() {
    if (document.getElementById("devDemoProviderStyles")) return;
    const style = document.createElement("style");
    style.id = "devDemoProviderStyles";
    style.textContent = `
      .dev-demo-provider-mode {
        margin: 18px 0 16px;
        padding: 14px 16px;
        border: 1px solid #3f4d63;
        border-radius: 12px;
        background: #0b1220;
      }
      .dev-demo-provider-switch {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        cursor: pointer;
      }
      .dev-demo-provider-switch-copy {
        display: grid;
        gap: 3px;
      }
      .dev-demo-provider-switch-copy strong {
        color: #f8fafc;
        font-size: .96rem;
      }
      .dev-demo-provider-switch-copy span {
        color: #94a3b8;
        font-size: .82rem;
      }
      .dev-demo-provider-switch input {
        flex: 0 0 auto;
        width: 2.6rem;
        height: 1.35rem;
      }
      .dev-demo-provider-options {
        display: grid;
        gap: 9px;
        margin-top: 14px;
      }
      .dev-demo-provider-options[hidden] {
        display: none;
      }
      .dev-demo-provider-choice {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 10px;
        align-items: start;
        padding: 11px 12px;
        border: 1px solid #3f4d63;
        border-radius: 9px;
        background: #111827;
        cursor: pointer;
      }
      .dev-demo-provider-choice:has(input:checked) {
        border-color: #60a5fa;
        background: rgba(30, 64, 175, .18);
      }
      .dev-demo-provider-choice input {
        margin-top: 3px;
      }
      .dev-demo-provider-choice-copy {
        display: grid;
        gap: 2px;
      }
      .dev-demo-provider-choice-copy strong {
        color: #f8fafc;
      }
      .dev-demo-provider-choice-copy span {
        color: #94a3b8;
        font-size: .8rem;
      }
      .dev-demo-provider-note {
        margin-top: 10px;
        color: #94a3b8;
        font-size: .78rem;
      }
    `;
    document.head.appendChild(style);
  }

  function demoChoicesHtml() {
    return DEMO_PLAYLISTS.map(item => `
      <label class="dev-demo-provider-choice">
        <input type="radio" name="devDemoProviderChoice" value="${item.id}" ${item.recommended ? "checked" : ""}>
        <span class="dev-demo-provider-choice-copy">
          <strong>${item.label}</strong>
          <span>${item.detail}</span>
        </span>
      </label>
    `).join("");
  }

  function enhancePrimaryProvider() {
    if (!isPrimaryProviderStep() || field("devDemoProviderMode")) return;
    const grid = providerGrid();
    if (!grid) return;

    installStyles();

    const block = document.createElement("section");
    block.className = "dev-demo-provider-mode";
    block.innerHTML = `
      <label class="dev-demo-provider-switch" for="devDemoProviderMode">
        <span class="dev-demo-provider-switch-copy">
          <strong>I don't have an IPTV service yet — just testing</strong>
          <span>Use a free public M3U so you can try Picker before adding your own provider.</span>
        </span>
        <input id="devDemoProviderMode" type="checkbox" role="switch">
      </label>
      <div id="devDemoProviderOptions" class="dev-demo-provider-options" hidden>
        ${demoChoicesHtml()}
        <div class="dev-demo-provider-note">These are third-party public playlists. Channel availability and lineup can change without notice.</div>
      </div>
    `;

    grid.insertAdjacentElement("beforebegin", block);

    field("devDemoProviderMode")?.addEventListener("change", applyMode);
    document.querySelectorAll('input[name="devDemoProviderChoice"]').forEach(input => {
      input.addEventListener("change", applySelectedDemo);
    });
  }

  const observer = new MutationObserver(() => enhancePrimaryProvider());
  observer.observe(document.body, {childList: true, subtree: true});
  enhancePrimaryProvider();
})();
