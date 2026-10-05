(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const form = el("uiLightingForm");
  if (!form) return;
  let knownLights = [];
  let knownRokus = [];
  let savedSettings = null;
  const status = (message, error = false) => {
    el("uiLightingStatus").textContent = message;
    el("uiLightingStatus").className = `ui-settings-status is-${error ? "error" : "success"}`;
  };
  const render = data => {
    savedSettings = data;
    el("uiLightingEnabled").checked = Boolean(data.enabled);
    el("uiLightingPlaying").value = data.brightness;
    el("uiLightingPaused").value = data.paused_brightness;
    el("uiLightingFade").value = data.transition_ms / 1000;
    renderChoices();
  };
  function options(id, values, selected, placeholder) {
    const select = el(id);
    select.replaceChildren(new Option(placeholder, ""));
    values.forEach(value => select.add(new Option(value.label, value.key)));
    select.value = selected || "";
  }
  function renderChoices() {
    if (!savedSettings) return;
    options("uiLightingLight", knownLights.map(light => ({key: light.device_id, label: light.name})),
      savedSettings.target?.device_id, "Choose a light");
    options("uiLightingRoku", knownRokus.map(roku => ({key: roku.host,
      label: roku.host === savedSettings.roku_host ? savedSettings.roku_name || roku.name : roku.name})),
      savedSettings.roku_host, "Choose a Roku");
  }
  async function request(options = {}, path = "/api/movie-lighting") {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not save lighting settings.");
    return data;
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const light = knownLights.find(value => value.device_id === el("uiLightingLight").value);
    const roku = knownRokus.find(value => value.host === el("uiLightingRoku").value);
    if (!light || !roku) { status("Choose an identified light and a Roku for this room.", true); return; }
    el("uiLightingSave").disabled = true;
    status("Saving lighting settings…");
    try {
      render(await request({method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({
        enabled: el("uiLightingEnabled").checked,
        brightness: Number(el("uiLightingPlaying").value),
        paused_brightness: Number(el("uiLightingPaused").value),
        transition_ms: Math.round(Number(el("uiLightingFade").value) * 1000),
        target: light,
        roku_host: roku.host,
        roku_name: roku.host === savedSettings.roku_host ? savedSettings.roku_name || roku.name : roku.name,
      })}));
      status("Saved. Brightness changes apply on play or pause; a different light applies to the next movie.");
    } catch (error) { status(error.message, true); }
    finally { el("uiLightingSave").disabled = false; }
  });
  el("uiLightingRefresh").addEventListener("click", async () => {
    const selected = el("uiLightingLight").value;
    const selectedRoku = el("uiLightingRoku").value;
    el("uiLightingRefresh").disabled = true;
    status("Reading light names…");
    try {
      const data = await request({method: "POST"}, "/api/movie-lighting/lights/refresh");
      knownLights = data.lights;
      renderChoices();
      el("uiLightingLight").value = selected;
      el("uiLightingRoku").value = selectedRoku;
      status(`Updated names from ${data.responding} lights. No lights were changed.`);
    } catch (error) { status(error.message, true); }
    finally { el("uiLightingRefresh").disabled = false; }
  });
  el("uiLightingSave").disabled = true;
  Promise.all([request(), request({}, "/api/movie-lighting/lights"), request({}, "/api/guide/roku/devices")])
    .then(([settings, lights, rokus]) => {
      knownLights = lights.lights;
      knownRokus = rokus.devices;
      render(settings);
      el("uiLightingSave").disabled = false;
    })
    .catch(error => status(error.message, true));
})();
