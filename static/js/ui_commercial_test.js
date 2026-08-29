(() => {
  "use strict";

  const el = id => document.getElementById(id);
  let state = {active: false, elapsed_seconds: 0};
  let rotationState = {running: false, current_channel: {}};
  let busy = false;
  let lastPreviewUrl = "";
  let pendingPreviewUrl = "";
  let previewSessionId = "";
  let previewSwitchGeneration = 0;
  let previewSwitchChain = Promise.resolve();
  let lastChartSignature = "";
  const CHART_TICK_COUNT = 5;
  const CHART_HISTORY_MINUTES = 30;

  function formatElapsed(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(value / 60);
    return `${String(minutes).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }

  function formatChartTime(value) {
    if (!value) return "";
    const when = new Date(value);
    if (Number.isNaN(when.getTime())) return String(value);
    return when.toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"});
  }

  function chartTimeBounds(history) {
    const timestamps = (Array.isArray(history) ? history : [])
      .map(point => new Date(point?.observed_at).getTime())
      .filter(Number.isFinite);
    const latestObserved = timestamps.length ? Math.max(...timestamps) : Date.now();
    const end = Math.max(Date.now(), latestObserved);
    return {start: end - CHART_HISTORY_MINUTES * 60 * 1000, end};
  }

  function chartPath(history, feature, width, height, bounds) {
    const points = Array.isArray(history) ? history : [];
    if (!points.length) return "";
    const viewWidth = Math.max(1, Number(width || 600));
    const viewHeight = Math.max(1, Number(height || 150));
    const horizontalInset = 8;
    const verticalInset = 8;
    const xMin = horizontalInset;
    const yMin = verticalInset;
    const xScale = Math.max(1, viewWidth - horizontalInset * 2);
    const yScale = Math.max(1, viewHeight - verticalInset * 2);
    return points.map((point, index) => {
      const value = Math.max(0, Math.min(1, Number(point?.features?.[feature] || 0)));
      const observed = new Date(point?.observed_at).getTime();
      const timePosition = Number.isFinite(observed)
        ? (observed - bounds.start) / Math.max(1, bounds.end - bounds.start)
        : index / Math.max(1, points.length - 1);
      const x = xMin + Math.max(0, Math.min(1, timePosition)) * xScale;
      const y = yMin + (1 - value) * yScale;
      return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function buildTimestampSamples(history, count = CHART_TICK_COUNT) {
    const points = Array.isArray(history) ? history : [];
    if (!points.length) return [];
    const safeCount = Math.max(2, count);
    const bounds = chartTimeBounds(points);
    const picked = [];
    for (let i = 0; i < safeCount; i++) {
      picked.push(new Date(
        bounds.start + (i / Math.max(1, safeCount - 1)) * (bounds.end - bounds.start)
      ).toISOString());
    }
    return picked.map(formatChartTime);
  }

  function renderChartTimestamps(history) {
    const axis = el("uiChannelModelChartTimestamps");
    if (!axis) return;
    axis.replaceChildren();
    const labels = buildTimestampSamples(history, CHART_TICK_COUNT);
    labels.forEach(label => {
      const tag = document.createElement("span");
      tag.textContent = label || "—";
      axis.appendChild(tag);
    });
  }

  function renderChannelChart(profile) {
    const history = Array.isArray(profile?.history) ? profile.history : [];
    const chart = el("uiChannelModelChart");
    const chartRect = chart?.getBoundingClientRect();
    const width = Number(chartRect?.width || 600);
    const height = Number(chartRect?.height || 150);
    const signature = JSON.stringify([
      profile?.channel_identity || "",
      width.toFixed(0),
      height.toFixed(0),
      history.length,
      history[0]?.observed_at || "",
      history.at(-1)?.observed_at || "",
    ]);
    if (signature === lastChartSignature) return;
    lastChartSignature = signature;
    chart?.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const bounds = chartTimeBounds(history);
    const paths = {
      uiChannelCutLine: "cut_density",
      uiChannelColorLine: "color_volatility",
      uiChannelGraphicLine: "program_graphics_confidence",
      uiChannelBugLine: "bug_identity_confidence",
      // Keep the final decision signal last so its white line is painted above
      // every contributing signal.
      uiChannelConfidenceLine: "commercial_confidence",
    };
    Object.entries(paths).forEach(([id, feature]) => {
      el(id)?.setAttribute("d", chartPath(history, feature, width, height, bounds));
    });
    renderChartTimestamps(history);
  }

  function latestStream(injection) {
    const streams = Array.isArray(injection?.streams) ? injection.streams : [];
    if (!streams.length) return null;
    return streams.reduce((best, current) => {
      const bestAt = Number(best?.created_at || 0);
      const currentAt = Number(current?.created_at || 0);
      return currentAt > bestAt ? current : best;
    }, streams[0]);
  }

  function streamToPreviewUrl(stream) {
    if (!stream?.identity) return "";
    const [kind, ...identityParts] = String(stream.identity).split(":");
    const identity = identityParts.join(":");
    if (!identity) return "";
    if (kind === "manual") {
      return `/guide/play/manual/${encodeURIComponent(identity)}`;
    }
    if (kind === "sports" && identityParts.length) {
      return `/guide/play/sports/${encodeURIComponent(identity)}`;
    }
    return "";
  }

  function newPreviewSessionId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function releaseCommercialPreview({keepalive = false} = {}) {
    const video = el("uiCommercialTestVideo");
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    const sessionId = previewSessionId;
    previewSessionId = "";
    lastPreviewUrl = "";
    if (sessionId) {
      try {
        await fetch(`/guide/play/preview/${encodeURIComponent(sessionId)}`, {
          method: "DELETE",
          cache: "no-store",
          keepalive,
        });
      } catch (_error) {
        // The media request closing also releases the server-side process.
      }
    }
  }

  function showPreviewReconnect(message = "Preview reconnecting…") {
    const video = el("uiCommercialTestVideo");
    const fallback = el("uiCommercialTestFallback");
    video?.classList.add("d-none");
    if (fallback) {
      fallback.textContent = message;
      fallback.classList.remove("d-none");
    }
  }

  function requestPreviewSwitch(previewUrl) {
    if (!previewUrl || previewUrl === lastPreviewUrl || previewUrl === pendingPreviewUrl) return;
    pendingPreviewUrl = previewUrl;
    const generation = ++previewSwitchGeneration;
    previewSwitchChain = previewSwitchChain.then(async () => {
      if (generation !== previewSwitchGeneration) return;
      showPreviewReconnect();
      await releaseCommercialPreview();
      // Give the old streaming response one scheduler turn to run its cleanup
      // and release its FFmpeg session before requesting the replacement.
      await new Promise(resolve => setTimeout(resolve, 150));
      if (generation !== previewSwitchGeneration) return;

      const video = el("uiCommercialTestVideo");
      const fallback = el("uiCommercialTestFallback");
      if (!video || !fallback) return;
      lastPreviewUrl = previewUrl;
      previewSessionId = newPreviewSessionId();
      video.muted = false;
      video.src = `${previewUrl}?preview_session=${encodeURIComponent(previewSessionId)}&_=${Date.now()}`;
      video.load();
      video.play().catch(() => {
        // Browsers may reject audible autoplay without a recent user gesture.
        video.muted = true;
        video.play().catch(() => showPreviewReconnect());
      });
    }).finally(() => {
      if (generation === previewSwitchGeneration) pendingPreviewUrl = "";
    });
  }

  function renderCommercialPreview(stream, visible) {
    const preview = el("uiCommercialTestPreview");
    const video = el("uiCommercialTestVideo");
    const fallback = el("uiCommercialTestFallback");
    if (!preview || !video || !fallback) return;

    const collectorPreviewUrl = String(rotationState.current_channel?.play_url || "");
    const previewUrl = rotationState.running
      ? collectorPreviewUrl
      : streamToPreviewUrl(stream);
    if (!visible || !previewUrl) {
      preview.classList.add("d-none");
      if (previewSessionId || video.getAttribute("src")) {
        previewSwitchGeneration += 1;
        pendingPreviewUrl = "";
        void releaseCommercialPreview();
      }
      fallback.classList.add("d-none");
      return;
    }

    preview.classList.remove("d-none");
    if (previewUrl !== lastPreviewUrl) requestPreviewSwitch(previewUrl);
    if (pendingPreviewUrl) return;
    if (!video.paused) return;
    video.muted = false;
    video.play().catch(() => {
      // Browsers may reject audible autoplay without a recent user gesture.
      // Keep the preview running muted in that case; its native controls let
      // the user enable audio with one click.
      video.muted = true;
      video.play().catch(() => {});
    });
  }

  function render() {
    const button = el("uiCommercialTestToggle");
    const preview = el("uiCommercialTestPreview");
    const status = el("uiCommercialTestStatus");
    const timer = el("uiCommercialTestTimer");
    if (!button || !preview || !status || !timer) return;
    button.disabled = busy;
    button.textContent = state.active ? "End Commercial" : "Start Commercial";
    button.classList.toggle("ui-btn-danger", state.active);
    button.classList.toggle("ui-btn-primary", !state.active);
    const injection = state.injection || {};
    const stream = latestStream(injection);
    const detector = stream?.logo_detector || {};
    const hasCommercial = Boolean(state.active || stream?.commercial_active);
    renderCommercialPreview(stream, Boolean(hasCommercial || rotationState.running));
    const decisionBadge = el("uiCommercialDecisionBadge");
    if (decisionBadge) {
      decisionBadge.classList.toggle("d-none", !Boolean(detector.commercial));
    }

    const eligible = Number(injection.eligible_streams || 0);
    const failed = Array.isArray(injection.results)
      ? injection.results.filter(item => !item.ok).length
      : 0;
    if (failed) {
      status.textContent = `Commercial switch failed on ${failed} stream${failed === 1 ? "" : "s"}`;
    } else if (state.active && eligible) {
      status.textContent = `Commercial active on ${eligible} Jellyfin stream${eligible === 1 ? "" : "s"}`;
    } else if (state.active) {
      status.textContent = "Preview active — no Jellyfin FFmpeg stream is currently connected";
    } else {
      status.textContent = eligible
        ? `${eligible} Jellyfin stream${eligible === 1 ? "" : "s"} ready`
        : "No Jellyfin FFmpeg stream connected";
    }
    timer.textContent = formatElapsed(state.elapsed_seconds);
    const learningChannelStatus = el("uiLearningChannelStatus");
    const learningChannelValue = el("uiLearningChannelValue");
    if (learningChannelStatus && learningChannelValue) {
      const learnedChannel = detector.channel_identity || stream?.identity || "";
      if (learnedChannel) {
        learningChannelStatus.textContent = "Learning channel";
        learningChannelValue.textContent = learnedChannel;
      } else if (stream) {
        learningChannelStatus.textContent = "Learning channel";
        learningChannelValue.textContent = "No channel identity has been learned yet";
      } else {
        learningChannelStatus.textContent = "Learning channel";
        learningChannelValue.textContent = "Waiting for Jellyfin stream";
      }
    }
    const logoStatus = el("uiLogoDetectorStatus");
    const logoTimestamp = el("uiLogoDetectorTimestamp");
    if (logoStatus && logoTimestamp) {
      const labels = {
        learning: "Learning broadcast logo",
        program: detector.logo_detected ? "Broadcast logo detected" : "Learning broadcast logo",
        commercial: detector.logo_detected ? "Broadcast logo detected" : "Broadcast logo not detected",
        error: "Logo detector unavailable",
        disabled: "Automatic detection disabled",
      };
      logoStatus.textContent = labels[detector.state] || "Logo detector idle";
      if (detector.error) {
        logoTimestamp.textContent = detector.error;
      } else if (detector.logo_detected_at) {
        const learned = new Date(detector.logo_detected_at);
        const lastSeen = detector.logo_last_seen_at ? new Date(detector.logo_last_seen_at) : null;
        const region = detector.region ? ` · ${detector.region}` : "";
        const learnedLabel = Number.isNaN(learned.getTime())
          ? detector.logo_detected_at
          : learned.toLocaleString([], {dateStyle: "medium", timeStyle: "medium"});
        const lastSeenLabel = !lastSeen || Number.isNaN(lastSeen.getTime())
          ? detector.logo_last_seen_at || "not confirmed yet"
          : lastSeen.toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"});
        logoTimestamp.textContent = `Learned: ${learnedLabel} · Last seen: ${lastSeenLabel}${region}`;
      } else if (stream && detector.state !== "disabled") {
        logoTimestamp.textContent = "Sampling likely logo regions twice per second";
      } else if (stream) {
        logoTimestamp.textContent = "Enable it under Settings → Encoding";
      } else {
        logoTimestamp.textContent = "Starts when a Jellyfin FFmpeg stream connects";
      }
      const scoreboardStatus = el("uiScoreboardDetectorStatus");
      const scoreboardDetail = el("uiScoreboardDetectorDetail");
      if (scoreboardStatus && scoreboardDetail) {
        scoreboardStatus.textContent = !detector.scoreboard_applicable
          ? "Scoreboard detection not used"
          : detector.scoreboard_detected ? "Scoreboard detected" : "Scoreboard not detected";
        const scoreboardFound = detector.scoreboard_detected_at
          ? new Date(detector.scoreboard_detected_at) : null;
        const scoreboardTime = scoreboardFound && !Number.isNaN(scoreboardFound.getTime())
          ? scoreboardFound.toLocaleString([], {dateStyle: "medium", timeStyle: "medium"})
          : detector.scoreboard_detected_at;
        scoreboardDetail.textContent = !detector.scoreboard_applicable
          ? "Used only for sports-generated channels"
          : detector.scoreboard_detected
          ? `Detected: ${scoreboardTime} · ${detector.scoreboard_region}`
          : detector.logo_detected
            ? "Comparing two frames 0.5 seconds apart"
            : "Waits until the broadcast logo is learned";
      }
      const countdownStatus = el("uiCountdownDetectorStatus");
      const countdownDetail = el("uiCountdownDetectorDetail");
      if (countdownStatus && countdownDetail) {
        const countdownConfidence = Number(detector.countdown_confidence || 0).toFixed(1);
        countdownStatus.textContent = !detector.countdown_applicable
          ? "Countdown detection not used"
          : detector.countdown_detected
            ? "Countdown overlay detected"
            : "Countdown overlay not detected";
        countdownDetail.textContent = !detector.countdown_applicable
          ? "Disabled for sports-generated channels"
          : detector.countdown_detected
            ? `${detector.countdown_region} · Confidence: ${countdownConfidence}% · direct commercial signal`
            : Number(detector.countdown_probation_seconds_remaining || 0) > 0
              ? `Waiting ${Math.ceil(Number(detector.countdown_probation_seconds_remaining))}s for normal network-bug detection first`
            : !detector.countdown_fallback_available
              ? `Network bug is authoritative · countdown fallback inactive · Confidence: ${countdownConfidence}%`
            : `Scanning all four corners · Confidence: ${countdownConfidence}%`;
      }
      if (detector.logo_detected && Number.isFinite(Number(detector.commercial_confidence))) {
        const confidence = Number(detector.commercial_confidence).toFixed(1);
        logoTimestamp.textContent += ` · Commercial confidence: ${confidence}%`;
        if (Number.isFinite(Number(detector.local_break_confidence))
            && Number.isFinite(Number(detector.color_volatility))) {
          const localConfidence = Number(detector.local_break_confidence).toFixed(1);
          const colorVolatility = Number(detector.color_volatility).toFixed(1);
          logoTimestamp.textContent += ` · Local: ${localConfidence}% · Color changes: ${colorVolatility}%`;
        }
      }
      const modelStatus = el("uiChannelModelStatus");
      const modelDetail = el("uiChannelModelDetail");
      const profile = state.channel_profile || {};
      if (modelStatus && modelDetail) {
        if (!stream) {
          modelStatus.textContent = "Channel model waiting";
          modelDetail.textContent = "Starts when a non-sports FFmpeg stream connects";
        } else if (detector.scoreboard_applicable) {
          modelStatus.textContent = "Sports channel model not used";
          modelDetail.textContent = "Sports-generated channels use scoreboard evidence";
        } else if (detector.channel_model_ready) {
          modelStatus.textContent = `Channel commercial profile: ${Number(profile.score || detector.channel_model_score || 0).toFixed(1)}%`;
          modelDetail.textContent = `Adaptive shadow score · ${Number(profile.retention_days || 14)}-day rolling history`;
        } else {
          modelStatus.textContent = "Learning this channel";
          modelDetail.textContent = profile.channel_identity
            ? "Needs 30 program samples and 3 commercial samples"
            : "Needs confirmed program and commercial samples";
        }
      }
      if (el("uiChannelProgramSamples")) {
        el("uiChannelProgramSamples").textContent = String(profile.program_samples || 0);
      }
      if (el("uiChannelCommercialSamples")) {
        el("uiChannelCommercialSamples").textContent = String(profile.commercial_samples || 0);
      }
      if (el("uiChannelShadowScore")) {
        el("uiChannelShadowScore").textContent = profile.ready
          ? `${Number(profile.score || 0).toFixed(1)}%` : "Learning";
      }
      if (el("uiClassifiedCommercialCount")) {
        el("uiClassifiedCommercialCount").textContent = String(
          state.signature_library?.classified || 0,
        );
      }
      renderChannelChart(profile);
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }

  async function refresh() {
    if (!el("uiCommercialTestToggle")) return;
    try {
      [state, rotationState] = await Promise.all([
        api(`/api/commercial-break?_=${Date.now()}`),
        api(`/api/channel-learning-rotation?_=${Date.now()}`),
      ]);
      render();
    } catch (error) {
      el("uiCommercialTestStatus").textContent = error.message;
    }
  }

  async function toggle() {
    if (busy) return;
    busy = true;
    render();
    try {
      const stream = latestStream(state.injection || {});
      state = await api("/api/commercial-break", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          action: state.active ? "end" : "start",
          stream_identity: stream?.identity || "",
        }),
      });
    } catch (error) {
      el("uiCommercialTestStatus").textContent = error.message;
    } finally {
      busy = false;
      render();
    }
  }

  async function mark(label) {
    const stream = latestStream(state.injection || {});
    const streamIdentity = stream?.identity || "";
    try {
      const result = await api("/api/commercial-break/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          label,
          stream_identity: streamIdentity,
        }),
      });
      const modelDetail = el("uiChannelModelDetail");
      if (modelDetail) {
        modelDetail.textContent = label === "commercial"
          ? "Commercial sample saved for this channel"
          : "Program sample saved for this channel";
      }
      return result;
    } catch (error) {
      const modelDetail = el("uiChannelModelDetail");
      if (modelDetail) modelDetail.textContent = error.message;
      return null;
    }
  }

  document.addEventListener("click", event => {
    if (event.target.closest("#uiCommercialTestToggle")) void toggle();
    if (event.target.closest("#uiMarkProgramBtn")) void mark("program");
    if (event.target.closest("#uiMarkCommercialBtn")) void mark("commercial");
  });
  el("uiCommercialTestVideo")?.addEventListener("playing", event => {
    event.currentTarget.classList.remove("d-none");
    el("uiCommercialTestFallback")?.classList.add("d-none");
  });
  el("uiCommercialTestVideo")?.addEventListener("error", event => {
    event.currentTarget.classList.add("d-none");
    showPreviewReconnect();
  });
  window.addEventListener("pagehide", () => releaseCommercialPreview({keepalive: true}));
  setInterval(() => {
    if (state.active) state.elapsed_seconds = Number(state.elapsed_seconds || 0) + 1;
    render();
  }, 1000);
  setInterval(refresh, 10000);
  void refresh();
})();
