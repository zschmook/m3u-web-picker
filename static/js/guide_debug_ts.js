(() => {
  "use strict";

  let enabled = false;

  function debugPaths(playUrl) {
    const value = String(playUrl || "").split("?", 1)[0];
    let match = value.match(/^\/guide\/play\/manual\/([^/]+)$/);
    if (match) {
      return {
        ts: `/guide/debug/ts/manual/${match[1]}`,
        m3u: `/guide/debug/m3u/manual/${match[1]}.m3u`,
      };
    }
    match = value.match(/^\/guide\/play\/sports\/(\d+)$/);
    if (match) {
      return {
        ts: `/guide/debug/ts/sports/${match[1]}`,
        m3u: `/guide/debug/m3u/sports/${match[1]}.m3u`,
      };
    }
    return {ts: "", m3u: ""};
  }

  function decorateButton(playButton) {
    const parent = playButton.parentElement;
    if (!parent || parent.querySelector(".guide-copy-m3u-btn")) return;
    const paths = debugPaths(
      playButton.dataset.playUrl || playButton.dataset.guidePlayUrl || ""
    );
    if (!paths.m3u) return;

    const m3uButton = document.createElement("button");
    m3uButton.type = "button";
    m3uButton.className = "btn btn-outline-light btn-sm guide-copy-m3u-btn ms-1";
    m3uButton.textContent = "M3U";
    m3uButton.title = "Copy one-channel M3U URL for VLC";
    m3uButton.dataset.m3uPath = paths.m3u;
    playButton.insertAdjacentElement("afterend", m3uButton);

    if (!parent.querySelector(".guide-copy-ts-btn") && paths.ts) {
      const tsButton = document.createElement("button");
      tsButton.type = "button";
      tsButton.className = "btn btn-outline-secondary btn-sm guide-copy-ts-btn ms-1";
      tsButton.textContent = "TS";
      tsButton.title = "Copy direct debug MPEG-TS URL";
      tsButton.dataset.tsPath = paths.ts;
      m3uButton.insertAdjacentElement("afterend", tsButton);
    }
  }

  function decorateGuide() {
    if (!enabled) return;
    document
      .querySelectorAll(".guide-play-btn, .guide-station-play")
      .forEach(decorateButton);
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  async function copyDebugUrl(button, path, copiedLabel) {
    if (!path) return;
    const url = new URL(path, window.location.origin).href;
    const prior = button.textContent;
    try {
      await copyText(url);
      button.textContent = copiedLabel;
    } catch (error) {
      console.error("Could not copy guide debug link", error);
      button.textContent = "Failed";
    } finally {
      window.setTimeout(() => {
        button.textContent = prior;
      }, 1200);
    }
  }

  document.addEventListener("click", event => {
    const m3uButton = event.target.closest(".guide-copy-m3u-btn");
    if (m3uButton) {
      event.preventDefault();
      event.stopPropagation();
      copyDebugUrl(m3uButton, m3uButton.dataset.m3uPath || "", "Copied");
      return;
    }

    const tsButton = event.target.closest(".guide-copy-ts-btn");
    if (tsButton) {
      event.preventDefault();
      event.stopPropagation();
      copyDebugUrl(tsButton, tsButton.dataset.tsPath || "", "Copied");
    }
  });

  new MutationObserver(decorateGuide).observe(document.body, {
    childList: true,
    subtree: true,
  });

  fetch(`/api/guide/debug/status?_=${Date.now()}`, {cache: "no-store"})
    .then(response => (response.ok ? response.json() : {enabled: false}))
    .then(data => {
      enabled = Boolean(data?.enabled);
      if (enabled) decorateGuide();
    })
    .catch(error => console.debug("Guide debug tools unavailable", error));
})();
