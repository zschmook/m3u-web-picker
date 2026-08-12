(() => {
  "use strict";

  function cleanScheduleApiUi(root = document) {
    root.querySelectorAll?.("*").forEach(element => {
      if (element.children.length !== 0) return;
      const text = String(element.textContent || "").trim();
      if (text === "Planned API-backed schedule datasets") {
        element.textContent = "API-backed schedule datasets";
      }
      if (text === "Current · 0 games") {
        element.classList.add("ui-current-zero-games");
      }
    });
  }

  cleanScheduleApiUi();

  new MutationObserver(mutations => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach(node => {
        if (node instanceof Element) cleanScheduleApiUi(node);
      });
      if (mutation.type === "characterData") {
        const parent = mutation.target?.parentElement;
        if (parent) cleanScheduleApiUi(parent);
      }
    }
  }).observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
})();
