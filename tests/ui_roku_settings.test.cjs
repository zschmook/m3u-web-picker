const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../static/js/ui_roku_settings.js'), 'utf8');

function setup(respond) {
  class Element {
    constructor() { this.children = []; this.events = {}; this.value = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, action) { this.events[name] = action; }
  }
  const elements = new Map();
  for (const id of ['uiRokuDiscover', 'uiRokuAdd', 'uiRokuHost', 'uiRokuAddForm',
    'uiRokuDiscoveryStatus', 'uiRokuDiscoveredList']) elements.set(id, new Element());
  const calls = [], changes = [];
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id), createElement: () => new Element()},
    fetch: async (url, options) => {
      calls.push({url, options});
      const data = await respond(url, options);
      return {ok: !data.error, status: data.error ? 502 : 200, json: async () => data};
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: {dispatchEvent(event) { changes.push(event); }},
  });
  vm.runInContext(script, context);
  return {elements, calls, changes,
    discover: () => elements.get('uiRokuDiscover').events.click(),
    add: async host => {
      elements.get('uiRokuHost').value = host;
      elements.get('uiRokuAddForm').events.submit({preventDefault() {}});
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

const living = {name: 'Living Room', host: '192.0.2.41', device_key: 'device:A', model: 'Roku TV'};
const bedroom = {name: 'Bedroom', host: '192.0.2.42', device_key: 'device:B', model: 'Roku Ultra'};
const found = {ok: true, devices: [living, bedroom], saved_devices: [living], subnet: '192.0.2.0/24'};

test('discovery only runs on demand and saved devices cannot be added again', async () => {
  const ui = setup(() => found);
  assert.equal(ui.calls.length, 0);
  await ui.discover();
  const rows = ui.elements.get('uiRokuDiscoveredList').children;
  assert.equal(rows[0].children[1].textContent, 'Saved');
  assert.equal(rows[0].children[1].disabled, true);
  assert.equal(rows[1].children[1].disabled, false);
  assert.match(ui.elements.get('uiRokuDiscoveryStatus').textContent, /Found 2 Rokus/);
});

test('saving a discovery refreshes the complete saved list and disables its add button', async () => {
  const ui = setup((url, options) => options.method === 'POST' ? {ok: true, device: bedroom}
    : url.endsWith('/discover') ? found : {ok: true, devices: [living, bedroom]});
  await ui.discover();
  await ui.elements.get('uiRokuDiscoveredList').children[1].children[1].events.click();
  const request = ui.calls.find(call => call.options.method === 'POST');
  assert.deepEqual(JSON.parse(request.options.body), {roku_host: bedroom.host});
  assert.equal(ui.changes.at(-1).detail.devices.length, 2);
  assert.equal(ui.elements.get('uiRokuDiscoveredList').children[1].children[1].textContent, 'Saved');
});

test('missing LAN configuration still allows a manual IP save', async () => {
  const ui = setup((url, options) => options.method === 'POST' ? {ok: true, device: bedroom}
    : url.endsWith('/discover') ? {ok: true, devices: [], saved_devices: [], subnet: ''}
      : {ok: true, devices: [bedroom]});
  await ui.discover();
  assert.match(ui.elements.get('uiRokuDiscoveryStatus').textContent, /LAN address/);
  assert.equal(ui.elements.get('uiRokuAdd').disabled, false);
  await ui.add(` ${bedroom.host} `);
  assert.deepEqual(JSON.parse(ui.calls.find(call => call.options.method === 'POST').options.body),
    {roku_host: bedroom.host});
  assert.equal(ui.elements.get('uiRokuHost').value, '');
});

test('a failed save keeps the IP for correction and permits another attempt', async () => {
  const ui = setup(() => ({error: 'Not a Roku device.'}));
  await ui.add('192.0.2.99');
  assert.equal(ui.elements.get('uiRokuHost').value, '192.0.2.99');
  assert.equal(ui.elements.get('uiRokuAdd').disabled, false);
  assert.equal(ui.changes.length, 0);
  assert.match(ui.elements.get('uiRokuDiscoveryStatus').textContent, /Not a Roku/);
});

test('a failed rescan clears stale results and restores discovery controls', async () => {
  let scans = 0;
  const ui = setup(() => ++scans === 1 ? found : Promise.reject(Error('Network unavailable.')));
  await ui.discover();
  await ui.discover();
  assert.equal(ui.elements.get('uiRokuDiscoveredList').hidden, true);
  assert.equal(ui.elements.get('uiRokuDiscover').disabled, false);
  assert.match(ui.elements.get('uiRokuDiscoveryStatus').textContent, /Network unavailable/);
});

test('pending scans prevent duplicate discovery and save requests', async () => {
  let finish;
  const ui = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.discover();
  await ui.discover();
  await ui.add(bedroom.host);
  assert.equal(ui.calls.length, 1);
  finish(found);
  await pending;
  assert.equal(ui.elements.get('uiRokuAdd').disabled, false);
});

test('Roku names are rendered as text and cannot inject HTML', async () => {
  const hostileName = '<img src=x onerror=alert(1)>';
  const ui = setup(() => ({...found, devices: [{...bedroom, name: hostileName}]}));
  await ui.discover();
  const name = ui.elements.get('uiRokuDiscoveredList').children[0].children[0].children[0];
  assert.equal(name.textContent, hostileName);
  assert.equal(name.innerHTML, undefined);
});

test('a confirmed save remains visible if refreshing the saved list fails', async () => {
  const ui = setup((url, options) => options.method === 'POST' ? {ok: true, device: bedroom}
    : Promise.reject(Error('Status unavailable.')));
  await ui.add(bedroom.host);
  assert.equal(ui.changes.at(-1).detail.devices[0].device_key, bedroom.device_key);
  assert.match(ui.elements.get('uiRokuDiscoveryStatus').textContent, /Saved Bedroom/);
});
