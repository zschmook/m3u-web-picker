(() => {
  const rows = document.getElementById("guideRows");
  if (!rows) return;

  let enabled = false;

  function debugTsPath(playUrl) {
    const value = String(playUrl || "").split("?", 1)[0];
    let match = value.match(/^\/guide\/play\/manual\/([^/]+)$/);
    if (match) return `/guide/debug/ts/manual/${match[1]}`;
    match = value.match(/^\/guide\/play\/sports\/(\d+)$/);
    if (match) return `/guide/debug/ts/sports/${match[1]}`;
    return "";
  }

  function decorateRows() {
    if (!enabled) return;
    for (const playButton of rows.querySelectorAll(".guide-play-btn")) {
      if (playButton.parentElement?.querySelector(".guide-copy-ts-btn")) continue;
      const tsPath = debugTsPath(playButton.dataset.playUrl || "");
      if (!tsPath) continue;

      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-outline-light btn-sm guide-copy-ts-btn ms-1";
      button.textContent = "Copy TS";
      button.title = "Copy debug MPEG-TS stream link";
      button.dataset.tsPath = tsPath;
      playButton.insertAdjacentElement("afterend", button);
    }
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

  rows.addEventListener("click", async event => {
    const button = event.target.closest(".guide-copy-ts-btn");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();

    const tsPath = String(button.dataset.tsPath || "");
    if (!tsPath) return;
    const url = new URL(tsPath, window.location.origin).href;
    const prior = button.textContent;
    try {
      await copyText(url);
      button.textContent = "Copied";
    } catch (error) {
      console.error("Could not copy TS link", error);
      button.textContent = "Copy failed";
    } finally {
      window.setTimeout(() => {
        button.textContent = prior;
      }, 1200);
    }
  });

  new MutationObserver(decorateRows).observe(rows, {childList: true, subtree: true});

  fetch(`/api/guide/debug/status?_=${Date.now()}`, {cache: "no-store"})
    .then(response => response.ok ? response.json() : {enabled: false})
    .then(data => {
      enabled = Boolean(data?.enabled);
      if (enabled) decorateRows();
    })
    .catch(error => console.debug("Guide debug tools unavailable", error));
})();
