(() => {
  "use strict";

  const player = document.getElementById("guidePlayer");
  const button = document.getElementById("guidePopoutBtn");
  const message = document.getElementById("guidePlayerMessage");
  const panel = document.getElementById("guidePlayerPanel");
  if (!player || !button) return;

  const supported = Boolean(document.pictureInPictureEnabled && player.requestPictureInPicture);

  function syncButton() {
    const poppedOut = document.pictureInPictureElement === player;
    const playable = Boolean(player.currentSrc && player.readyState > 0 && !player.classList.contains("d-none"));
    button.disabled = !supported || (!playable && !poppedOut);
    button.textContent = poppedOut ? "Close popout" : "Pop out";
    button.title = supported
      ? (poppedOut ? "Return video to the guide" : "Keep this video floating above the guide")
      : "Picture-in-Picture is not supported by this browser";
  }

  function restorePlayerToGuide() {
    if (!player.currentSrc || !panel) return;
    player.classList.remove("d-none");
    panel.classList.remove("d-none", "guide-player-popped-out");
    window.focus();
    requestAnimationFrame(() => {
      panel.scrollIntoView({behavior: "smooth", block: "start"});
      button.focus({preventScroll: true});
      window.dispatchEvent(new Event("resize"));
    });
  }

  button.addEventListener("click", async () => {
    try {
      if (document.pictureInPictureElement === player) {
        await document.exitPictureInPicture();
      } else {
        await player.requestPictureInPicture();
      }
    } catch (error) {
      if (message) {
        message.textContent = `Could not open the video popout${error?.message ? `: ${error.message}` : "."}`;
      }
    } finally {
      syncButton();
    }
  });

  ["loadedmetadata", "playing", "emptied"].forEach(eventName => player.addEventListener(eventName, syncButton));
  player.addEventListener("enterpictureinpicture", () => {
    panel?.classList.add("guide-player-popped-out");
    syncButton();
  });
  player.addEventListener("leavepictureinpicture", () => {
    restorePlayerToGuide();
    syncButton();
  });
  new MutationObserver(syncButton).observe(player, {attributes: true, attributeFilter: ["class", "src"]});
  syncButton();
})();
