(() => {
  "use strict";

  const state = {
    timer: null,
    requestId: 0,
    validSignature: "",
    validating: false,
    loading: false,
  };

  const escText = value => String(value ?? "").trim();

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

  function wizardBody() {
    return document.getElementById("devOnboardingBody");
  }

  function isPrimaryStep() {
    return wizardBody()?.querySelector("h2")?.textContent?.trim() === "Primary Provider";
  }

  function isAlreadyConfigured() {
    const schedule = document.getElementById("devOnboardingSchedule");
    return schedule?.dataset.providerConfigured === "true";
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
      name: escText(f.name?.value) || "Primary",
      url: escText(f.url?.value),
      username: String(f.username?.value || ""),
      password: String(f.password?.value || ""),
    };
  }

  function signature(value = values()) {
    return [value.url, value.username, value.password].join("\u001f");
  }

  function setStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
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
    schedule.classList.toggle("is-disabled", !unlocked);
    schedule.dataset.providerValidated = String(Boolean(unlocked));
    for (const control of scheduleControls()) control.disabled = !unlocked;

    const note = schedule.querySelector(".dev-onboarding-schedule-note");
    if (note) {
      if (unlocked) {
        let detected = "local timezone";
        try {
          detected = Intl.DateTimeFormat().resolvedOptions().timeZone || detected;
        } catch {}
        note.textContent = `Provider validated. Detected local timezone: ${detected}. Change it here if this Picker should follow a different local clock.`;
      } else {
        note.textContent = "Validate the Primary Provider to configure automatic updates.";
      }
    }
  }

  function setNextState(enabled, label = "Continue") {
    const button = document.getElementById("devOnboardingNext");
    if (!button) return;
    button.disabled = !enabled;
    button.textContent = label;
  }

  function invalidate(message = "") {
    state.validSignature = "";
    state.validating = false;
    setScheduleUnlocked(false);
    setNextState(false, "Continue");
    if (message) setStatus(message);
  }

  function credentialsState(value = values()) {
    const hasUser = Boolean(value.username);
    const hasPass = Boolean(value.password);
    if (hasUser && hasPass) return "complete";
    if (hasUser || hasPass) return "partial";
    return "empty";
  }

  function friendlyValidationWait(error) {
    const raw = String(error?.message || "").toLowerCase();
    if (raw.includes("404")) return "Provider URL returned 404. If this is Xtream, enter both username and password; validation will retry automatically.";
    if (raw.includes("empty") || raw.includes("no data") || raw.includes("did not return")) {
      return "Provider URL did not return a usable playlist. If this is Xtream, enter both username and password; validation will retry automatically.";
    }
    return "That URL did not validate as a direct M3U. If this is Xtream, enter both username and password; validation will retry automatically.";
  }

  async function validateCurrent() {
    if (!isPrimaryStep() || isAlreadyConfigured() || state.loading) return;
    const value = values();
    const currentSignature = signature(value);

    if (!value.url) {
      invalidate("Paste the provider URL to begin validation.");
      return;
    }
    if (!/^https?:\/\//i.test(value.url)) {
      invalidate("Provider URL must start with http:// or https://");
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
    setNextState(false, "Checking provider…");
    setStatus(
      creds === "complete"
        ? "Validating provider URL and Xtream credentials…"
        : "Checking provider URL…"
    );

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
      setNextState(true, "Continue");
      const kind = result.kind === "xtream" ? "Xtream provider" : "M3U provider";
      setStatus(`${kind} validated. Configure the schedule, then Continue.`, "success");
    } catch (error) {
      if (requestId !== state.requestId || signature() !== currentSignature) return;
      state.validating = false;
      state.validSignature = "";
      setScheduleUnlocked(false);
      setNextState(false, "Continue");

      if (creds === "empty") {
        setStatus(friendlyValidationWait(error));
      } else {
        setStatus(error.message || "Provider validation failed.", "error");
      }
    }
  }

  function queueValidation(delay = 450) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => void validateCurrent(), delay);
  }

  function onProviderInput() {
    if (!isPrimaryStep() || isAlreadyConfigured() || state.loading) return;
    ++state.requestId; // invalidate any in-flight response immediately
    const value = values();
    const creds = credentialsState(value);
    state.validSignature = "";
    setScheduleUnlocked(false);
    setNextState(false, "Continue");

    if (!value.url) {
      setStatus("Paste the provider URL to begin validation.");
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

  async function installValidatedProvider() {
    if (state.loading || !isPrimaryStep() || isAlreadyConfigured()) return;
    const value = values();
    const currentSignature = signature(value);
    if (!state.validSignature || state.validSignature !== currentSignature) {
      invalidate("Provider details changed. Waiting for validation…");
      queueValidation(0);
      return;
    }

    state.loading = true;
    setNextState(false, "Loading provider…");
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
      setNextState(false, "Continue");
      setStatus(error.message || "Could not load the primary provider.", "error");
      queueValidation(700);
    } finally {
      if (progressTimer) clearInterval(progressTimer);
    }
  }

  function bindPrimaryStep() {
    if (!isPrimaryStep() || isAlreadyConfigured()) return;
    const f = fields();
    if (!f.url || !f.username || !f.password || !f.next || !f.schedule) return;
    if (f.url.dataset.validationBound === "true") {
      // The schedule block may have been recreated after an async enhancement.
      const valid = state.validSignature && state.validSignature === signature();
      setScheduleUnlocked(Boolean(valid));
      setNextState(Boolean(valid), "Continue");
      return;
    }

    f.url.dataset.validationBound = "true";
    setScheduleUnlocked(false);
    setNextState(false, "Continue");

    for (const input of [f.url, f.username, f.password]) {
      input.addEventListener("input", onProviderInput);
      input.addEventListener("change", onProviderInput);
      input.addEventListener("paste", () => setTimeout(onProviderInput, 0));
    }

    if (values().url) queueValidation(100);
    else setStatus("Paste the provider URL to begin validation.");
  }

  const observer = new MutationObserver(() => bindPrimaryStep());
  observer.observe(document.body, {childList: true, subtree: true});
  bindPrimaryStep();

  document.addEventListener("click", event => {
    const button = event.target.closest("#devOnboardingNext");
    if (!button || !isPrimaryStep() || isAlreadyConfigured()) return;

    // The base wizard still owns later steps. On the unconfigured Primary page,
    // this validation layer owns Continue so an unvalidated provider can never
    // slip through to /api/load-url.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (button.disabled) return;
    void installValidatedProvider();
  }, true);
})();
