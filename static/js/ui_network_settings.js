(() => {
  "use strict";
  const el = id => document.getElementById(id);
  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  }
  function status(message, kind = "") {
    const node = el("uiNetworkStatus");
    if (!node) return;
    node.textContent = message;
    node.className = `ui-settings-status${kind ? ` is-${kind}` : ""}`;
  }
  function render(data) {
    el("uiNetworkPort").value = data.external_port || 9999;
    el("uiNetworkAddress").textContent = data.lan_host
      ? `http://${data.lan_host}:${data.external_port}`
      : "LAN host is not configured";
  }
  async function load() {
    if (!el("uiNetworkSave")) return;
    try { render(await api("/api/network-config")); }
    catch (error) { status(error.message, "error"); }
  }
  async function save() {
    el("uiNetworkSave").disabled = true;
    status("Saving public URL port…");
    try {
      const data = await api("/api/network-config", {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({external_port: Number(el("uiNetworkPort").value)}),
      });
      render(data);
      status("Port saved. Run Master Update to rewrite existing generated sports logo URLs.", "success");
    } catch (error) {
      status(error.message, "error");
    } finally {
      el("uiNetworkSave").disabled = false;
    }
  }
  el("uiNetworkSave")?.addEventListener("click", save);
  void load();
})();
