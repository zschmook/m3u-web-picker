(() => {
  "use strict";

  const player = document.getElementById("guidePlayer");
  const button = document.getElementById("guidePopoutBtn");
  const message = document.getElementById("guidePlayerMessage");
  const panel = document.getElementById("guidePlayerPanel");
  if (!player || !button) return;

  function supportsStandardPip() {
    return Boolean(document.pictureInPictureEnabled && typeof player.requestPictureInPicture === "function");
  }

  function supportsWebkitPip() {
    if (typeof player.webkitSetPresentationMode !== "function") return false;
    if (typeof player.webkitSupportsPresentationMode !== "function") return true;
    try {
      return player.webkitSupportsPresentationMode("picture-in-picture");
    } catch (_error) {
      return false;
    }
  }

  function isPoppedOut() {
    return document.pictureInPictureElement === player
      || player.webkitPresentationMode === "picture-in-picture";
  }

  function syncButton() {
    const supported = supportsStandardPip() || supportsWebkitPip();
    const poppedOut = isPoppedOut();
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
      const playback = player.play();
      if (playback && typeof playback.catch === "function") {
        playback.catch(error => {
          if (message) {
            message.textContent = `Video returned to the guide, but playback could not resume${error?.message ? `: ${error.message}` : "."}`;
          }
        });
      }
    });
  }

  button.addEventListener("click", async () => {
    try {
      if (player.webkitPresentationMode === "picture-in-picture") {
        player.webkitSetPresentationMode("inline");
      } else if (document.pictureInPictureElement === player) {
        await document.exitPictureInPicture();
      } else if (supportsStandardPip()) {
        try {
          await player.requestPictureInPicture();
        } catch (error) {
          if (!supportsWebkitPip()) throw error;
          player.webkitSetPresentationMode("picture-in-picture");
        }
      } else if (supportsWebkitPip()) {
        player.webkitSetPresentationMode("picture-in-picture");
      } else {
        throw new Error("Picture-in-Picture is not supported by this browser");
      }
    } catch (error) {
      if (message) {
        message.textContent = `Could not open the video popout${error?.message ? `: ${error.message}` : "."}`;
      }
    } finally {
      syncButton();
    }
  });

  ["loadedmetadata", "canplay", "playing", "emptied"].forEach(eventName => player.addEventListener(eventName, syncButton));
  player.addEventListener("enterpictureinpicture", () => {
    panel?.classList.add("guide-player-popped-out");
    syncButton();
  });
  player.addEventListener("leavepictureinpicture", () => {
    restorePlayerToGuide();
    syncButton();
  });
  player.addEventListener("webkitpresentationmodechanged", () => {
    if (player.webkitPresentationMode === "picture-in-picture") {
      panel?.classList.add("guide-player-popped-out");
    } else {
      restorePlayerToGuide();
    }
    syncButton();
  });
  new MutationObserver(syncButton).observe(player, {attributes: true, attributeFilter: ["class", "src"]});
  syncButton();
})();
