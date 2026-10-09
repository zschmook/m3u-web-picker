(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const status = () => document.querySelector('[data-settings-panel-content="streams"] [role="status"]');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const modes = {direct:'Direct relay',passthrough:'FFmpeg passthrough',encoding:'FFmpeg encoding'};
  let inFlight = false;
  let timer;
  function visible() {
    const panel = document.querySelector('[data-settings-panel-content="streams"]');
    const page = el('uiPage-settings');
    return !document.hidden && panel?.classList.contains('is-active') && !page?.classList.contains('ui-page-hidden');
  }
  function duration(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(total / 3600);
    return `${hours ? `${hours}:` : ''}${String(Math.floor(total / 60) % 60).padStart(2,'0')}:${String(total % 60).padStart(2,'0')}`;
  }
  function render(data) {
    const resources = data.resources || {};
    for (const [key, name] of [['cpu','Cpu'], ['gpu','Gpu'], ['ram','Ram']]) {
      const value = resources[`${key}_percent`];
      el(`uiStreams${name}`).textContent = typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}%` : (key === 'cpu' && resources.cpu_scope !== 'Unavailable' && resources.cpu_scope ? 'Sampling…' : 'Unavailable');
      el(`uiStreams${name}Scope`).textContent = key === 'gpu' && value == null ? 'GPU telemetry is not exposed to Picker.' : (resources[`${key}_scope`] || '');
    }
    const network = data.network || {};
    el('uiStreamsLan').textContent = network.lan_ip || 'Not configured';
    const vpn = el('uiStreamsVpn');
    vpn.textContent = network.vpn_ip || (network.route === 'blocked' ? 'Disconnected' : 'Off');
    vpn.classList.toggle('is-success', network.route === 'vpn');
    vpn.classList.toggle('is-warning', network.route === 'blocked');
    el('uiStreamsRoute').textContent = {vpn:'VPN',normal:'Normal internet',blocked:'Blocked — VPN unavailable'}[network.route] || 'Unknown';
    el('uiStreamsTotals').innerHTML = `<span><strong>${Number(data.observed_stream_count)||0}</strong> observed streams</span><span><strong>${Number(data.client_count)||0}</strong> client addresses/types</span><span><strong>${Number(data.ffmpeg_sessions)||0}</strong> FFmpeg sessions</span><span><strong>${Number(data.relay_requests)||0}</strong> active relay responses</span><span><strong>${Number(data.recoveries)||0}</strong> HLS recoveries</span>`;
    el('uiStreamsRows').innerHTML = (data.streams || []).map(row => `<tr>
      <td><strong>${escape(row.channel)}</strong><small>${escape(row.provider)}</small></td>
      <td>${escape(row.client_ip)}<small>${escape(row.client)}</small></td>
      <td>${escape(modes[row.mode] || 'Unknown')}<small>${escape(row.transport)}</small></td>
      <td>${duration(row.elapsed_seconds)}</td><td>${((Number(row.bytes_per_second)||0)/1e6).toFixed(2)} MB/s<small>${(Number(row.mbps)||0).toFixed(2)} Mbps</small></td>
      <td class="${row.state === 'receiving' ? 'is-success' : 'is-warning'}">${row.state === 'receiving' ? 'Receiving' : 'Waiting for data'}</td>
    </tr>`).join('') || '<tr><td colspan="6">No streams are currently being served by Picker.</td></tr>';
    el('uiStreamsClients').innerHTML = (data.clients || []).map(client => `<tr><td>${escape(client.client_ip)}<small>${escape(client.client)}</small></td><td>${Number(client.connections)||0}</td><td>${((Number(client.bytes_per_second)||0)/1e6).toFixed(2)} MB/s<small>${((Number(client.bytes_per_second)||0)*8/1e6).toFixed(2)} Mbps</small></td><td>${((Number(client.bytes_sent)||0)/1e6).toFixed(1)} MB</td></tr>`).join('') || '<tr><td colspan="4">No active client traffic.</td></tr>';
    el('uiStreamsEvents').innerHTML = (data.events || []).map(event => `<div>${escape(event.channel)} · ${escape(event.message)}<small>${escape(event.client_ip)} · ${escape(new Date(event.recorded_at * 1000).toLocaleTimeString())}</small></div>`).join('') || 'No recent activity.';
  }
  async function refresh() {
    if (!visible() || inFlight || !el('uiStreamsRows')) return;
    inFlight = true;
    el('uiStreamsRefresh').disabled = true;
    try {
      const response = await fetch('/api/streams', {cache:'no-store'});
      if (!response.ok) throw new Error('Could not load stream activity.');
      const data = await response.json();
      if (visible()) {
        render(data);
        status().textContent = `Updated ${new Date().toLocaleTimeString()} · Refreshes every 5 seconds`;
        status().classList.remove('is-error');
      }
    } catch (error) {
      status().textContent = `${error.message} Displayed activity may be out of date.`;
      status().classList.add('is-error');
    } finally {
      inFlight = false;
      el('uiStreamsRefresh').disabled = false;
    }
  }
  function schedule() {
    clearInterval(timer);
    if (visible()) {void refresh(); timer = setInterval(refresh, 5000);}
  }
  el('uiStreamsRefresh')?.addEventListener('click', refresh);
  window.addEventListener('ui:settings-panel', schedule);
  window.addEventListener('ui:page', schedule);
  window.addEventListener('hashchange', schedule);
  document.addEventListener('visibilitychange', schedule);
  schedule();
})();
