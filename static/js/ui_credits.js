(() => {
  'use strict';
  let loaded = false, pending = false;
  const element = (tag, text) => {const node = document.createElement(tag); if (text) node.textContent = text; return node;};
  function link(url, label) {
    if (typeof url !== 'string' || !(url.startsWith('/static/licenses/') || /^https:\/\//.test(url))) return element('span', label);
    const node = element('a', label); node.href = url;
    if (url.startsWith('https://')) {node.target = '_blank'; node.rel = 'noopener noreferrer';}
    return node;
  }
  async function load() {
    if (loaded || pending || !document.querySelector('[data-settings-panel-content="credits"].is-active')) return;
    pending = true;
    const root = document.getElementById('uiCreditsInventory'), status = document.getElementById('uiCreditsStatus');
    try {
      const response = await fetch('/static/licenses/generated/index.json');
      if (!response.ok) throw new Error('Installed inventory is generated during Docker builds. It is not available in this installation.');
      const data = await response.json(), groups = new Map();
      for (const row of data.entries || []) {
        if (!groups.has(row.group)) groups.set(row.group, []);
        groups.get(row.group).push(row);
      }
      const content = element('div'); content.className = 'ui-credits-list';
      for (const [name, rows] of groups) {
        const group = element('details'); group.append(element('summary', `${name} (${rows.length})`));
        for (const row of rows) {
          const item = element('p'); item.append(element('strong', `${row.name} ${row.version}`), element('span', ` — ${row.license}. `));
          item.append(link(row.website, 'Project'), element('span', ' · '), link(row.source, 'Source'));
          for (const [index, notice] of (row.notices || []).entries()) item.append(element('span', ' · '), link(notice, `Notice ${index + 1}`));
          group.append(item);
        }
        content.append(group);
      }
      const common = element('details'); common.append(element('summary', 'Shared license texts'));
      for (const url of data.common_licenses || []) {const item = element('p'); item.append(link(url, url.split('/').pop().replace('.txt',''))); common.append(item);}
      content.append(common); root.replaceChildren(content);
      status.textContent = `${data.entries.length} installed components. Full copyright and license texts are included in the download.`;
      loaded = true;
    } catch (error) {status.textContent = error.message;}
    finally {pending = false;}
  }
  window.addEventListener('ui:settings-panel', load);
  window.addEventListener('ui:page', load);
  load();
})();
