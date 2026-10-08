(() => {
  'use strict';
  const button = document.getElementById('uiVpnStatus');
  if (!button) return;
  const message = document.getElementById('uiVpnMessage');
  let previous = null, busy = false, polling = false;

  function render(vpn, stale = false) {
    const enabled = Boolean(vpn?.enabled);
    button.hidden = !enabled;
    if (message) message.hidden = !enabled;
    const wanted = Boolean(vpn?.desired_on ?? vpn?.requested);
    const switching = ['pending', 'applying'].includes(vpn?.control_status);
    const active = !stale && !switching && wanted && vpn?.network_protected && vpn?.app_vpn_active;
    const off = !stale && !switching && vpn && !wanted && !vpn.network_protected;
    const label = active ? 'VPN connected' : off ? 'VPN off — normal internet' : stale ? 'VPN status unavailable' : switching ? 'Switching connection' : 'VPN enabled — disconnected';
    button.classList.toggle('is-active', Boolean(active));
    button.classList.toggle('is-warning', !active && !off);
    button.setAttribute('aria-pressed', String(wanted));
    button.setAttribute('aria-label', label + (vpn?.configured ? (wanted ? '. Turn VPN off.' : '. Turn VPN on.') : ''));
    button.title = label + '. Switching briefly interrupts playback; stop streams first.';
    button.disabled = !enabled || busy || stale || switching || !vpn?.control_available;
    if (message) {
      const ip = active ? vpn?.vpn_public_ip : '';
      message.textContent = ip || vpn?.control_error || (switching ? 'Switching…' : '');
      message.classList.toggle('is-active', Boolean(ip));
      message.title = ip ? 'VPN public IP: ' + ip : message.textContent;
    }
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/api/vpn-state', {cache: 'no-store'});
      if (!response.ok) throw new Error('Unavailable');
      const vpn = await response.json();
      render(vpn);
      const switching = ['pending', 'applying'].includes(vpn.control_status);
      const ready = !switching && (vpn.desired_on ? vpn.network_protected && vpn.app_vpn_active : !vpn.network_protected);
      if (ready && (document.body.dataset.vpnRecovery || (previous && previous.network_protected !== vpn.network_protected))) window.location.reload();
      previous = vpn;
    } catch (_) {
      render(previous, true);
    } finally { polling = false; }
  }

  button.addEventListener('click', async event => {
    event.stopPropagation();
    if (button.disabled || busy || !previous) return;
    busy = true; render(previous);
    try {
      const retry = previous.control_status === 'failed' && previous.network_protected !== previous.desired_on;
      const response = await fetch('/api/vpn-control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: retry ? previous.desired_on : !previous.desired_on})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to switch connection.');
      previous = result; render(result);
    } catch (error) {
      if (message) { message.classList.remove('is-active'); message.textContent = error.message; message.title = error.message; }
    } finally { busy = false; button.disabled = !previous.enabled || !previous.control_available || ['pending','applying'].includes(previous.control_status); }
  });
  window.refreshVpnPowerStatus = poll;
  render(null);
  poll(); window.setInterval(poll, 4000);
})();
