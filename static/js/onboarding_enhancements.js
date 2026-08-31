(() => {
  "use strict";

  const API_SPORTS_URL = "https://api-sports.io";

  const esc = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || data.message || `Request failed (${response.status}).`);
    }
    return data;
  }

  function wizardBody() {
    return document.getElementById("devOnboardingBody");
  }

  function heading() {
    return wizardBody()?.querySelector("h2")?.textContent?.trim() || "";
  }

  function setWizardStatus(message, kind = "") {
    const target = document.getElementById("devOnboardingStatus");
    if (!target) return;
    target.textContent = message || "";
    target.className = `dev-onboarding-status${kind ? ` ${kind}` : ""}`;
  }

  function detectedTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/New_York";
    } catch {
      return "America/New_York";
    }
  }

  function timezoneOptions(selected, detected) {
    let values = [];
    try {
      if (typeof Intl.supportedValuesOf === "function") {
        values = Intl.supportedValuesOf("timeZone");
      }
    } catch {
      values = [];
    }
    if (!values.length) {
      values = [
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "America/Anchorage",
        "Pacific/Honolulu",
        "UTC",
      ];
    }
    for (const value of [selected, detected, "UTC"]) {
      if (value && !values.includes(value)) values.push(value);
    }
    values.sort((a, b) => a.localeCompare(b));
    return values.map(value =>
      `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(value)}</option>`
    ).join("");
  }

  function installStyles() {
    if (document.getElementById("devOnboardingEnhancementStyles")) return;
    const style = document.createElement("style");
    style.id = "devOnboardingEnhancementStyles";
    style.textContent = `
      .dev-onboarding-schedule {
        margin-top: 20px;
        padding: 16px;
        border: 1px solid #3f4d63;
        border-radius: 12px;
        background: #0b1220;
      }
      .dev-onboarding-schedule.is-disabled {
        opacity: .5;
      }
      .dev-onboarding-schedule-heading {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
      }
      .dev-onboarding-schedule-heading strong {
        font-size: .96rem;
      }
      .dev-onboarding-schedule-grid {
        display: grid;
        grid-template-columns: minmax(160px, .8fr) minmax(160px, .8fr) minmax(260px, 1.4fr);
        gap: 12px;
        align-items: end;
      }
      .dev-onboarding-schedule-field label {
        display: block;
        margin-bottom: 6px;
        color: #dbe4f0;
        font-size: .82rem;
        font-weight: 700;
      }
      .dev-onboarding-schedule-field input[type="time"],
      .dev-onboarding-schedule-field select {
        width: 100%;
        min-height: 40px;
        padding: 8px 10px;
        border: 1px solid #46556d;
        border-radius: 8px;
        background: #111827;
        color: #f8fafc;
      }
      .dev-onboarding-schedule-switch {
        display: flex;
        align-items: center;
        gap: 9px;
        min-height: 40px;
      }
      .dev-onboarding-schedule-switch input {
        width: 2.4rem;
        height: 1.25rem;
      }
      .dev-onboarding-schedule-note {
        margin-top: 10px;
        color: #94a3b8;
        font-size: .82rem;
      }
      .dev-onboarding-api-benefit {
        margin-top: 16px;
        padding: 14px 16px;
        border: 1px solid rgba(96, 165, 250, .45);
        border-radius: 10px;
        background: rgba(30, 64, 175, .12);
        color: #dbeafe;
      }
      .dev-onboarding-api-benefit a {
        display: inline-block;
        margin-top: 10px;
        font-weight: 750;
        color: #93c5fd;
      }
      @media (max-width: 760px) {
        .dev-onboarding-schedule-grid {
          grid-template-columns: 1fr;
        }
      }
    `;
    document.head.appendChild(style);
  }

  async function enhancePrimaryProvider() {
    const body = wizardBody();
    if (!body || heading() !== "Primary Provider" || document.getElementById("devOnboardingSchedule")) return;

    let onboardingPayload;
    let masterPayload;
    try {
      [onboardingPayload, masterPayload] = await Promise.all([
        api("/api/onboarding"),
        api("/api/master-update"),
      ]);
    } catch {
      return;
    }

    if (heading() !== "Primary Provider" || document.getElementById("devOnboardingSchedule")) return;

    const providerConfigured = Boolean(onboardingPayload.provider_configured);
    const answers = onboardingPayload.state?.answers || {};
    const master = masterPayload.master_update || {};
    const detected = detectedTimezone();
    const selectedZone = String(answers.schedule_timezone || detected);
    const selectedTime = String(answers.schedule_time || master.time || "03:00");
    const scheduleEnabled = answers.schedule_enabled === undefined
      ? Boolean(master.enabled ?? true)
      : Boolean(answers.schedule_enabled);
    const disabled = providerConfigured ? "" : "disabled";

    const schedule = document.createElement("section");
    schedule.id = "devOnboardingSchedule";
    schedule.dataset.providerConfigured = String(providerConfigured);
    schedule.className = `dev-onboarding-schedule${providerConfigured ? "" : " is-disabled"}`;
    schedule.innerHTML = `
      <div class="dev-onboarding-schedule-heading">
        <strong>Automatic Update Schedule</strong>
        <span class="dev-onboarding-muted">Defaults to 3:00 AM local time</span>
      </div>
      <div class="dev-onboarding-schedule-grid">
        <div class="dev-onboarding-schedule-field">
          <label>Automatic updates</label>
          <label class="dev-onboarding-schedule-switch" for="devScheduleEnabled">
            <input id="devScheduleEnabled" type="checkbox" role="switch" ${scheduleEnabled ? "checked" : ""} ${disabled}>
            <span>${scheduleEnabled ? "On" : "Off"}</span>
          </label>
        </div>
        <div class="dev-onboarding-schedule-field">
          <label for="devScheduleTime">Daily at</label>
          <input id="devScheduleTime" type="time" value="${esc(selectedTime)}" ${disabled}>
        </div>
        <div class="dev-onboarding-schedule-field">
          <label for="devScheduleTimezone">Timezone</label>
          <select id="devScheduleTimezone" ${disabled}>${timezoneOptions(selectedZone, detected)}</select>
        </div>
      </div>
      <div class="dev-onboarding-schedule-note">
        ${providerConfigured
          ? `Detected local timezone: ${esc(detected)}. Change it here if this Picker should follow a different local clock.`
          : "Validate the Primary Provider to configure automatic updates."}
      </div>
    `;

    const actions = body.querySelector(".dev-onboarding-actions");
    if (actions) body.insertBefore(schedule, actions);
    else body.appendChild(schedule);

    document.getElementById("devScheduleEnabled")?.addEventListener("change", event => {
      const label = event.target.closest("label")?.querySelector("span");
      if (label) label.textContent = event.target.checked ? "On" : "Off";
    });
  }

  function enhanceSportsApiInformation() {
    const body = wizardBody();
    if (!body || heading() !== "Sports API Information" || document.getElementById("devSportsApiBenefit")) return;
    const help = body.querySelector(".dev-onboarding-help");
    if (!help) return;

    const block = document.createElement("div");
    block.id = "devSportsApiBenefit";
    block.className = "dev-onboarding-api-benefit";
    block.innerHTML = `
      <strong>Why use it?</strong><br>
      API-SPORTS gives M3U Web Picker a canonical game/event schedule instead of relying only on provider channel names and XMLTV. That improves game and event matching and helps merge multiple listings of the same matchup into one logical event, reducing duplicate generated channels.
      <br><a href="${API_SPORTS_URL}" target="_blank" rel="noopener noreferrer">Sign up for API-SPORTS / get an API key ↗</a>
    `;
    help.insertAdjacentElement("afterend", block);
  }

  function enhanceCurrentWizardStep() {
    void enhancePrimaryProvider();
    enhanceSportsApiInformation();
  }

  async function savePrimaryScheduleAndContinue(button) {
    const schedule = document.getElementById("devOnboardingSchedule");
    if (!schedule || schedule.dataset.providerConfigured !== "true") return false;

    const enabled = Boolean(document.getElementById("devScheduleEnabled")?.checked);
    const time = document.getElementById("devScheduleTime")?.value || "03:00";
    const timezone = document.getElementById("devScheduleTimezone")?.value || detectedTimezone();

    button.disabled = true;
    setWizardStatus("Saving automatic update schedule…");
    try {
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
      location.reload();
    } catch (error) {
      button.disabled = false;
      setWizardStatus(error.message, "error");
    }
    return true;
  }

  async function maybeStartInitialRefresh() {
    try {
      const payload = await api("/api/onboarding");
      const state = payload.state || {};
      if (!payload.enabled || !state.completed || !state.answers?.initial_refresh_pending) return;
      await api("/api/onboarding/initial-refresh", {method: "POST"});
    } catch (error) {
      console.warn("Could not start initial post-onboarding update:", error);
    }
  }

  installStyles();

  const observer = new MutationObserver(() => enhanceCurrentWizardStep());
  observer.observe(document.body, {childList: true, subtree: true});
  enhanceCurrentWizardStep();

  document.addEventListener("click", event => {
    const button = event.target.closest("#devOnboardingNext");
    if (!button || heading() !== "Primary Provider") return;
    const schedule = document.getElementById("devOnboardingSchedule");
    if (!schedule || schedule.dataset.providerConfigured !== "true") return;

    // The base wizard's configured-provider Continue handler would advance
    // immediately. Capture this click first so schedule/timezone persistence is
    // guaranteed before step 2 is shown.
    event.preventDefault();
    event.stopImmediatePropagation();
    void savePrimaryScheduleAndContinue(button);
  }, true);

  void maybeStartInitialRefresh();
})();
