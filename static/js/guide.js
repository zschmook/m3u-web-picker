let guideChannels = [];
let currentGuideChannel = null;
let castContext = null;
let castApiReady = false;
let castSessionLoadInFlight = false;
let lastCastMediaUrl = "";
let currentCastPipelineToken = "";
let guideConfig = {media_origin: ""};

const guideEls = {
  status: document.getElementById("guideStatus"),
  rows: document.getElementById("guideRows"),
  empty: document.getElementById("guideEmpty"),
  search: document.getElementById("guideSearch"),
  visibleCount: document.getElementById("guideVisibleCount"),
  playerPanel: document.getElementById("guidePlayerPanel"),
  player: document.getElementById("guidePlayer"),
  playerTitle: document.getElementById("guidePlayerTitle"),
  playerMeta: document.getElementById("guidePlayerMeta"),
  playbackBadge: document.getElementById("guidePlaybackBadge"),
  nowPlayingLabel: document.getElementById("guideNowPlayingLabel"),
  playerMessage: document.getElementById("guidePlayerMessage"),
  castBtn: document.getElementById("guideCastBtn"),
  castRelay: document.getElementById("guideCastRelay"),
  lanTestBtn: document.getElementById("guideLanTestBtn"),
  castStatus: document.getElementById("guideCastStatus"),
  castScreen: document.getElementById("guideCastScreen"),
  castScreenDevice: document.getElementById("guideCastScreenDevice"),
  castScreenChannel: document.getElementById("guideCastScreenChannel"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function filteredGuideChannels() {
  const query = guideEls.search.value.trim().toLowerCase();
  if (!query) return guideChannels;
  return guideChannels.filter(channel => {
    const text = `${channel.number} ${channel.name} ${channel.group} ${channel.subtitle || ""}`.toLowerCase();
    return text.includes(query);
  });
}

function renderGuide() {
  const visible = filteredGuideChannels();
  guideEls.rows.innerHTML = visible.map(channel => {
    const logo = channel.logo
      ? `<img class="guide-logo" src="${escapeHtml(channel.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : "";
    const subtitle = channel.subtitle
      ? `<div class="guide-channel-subtitle">${escapeHtml(channel.subtitle)}</div>`
      : "";
    const generated = channel.generated
      ? `<span class="badge text-bg-primary guide-generated-badge">Auto</span>`
      : "";
    const isCurrent = currentGuideChannel?.play_url === channel.play_url;
    return `
      <tr class="${isCurrent ? "guide-current-row" : ""}">
        <td>${escapeHtml(channel.number)}</td>
        <td class="guide-channel-cell">
          <div class="guide-channel-main">
            ${logo}
            <div class="min-w-0">
              <div class="guide-channel-name">${escapeHtml(channel.name)}${generated}</div>
              ${subtitle}
            </div>
          </div>
        </td>
        <td>${escapeHtml(channel.group || "—")}</td>
        <td class="text-end">
          <button class="btn ${isCurrent ? "btn-outline-light" : "btn-success"} btn-sm guide-play-btn" type="button"
            data-play-url="${escapeHtml(channel.play_url)}"
            data-channel-name="${escapeHtml(channel.name)}"
            data-channel-group="${escapeHtml(channel.group || "")}" 
            data-channel-logo="${escapeHtml(channel.logo || "")}">${isCurrent ? "Playing" : "Play"}</button>
        </td>
      </tr>`;
  }).join("");

  guideEls.visibleCount.textContent = `${visible.length.toLocaleString()} channel${visible.length === 1 ? "" : "s"}`;
  guideEls.empty.classList.toggle("d-none", visible.length !== 0);
}

async function loadGuide() {
  guideEls.status.textContent = "Loading curated lineup…";
  try {
    const response = await fetch("/api/guide/channels", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load curated lineup.");
    guideChannels = Array.isArray(data.channels) ? data.channels : [];
    renderGuide();
    guideEls.status.textContent = `${guideChannels.length.toLocaleString()} currently served channel${guideChannels.length === 1 ? "" : "s"}`;
  } catch (error) {
    guideChannels = [];
    renderGuide();
    guideEls.status.textContent = error.message;
  }
}

function isLoopbackHost(host) {
  const normalized = String(host || "").trim().toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1" || normalized === "[::1]";
}

async function loadGuideConfig() {
  try {
    const response = await fetch("/api/guide/config", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load guide network configuration.");
    guideConfig = data || {media_origin: ""};
    if (!guideConfig.media_origin && !isLoopbackHost(window.location.hostname)) {
      guideConfig.media_origin = window.location.origin;
    }
    guideEls.castRelay.textContent = guideConfig.media_origin || "LAN relay not configured";
  } catch (error) {
    guideConfig = {media_origin: ""};
    guideEls.castRelay.textContent = "LAN relay unavailable";
    console.error("Could not load guide network configuration", error);
  }
  updateCastStatus();
}

function castMediaOrigin() {
  const configured = String(guideConfig?.media_origin || "").trim();
  if (configured) return configured.replace(/\/$/, "");
  if (!isLoopbackHost(window.location.hostname)) return window.location.origin.replace(/\/$/, "");
  return "";
}

function absoluteCastMediaUrl(path) {
  const origin = castMediaOrigin();
  if (!origin || !path) return "";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${origin}${normalizedPath}`;
}

async function stopCastRelayToken(token) {
  if (!token) return;
  try {
    await fetch("/api/guide/cast/stop", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token}),
      cache: "no-store",
    });
  } catch (error) {
    console.warn("Could not stop Cast HLS relay", error);
  }
}

async function stopCastRelay() {
  const token = currentCastPipelineToken;
  currentCastPipelineToken = "";
  await stopCastRelayToken(token);
}

async function startCastRelay(channel) {
  const previousToken = currentCastPipelineToken;
  const response = await fetch("/api/guide/cast/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({play_url: channel?.play_url || ""}),
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not start Cast HLS relay.");
  const mediaUrl = absoluteCastMediaUrl(data.playlist_path || "");
  if (!mediaUrl) throw new Error("The LAN media relay is not configured.");
  currentCastPipelineToken = data.token || "";
  return {
    mediaUrl,
    contentType: data.content_type || "application/x-mpegurl",
    token: currentCastPipelineToken,
    previousToken,
  };
}

async function testLanRelay() {
  const origin = castMediaOrigin();
  if (!origin) {
    updateCastStatus("LAN relay is not configured.");
    return;
  }
  guideEls.lanTestBtn.disabled = true;
  const previous = guideEls.lanTestBtn.textContent;
  guideEls.lanTestBtn.textContent = "Testing…";
  try {
    const response = await fetch(`${origin}/api/guide/ping?_=${Date.now()}`, {cache: "no-store"});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error("LAN relay did not answer correctly.");
    updateCastStatus(`LAN relay reachable at ${origin}.`);
  } catch (error) {
    updateCastStatus(`LAN relay test failed at ${origin}. Check macOS firewall / Docker LAN access.`);
    console.error("LAN relay test failed", error);
  } finally {
    guideEls.lanTestBtn.disabled = false;
    guideEls.lanTestBtn.textContent = previous;
  }
}

function currentCastSession() {
  return castContext?.getCurrentSession?.() || null;
}

function currentCastDeviceName() {
  const session = currentCastSession();
  try {
    return session?.getCastDevice?.()?.friendlyName || "Cast device";
  } catch (_) {
    return "Cast device";
  }
}

function updateCastStatus(message = "") {
  const session = currentCastSession();
  if (session) {
    const device = currentCastDeviceName();
    guideEls.castBtn.disabled = false;
    guideEls.castBtn.textContent = "Disconnect";
    guideEls.castStatus.textContent = message || `Receiver session connected to ${device}.`;
    guideEls.castScreenDevice.textContent = device;
    return;
  }

  guideEls.castBtn.textContent = "Cast";
  guideEls.castBtn.disabled = !castApiReady;

  if (message) {
    guideEls.castStatus.textContent = message;
  } else if (!window.isSecureContext) {
    guideEls.castStatus.textContent = "Cast sender needs a secure origin. Open this guide at http://localhost:1000/guide; receiver media still comes from the LAN relay.";
  } else if (!castApiReady) {
    guideEls.castStatus.textContent = "Google Cast SDK loading…";
  } else if (!castMediaOrigin()) {
    guideEls.castStatus.textContent = "Google Cast is ready, but the LAN media relay is not configured.";
  } else {
    guideEls.castStatus.textContent = "Ready for a real Google Cast receiver session.";
  }
}

function showLocalPlayer() {
  guideEls.player.classList.remove("d-none");
  guideEls.castScreen.classList.add("d-none");
  guideEls.nowPlayingLabel.textContent = "Now playing";
  guideEls.playbackBadge.textContent = "Local";
  guideEls.playbackBadge.className = "badge rounded-pill text-bg-secondary";
}

function showCastPlayer() {
  const device = currentCastDeviceName();
  guideEls.player.classList.add("d-none");
  guideEls.castScreen.classList.remove("d-none");
  guideEls.nowPlayingLabel.textContent = "Now casting";
  guideEls.playbackBadge.textContent = "Cast";
  guideEls.playbackBadge.className = "badge rounded-pill text-bg-primary";
  guideEls.castScreenDevice.textContent = device;
  guideEls.castScreenChannel.textContent = currentGuideChannel?.name || "";
}

function stopLocalStream({hidePanel = false} = {}) {
  guideEls.player.pause();
  guideEls.player.removeAttribute("src");
  guideEls.player.load();
  if (hidePanel) guideEls.playerPanel.classList.add("d-none");
}

async function stopRemoteMedia() {
  const session = currentCastSession();
  const media = session?.getMediaSession?.();
  if (!media) return;
  try {
    await new Promise(resolve => media.stop(null, resolve, resolve));
  } catch (_) {
    // Best effort; ending the session or receiver disconnect will stop it too.
  }
}

async function stopPlayback() {
  stopLocalStream({hidePanel: false});
  await stopRemoteMedia();
  await stopCastRelay();
  currentGuideChannel = null;
  guideEls.playerPanel.classList.add("d-none");
  guideEls.playerMessage.textContent = "";
  guideEls.playerMeta.textContent = "";
  showLocalPlayer();
  renderGuide();
}

function setCurrentChannel(channel) {
  currentGuideChannel = channel;
  guideEls.playerPanel.classList.remove("d-none");
  guideEls.playerTitle.textContent = channel.name || "Channel";
  guideEls.playerMeta.textContent = channel.group || "";
  renderGuide();
  guideEls.playerPanel.scrollIntoView({behavior: "smooth", block: "start"});
}

function playLocalChannel(channel) {
  stopLocalStream({hidePanel: false});
  setCurrentChannel(channel);
  showLocalPlayer();
  guideEls.playerMessage.textContent = "Starting stream…";
  const url = channel.play_url || "";
  guideEls.player.src = `${url}${url.includes("?") ? "&" : "?"}_=${Date.now()}`;
  const attempt = guideEls.player.play();
  if (attempt?.catch) {
    attempt.catch(() => {
      guideEls.playerMessage.textContent = "The browser did not start this stream automatically. Press Play again; if it still fails, check the container logs for ffmpeg errors.";
    });
  }
}

async function castChannel(channel) {
  const session = currentCastSession();
  if (!session) throw new Error("Choose a Chromecast / Google TV receiver first.");
  if (castSessionLoadInFlight) return;

  const device = currentCastDeviceName();
  setCurrentChannel(channel);
  stopLocalStream({hidePanel: false});
  showCastPlayer();
  guideEls.playerMessage.textContent = `Connecting to ${device}…`;
  updateCastStatus(`Receiver session: ${device} · starting HLS relay on ${castMediaOrigin()}`);

  castSessionLoadInFlight = true;
  let relay = null;
  try {
    // Browser playback stays on the working fragmented-MP4 endpoint. Cast gets
    // its own short rolling HLS playlist + MPEG-TS segments so the receiver can
    // pull discrete live media objects over the LAN.
    relay = await startCastRelay(channel);
    const mediaUrl = relay.mediaUrl;
    lastCastMediaUrl = mediaUrl;
    guideEls.playerMessage.textContent = `Starting on ${device}…`;
    updateCastStatus(`Receiver session: ${device} · loading HLS ${mediaUrl}`);

    // This is intentionally NOT HTMLMediaElement.remote / browser Remote Playback.
    // CAF hands the LAN HLS playlist directly to the selected Cast receiver.
    const mediaInfo = new chrome.cast.media.MediaInfo(mediaUrl, relay.contentType);
    mediaInfo.streamType = chrome.cast.media.StreamType.LIVE;
    if (chrome.cast.media.HlsSegmentFormat?.TS) {
      mediaInfo.hlsSegmentFormat = chrome.cast.media.HlsSegmentFormat.TS;
    }
    if (chrome.cast.media.HlsVideoSegmentFormat?.MPEG2_TS) {
      mediaInfo.hlsVideoSegmentFormat = chrome.cast.media.HlsVideoSegmentFormat.MPEG2_TS;
    }

    const metadata = new chrome.cast.media.GenericMediaMetadata();
    metadata.title = channel.name || "M3U Web Picker";
    metadata.subtitle = channel.group || "Live TV";
    if (channel.logo && /^https?:\/\//i.test(channel.logo)) {
      metadata.images = [new chrome.cast.Image(channel.logo)];
    }
    mediaInfo.metadata = metadata;

    const request = new chrome.cast.media.LoadRequest(mediaInfo);
    request.autoplay = true;
    await session.loadMedia(request);
    if (relay.previousToken && relay.previousToken !== relay.token) {
      await stopCastRelayToken(relay.previousToken);
    }
    guideEls.playerMessage.textContent = `Playing on ${device}.`;
    updateCastStatus(`Receiver session: ${device} · playing HLS ${mediaUrl}`);
  } catch (error) {
    const failedToken = relay?.token || currentCastPipelineToken;
    if (failedToken && failedToken === currentCastPipelineToken) {
      currentCastPipelineToken = relay?.previousToken || "";
    }
    await stopCastRelayToken(failedToken);
    showLocalPlayer();
    const detail = error?.description || error?.code || error?.message || error || "unknown error";
    guideEls.playerMessage.textContent = `Receiver load failed: ${detail}.`;
    updateCastStatus(`Receiver session: ${device} · HLS/loadMedia failed: ${detail}`);
    throw error;
  } finally {
    castSessionLoadInFlight = false;
  }
}

async function playChannel(channel) {
  if (currentCastSession()) {
    try {
      await castChannel(channel);
      return;
    } catch (error) {
      console.error("Cast playback failed", error);
      return;
    }
  }
  playLocalChannel(channel);
}

async function toggleCast() {
  if (!castApiReady || !castContext) {
    updateCastStatus("Google Cast SDK is not ready yet.");
    return;
  }

  const session = currentCastSession();
  if (session) {
    try {
      await stopRemoteMedia();
      await stopCastRelay();
      castContext.endCurrentSession(true);
      lastCastMediaUrl = "";
    } catch (error) {
      console.error("Could not end Cast receiver session", error);
    }
    return;
  }

  if (!castMediaOrigin()) {
    updateCastStatus("LAN media relay is not configured.");
    return;
  }

  guideEls.castBtn.disabled = true;
  updateCastStatus("Opening Google Cast receiver picker…");
  try {
    await castContext.requestSession();
    const newSession = currentCastSession();
    if (!newSession) {
      updateCastStatus("No Google Cast receiver session was created.");
      return;
    }

    const device = currentCastDeviceName();
    updateCastStatus(`Receiver session connected to ${device}.`);
    if (currentGuideChannel) {
      // One deliberate loadMedia call after requestSession resolves. exp5 could
      // race this against SESSION_STARTED and send the same channel twice.
      await castChannel(currentGuideChannel);
    } else {
      guideEls.playerMessage.textContent = `Connected to ${device}. Press Play on a channel to load it on the receiver.`;
    }
  } catch (error) {
    if (error !== chrome.cast.ErrorCode.CANCEL) {
      console.error("Cast receiver session request failed", error);
      updateCastStatus(`Could not start receiver session: ${error?.description || error?.code || error}.`);
    }
  } finally {
    updateCastStatus(guideEls.castStatus.textContent);
  }
}

function initializeCastApi() {
  try {
    castContext = cast.framework.CastContext.getInstance();
    castContext.setOptions({
      receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
      autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
    });

    castContext.addEventListener(
      cast.framework.CastContextEventType.CAST_STATE_CHANGED,
      () => updateCastStatus()
    );

    castContext.addEventListener(
      cast.framework.CastContextEventType.SESSION_STATE_CHANGED,
      event => {
        const started = event.sessionState === cast.framework.SessionState.SESSION_STARTED
          || event.sessionState === cast.framework.SessionState.SESSION_RESUMED;
        const ended = event.sessionState === cast.framework.SessionState.SESSION_ENDED;

        if (started) {
          // Do not load media here. toggleCast() owns the initial load so the
          // selected receiver gets exactly one explicit loadMedia request.
          updateCastStatus(`Receiver session connected to ${currentCastDeviceName()}.`);
        } else if (ended) {
          lastCastMediaUrl = "";
          stopCastRelay();
          showLocalPlayer();
          updateCastStatus("Chromecast disconnected.");
          if (currentGuideChannel) {
            guideEls.playerMessage.textContent = "Chromecast disconnected. Press Play to resume locally.";
          }
          renderGuide();
        } else {
          updateCastStatus();
        }
      }
    );

    castApiReady = true;
    updateCastStatus();
  } catch (error) {
    castApiReady = false;
    console.error("Cast initialization failed", error);
    updateCastStatus(`Cast initialization failed: ${error?.message || error}.`);
  }
}

// exp7: keep Chrome's native Remote Playback path out of this experiment.
// Only the Google Cast Application Framework receiver session below is allowed
// to move video off this Mac.
guideEls.player.disableRemotePlayback = true;

window.__onGCastApiAvailable = function(isAvailable) {
  if (isAvailable) initializeCastApi();
  else updateCastStatus("Google Cast is not available in this browser.");
};

guideEls.player.addEventListener("playing", () => {
  showLocalPlayer();
  guideEls.playerMessage.textContent = "";
});

guideEls.player.addEventListener("waiting", () => {
  guideEls.playerMessage.textContent = "Buffering…";
});

guideEls.player.addEventListener("error", () => {
  guideEls.playerMessage.textContent = "Playback failed. ffmpeg may have rejected the provider stream, or the browser may have rejected the converted MP4.";
});

guideEls.rows.addEventListener("click", event => {
  const button = event.target.closest(".guide-play-btn");
  if (!button) return;
  playChannel({
    name: button.dataset.channelName || "Channel",
    group: button.dataset.channelGroup || "",
    logo: button.dataset.channelLogo || "",
    play_url: button.dataset.playUrl || "",
  });
});

guideEls.search.addEventListener("input", renderGuide);
document.getElementById("guideRefreshBtn").addEventListener("click", loadGuide);
document.getElementById("guideStopBtn").addEventListener("click", stopPlayback);
document.getElementById("guideCloseBtn").addEventListener("click", () => window.close());
guideEls.castBtn.addEventListener("click", toggleCast);
guideEls.lanTestBtn.addEventListener("click", testLanRelay);

updateCastStatus();
loadGuideConfig();
loadGuide();
