// Android app handoff must happen synchronously inside the user's Play click.
function guideIsAndroidPhone() {
  const hints = navigator.userAgentData;
  if (hints?.platform === "Android") return hints.mobile === true;
  return /Android/i.test(navigator.userAgent || "") && /Mobile/i.test(navigator.userAgent || "");
}

function guidePrefersVlc() {
  return guideIsAndroidPhone()
    && document.getElementById("guidePhonePlayer")?.value === "vlc"
    && !guideState.roku.active && !currentCastSession();
}

function guidePlayLabel(isPlaying) {
  return guidePrefersVlc() ? "Play in VLC" : (isPlaying ? "Playing" : "Play");
}

function guideVlcIntent(playUrl) {
  if (!playUrl) throw new Error("This channel has no playback URL.");
  const media = new URL(playUrl, window.location.href);
  if (!["http:", "https:"].includes(media.protocol) || media.origin !== window.location.origin) {
    throw new Error("VLC requires a stream from this guide server.");
  }
  media.hash = "";
  const fallback = new URL("/guide", window.location.origin);
  fallback.searchParams.set("player", "browser");
  // Strip fragments and escape literal separators before adding intent fields.
  return `intent://${media.href.slice(media.protocol.length + 2).replace(/;/g, "%3B")}`
    + `#Intent;scheme=${media.protocol.slice(0, -1)};package=org.videolan.vlc;`
    + "action=android.intent.action.VIEW;type=video/*;"
    + `S.browser_fallback_url=${encodeURIComponent(fallback.href)};end`;
}

function playGuideInVlc(channel) {
  try {
    const intent = guideVlcIntent(channel?.play_url);
    stopLocalStream({hidePanel: true});
    stopListenStream();
    clearRestartPlayback();
    guideState.currentChannel = null;
    guideState.mode = "stopped";
    renderGuide();
    document.getElementById("guidePhonePlayerHelp").textContent =
      "Opening VLC. Control playback there. If it doesn't open, choose Browser and press Play again.";
    window.location.assign(intent);
  } catch (error) {
    document.getElementById("guidePhonePlayerHelp").textContent =
      `${error.message} Choose Browser to play here.`;
  }
}

(() => {
  if (!guideIsAndroidPhone()) return;
  const controls = document.getElementById("guidePhonePlayerControls");
  const select = document.getElementById("guidePhonePlayer");
  controls.classList.remove("d-none");
  let preference = "vlc";
  try { preference = localStorage.getItem("m3u-guide-phone-player") || preference; } catch (_) {}
  const fallback = new URL(window.location.href).searchParams.get("player") === "browser";
  select.value = fallback || preference === "browser" ? "browser" : "vlc";
  if (fallback) document.getElementById("guidePhonePlayerHelp").textContent =
    "VLC did not open. Browser playback is selected; press Play on your channel.";
  select.addEventListener("change", () => {
    try { localStorage.setItem("m3u-guide-phone-player", select.value); } catch (_) {}
    document.getElementById("guidePhonePlayerHelp").textContent = select.value === "vlc"
      ? "Opens the VLC app. For locked-screen audio, enable Play videos in background in VLC settings."
      : "Plays inside this browser.";
    renderGuide();
  });
})();
