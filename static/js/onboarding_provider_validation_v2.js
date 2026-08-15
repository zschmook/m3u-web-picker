(() => {
  "use strict";

  const boundInputs = new WeakSet();
  const state = {
    timer: null,
    requestId: 0,
    validSignature: "",
    validating: false,
    loading: false,
  };

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      const error = new Error(data.error || data.message || `Request failed (${response.status}).`);
      error.payload = data;
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function body() {
    return document.getElementById("devOnboardingBody");
  }

  function isPrimaryStep() {
    return body()?.querySelector("h2")?.textContent?.trim() === "Primary Provider";
  }

  function fields() {
    return {
      name: document.getElementById("devProviderName"),
      url: document.getElementById("devProviderUrl"),
      username: document.getElementById("devProviderUsername"),
      password: document.getElementById("devProviderPassword"),
      next: document.getElementById("devOnboardingNext"),
      schedule: document.getElementById("devOnboardingSchedule"),
    };
  }

  function values() {
    const f = fields();
    return {
      name: String(f.name?.value || "Primary").trim() || "Primary",
      url: String(f.url?.value || "").trim(),
      username: String(f.username?.value || ""),
      password: String(f.password?.value || ""),
    };
  }

  function signature(value = values()) {
    return [value.url, value.username, value.password].join("\u001f");
  }

  function credentialsState(value = values()) {
    const user = Boolean(value.username);
    const pass = Boolean(value.password);
    if (user && pass) return "complete";
    if (user || pass) return "partial";
    return "empty";
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    if (target.textContent !== String(message || "")) target.textContent = message || "";
    const className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
    if (target.className !== className) target.className = className;
  }

  function scheduleControls() {
    return [
      document.getElementById("devScheduleEnabled"),
      document.getElementById("devScheduleTime"),
      document.getElementById("devScheduleTimezone"),
    ].filter(Boolean);
  }

  function setScheduleUnlocked(unlocked) {
    const schedule = document.getElementById("devOnboardingSchedule");
    if (!schedule) return;

    const nextValue = String(Boolean(unlocked));
    if (schedule.dataset.providerValidated !== nextValue) {
      schedule.dataset.providerValidated = nextValue;
    }
    schedule.classList.toggle("is-disabled", !unlocked);

    for (const control of scheduleControls()) {
      if (control.disabled === Boolean(unlocked)) control.disabled = !unlocked;
    }

    const note = schedule.querySelector(".dev-onboarding-schedule-note");
    if (!note) return;

    let message = "Validate the Primary Provider to configure automatic updates.";
    if (unlocked) {
      let detected = "local timezone";
      try {
        detected = Intl.DateTimeFormat().resolvedOptions().timeZone || detected;
      } catch {}
      message = `Provider validated. Detected local timezone: ${detected}. Change it here if this Picker should follow a different local clock.`;
    }
    if (note.textContent !== message) note.textContent = message;
  }

  function setNext(enabled, label = "Continue") {
    const button = document.getElementById("devOnboardingNext");
    if (!button) return;
    const disabled = !enabled;
    if (button.disabled !== disabled) button.disabled = disabled;
    if (button.textContent !== label) button.textContent = label;
  }

  function invalidate(message = "") {
    state.validSignature = "";
    state.validating = false;
    setScheduleUnlocked(false);
    setNext(false, "Continue");
    if (message) setStatus(message);
  }

  function friendlyDirectFailure(error) {
    const raw = String(error?.message || "").toLowerCase();
    if (raw.includes("404")) {
      return "Provider URL returned 404. If this is Xtream, enter both username and password; validation will retry automatically.";
    }
    if (raw.includes("empty") || raw.includes("no data") || raw.includes("did not return")) {
      return "Provider URL returned no usable playlist. If this is Xtream, enter both username and password; validation will retry automatically.";
    }
    return "URL did not validate as a direct M3U. If this is Xtream, enter both username and password; validation will retry automatically.";
  }

  async function validateCurrent() {
    if (!isPrimaryStep() || state.loading) return;

    const value = values();
    const currentSignature = signature(value);

    if (!value.url) {
      invalidate("Paste the provider URL to begin validation.");
      return;
    }
    if (!/^https?:\/\//i.test(value.url)) {
      invalidate();
      setStatus("Provider URL must start with http:// or https://", "error");
      return;
    }

    const creds = credentialsState(value);
    if (creds === "partial") {
      invalidate("Waiting for both Xtream username and password before validating…");
      return;
    }

    const requestId = ++state.requestId;
    state.validating = true;
    state.validSignature = "";
    setScheduleUnlocked(false);
    setNext(false, "Checking provider…");
    setStatus(creds === "complete"
      ? "Validating provider URL and Xtream credentials…"
      : "Checking provider URL…");

    try {
      const result = await api("/api/providers/validate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(value),
      });

      if (requestId !== state.requestId || signature() !== currentSignature) return;
      if (!result.valid) throw new Error(result.error || "Provider validation failed.");

      state.validating = false;
      state.validSignature = currentSignature;
      setScheduleUnlocked(true);
      setNext(true, "Continue");
      const label = result.kind === "xtream" ? "Xtream provider" : "M3U provider";
      setStatus(`${label} validated. Configure the schedule, then Continue.`, "success");
    } catch (error) {
      if (requestId !== state.requestId || signature() !== currentSignature) return;

      state.validating = false;
      state.validSignature = "";
      setScheduleUnlocked(false);
      setNext(false, "Continue");

      if (creds === "empty") setStatus(friendlyDirectFailure(error));
      else setStatus(error.message || "Provider validation failed.", "error");
    }
  }

  function queueValidation(delay = 450) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => void validateCurrent(), delay);
  }

  function onProviderInput() {
    if (!isPrimaryStep() || state.loading) return;

    ++state.requestId;
    state.validSignature = "";
    setScheduleUnlocked(false);
    setNext(false, "Continue");

    const value = values();
    const creds = credentialsState(value);

    if (!value.url) {
      setStatus("Paste the provider URL to begin validation.");
      return;
    }
    if (!/^https?:\/\//i.test(value.url)) {
      setStatus("Provider URL must start with http:// or https://", "error");
      return;
    }
    if (creds === "partial") {
      setStatus("Waiting for both Xtream username and password before validating…");
      return;
    }

    queueValidation();
  }

  async function saveSchedule() {
    const enabled = Boolean(document.getElementById("devScheduleEnabled")?.checked);
    const time = document.getElementById("devScheduleTime")?.value || "03:00";
    let timezone = document.getElementById("devScheduleTimezone")?.value || "";
    if (!timezone) {
      try {
        timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York";
      } catch {
        timezone = "America/New_York";
      }
    }

    await api("/api/master-update", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled, time}),
    });
    await api("/api/sports/settings", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({timezone, refresh_time: time}),
    });
    await api("/api/onboarding", {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        current_step: 2,
        answers: {
          provider_configured: true,
          schedule_enabled: enabled,
          schedule_time: time,
          schedule_timezone: timezone,
        },
      }),
    });
  }

  async function installProvider() {
    if (state.loading || !isPrimaryStep()) return;

    const value = values();
    const currentSignature = signature(value);
    if (!state.validSignature || state.validSignature !== currentSignature) {
      invalidate("Provider details changed. Waiting for validation…");
      queueValidation(0);
      return;
    }

    state.loading = true;
    setNext(false, "Loading provider…");
    for (const control of [fields().name, fields().url, fields().username, fields().password, ...scheduleControls()]) {
      if (control) control.disabled = true;
    }
    setStatus("Provider validated. Loading the full channel catalog… this can take a while.");

    let progressTimer = null;
    try {
      progressTimer = setInterval(async () => {
        try {
          const progress = await api("/api/providers/progress");
          const text = [progress.stage, progress.detail].filter(Boolean).join(" • ");
          if (text) setStatus(text);
        } catch {}
      }, 1200);

      await api("/api/load-url", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(value),
      });

      setStatus("Primary provider loaded. Saving automatic update schedule…");
      await saveSchedule();
      location.reload();
    } catch (error) {
      state.loading = false;
      state.validSignature = "";
      for (const control of [fields().name, fields().url, fields().username, fields().password]) {
        if (control) control.disabled = false;
      }
      setScheduleUnlocked(false);
      setNext(false, "Continue");
      setStatus(error.message || "Could not load the primary provider.", "error");
      queueValidation(700);
    } finally {
      if (progressTimer) clearInterval(progressTimer);
    }
  }

  function bindIfReady() {
    if (!isPrimaryStep()) return;
    const f = fields();
    if (!f.url || !f.username || !f.password || !f.next || !f.schedule) return;
    if (boundInputs.has(f.url)) return;

    boundInputs.add(f.url);
    setScheduleUnlocked(false);
    setNext(false, "Continue");

    for (const input of [f.url, f.username, f.password]) {
      input.addEventListener("input", onProviderInput);
      input.addEventListener("change", onProviderInput);
      input.addEventListener("paste", () => setTimeout(onProviderInput, 0));
    }

    if (values().url) queueValidation(100);
    else setStatus("Paste the provider URL to begin validation.");
  }

  const observer = new MutationObserver(() => bindIfReady());
  observer.observe(document.body, {childList: true, subtree: true});
  bindIfReady();

  document.addEventListener("click", event => {
    const button = event.target.closest("#devOnboardingNext");
    if (!button || !isPrimaryStep()) return;

    event.preventDefault();
    event.stopImmediatePropagation();
    if (button.disabled) return;
    void installProvider();
  }, true);
})();
