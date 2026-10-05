const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/js/ui_movie_lighting_settings.js'), 'utf8');
const living = {host: '192.0.2.41', name: 'Living Room', device_key: 'device:A'};
const bedroom = {host: '192.0.2.42', name: 'Bedroom', device_key: 'device:B'};
const shelf = {ip: '192.0.2.7', name: 'Shelf', model: 'KL110', device_id: 'light:A'};
const desk = {ip: '192.0.2.8', name: 'Desk', model: 'KL110', device_id: 'light:B'};
const room = {id: 'legacy', name: 'Room 1', enabled: true, brightness: 10, paused_brightness: 100,
  transition_ms: 2000, targets: [shelf], roku_host: living.host, roku_name: living.name};
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(respond = () => undefined) {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.events = {}; this.checked = false; }
    addEventListener(type, callback) { this.events[type] = callback; }
    replaceChildren(...children) { this.children = children; this.value = ''; }
    append(...children) { this.children.push(...children); }
    add(option) { this.children.push(option); }
    reportValidity() { return true; }
    querySelectorAll() { return this.children.flatMap(label => label.children).filter(input => input.tag === 'input' && input.checked); }
  }
  class Select extends Element {
    get value() { return this.selected || ''; }
    set value(value) { this.selected = this.children.some(option => option.value === value) ? value : ''; }
  }
  const ids = ['uiLightingForm', 'uiLightingStatus', 'uiLightingEnabled', 'uiLightingPlaying',
    'uiLightingPaused', 'uiLightingFade', 'uiLightingSave', 'uiLightingDiscover', 'uiLightingRefresh',
    'uiLightingAddRoom', 'uiLightingDeleteRoom', 'uiLightingRoomName', 'uiLightingEditor', 'uiLightingLights'];
  const elements = new Map(ids.map(id => [id, new Element()]));
  elements.set('uiLightingRoku', new Select()); elements.set('uiLightingRoom', new Select());
  const tab = new Element(), listeners = new Map(), calls = [], confirms = [];
  const context = vm.createContext({
    document: {getElementById: id => elements.get(id), querySelector: () => tab, createElement: tag => new Element(tag)},
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    window: {addEventListener(type, callback) { listeners.set(type, callback); }, confirm(text) { confirms.push(text); return true; }},
    fetch: async (url, options) => {
      calls.push({url, options});
      const custom = await respond(url, options);
      const data = custom ?? (url === '/api/movie-lighting' ? {rooms: []}
        : url === '/api/movie-lighting/lights' ? {lights: [shelf]}
          : options.method === 'POST' || options.method === 'PATCH' ? {...JSON.parse(options.body), id: 'saved'}
            : {ok: true, devices: [living]});
      return {ok: !data.error, json: async () => structuredClone(data)};
    },
  });
  vm.runInContext(source, context);
  return {elements, calls, confirms, ready: tick,
    inputs: () => elements.get('uiLightingLights').children.flatMap(label => label.children).filter(input => input.tag === 'input'),
    choose(...ids) { this.inputs().forEach(input => { input.checked = ids.includes(input.value); }); },
    changeRokus(devices, discovered) { listeners.get('ui:roku-devices-changed')({detail: {devices, discovered}}); },
    open: () => tab.events.click(), discoverLights: () => elements.get('uiLightingDiscover').events.click(),
    submit: () => elements.get('uiLightingForm').events.submit({preventDefault() {}}),
    add: () => elements.get('uiLightingAddRoom').events.click(),
    delete: () => elements.get('uiLightingDeleteRoom').events.click(),
    switch(id) { elements.get('uiLightingRoom').value = id; elements.get('uiLightingRoom').events.change(); },
  };
}
test('loading creates a disabled draft without automatic scans or saves', async () => {
  const ui = setup(); await ui.ready();
  assert.equal(ui.elements.get('uiLightingRoomName').value, 'Room 1');
  assert.equal(ui.elements.get('uiLightingEnabled').checked, false);
  assert.ok(ui.calls.every(call => !call.options.method));
});
test('existing pairing appears as a named room with the same light and levels', async () => {
  const ui = setup(url => url === '/api/movie-lighting' ? {rooms: [room]} : undefined); await ui.ready();
  assert.equal(ui.elements.get('uiLightingRoomName').value, 'Room 1');
  assert.equal(ui.elements.get('uiLightingRoku').value, living.host);
  assert.equal(ui.inputs()[0].checked, true);
  assert.equal(ui.elements.get('uiLightingEnabled').checked, true);
});
test('saved and discovered Rokus populate choices immediately without saving', async () => {
  const ui = setup(); await ui.ready(); ui.changeRokus([living], [living, bedroom]);
  assert.deepEqual(ui.elements.get('uiLightingRoku').children.map(option => option.text), ['Choose a Roku', 'Living Room', 'Bedroom']);
  assert.ok(ui.calls.every(call => !call.options.method));
});
test('device updates preserve unsaved group choices, levels, and enabled state', async () => {
  const ui = setup(); await ui.ready(); ui.elements.get('uiLightingRoku').value = living.host;
  ui.choose(shelf.device_id); ui.elements.get('uiLightingPlaying').value = '25'; ui.elements.get('uiLightingEnabled').checked = true;
  ui.changeRokus([living, bedroom], [living, bedroom]);
  assert.equal(ui.elements.get('uiLightingRoku').value, living.host);
  assert.equal(ui.inputs()[0].checked, true); assert.equal(ui.elements.get('uiLightingPlaying').value, '25');
  assert.equal(ui.elements.get('uiLightingEnabled').checked, true);
});
test('Roku choice follows stable identity when discovery changes its IP', async () => {
  const ui = setup(); await ui.ready(); ui.elements.get('uiLightingRoku').value = living.host;
  ui.changeRokus([{...living, host: '192.0.2.50'}], []);
  assert.equal(ui.elements.get('uiLightingRoku').value, '192.0.2.50');
});
test('discovery during initial loading beats an older saved-device response', async () => {
  let resolveDevices; const pending = new Promise(resolve => { resolveDevices = resolve; });
  const ui = setup(url => url.endsWith('/roku/devices') ? pending : undefined);
  ui.changeRokus([bedroom], []); resolveDevices({devices: [living]}); await ui.ready();
  assert.deepEqual(ui.elements.get('uiLightingRoku').children.map(option => option.value), ['', bedroom.host]);
});
test('an older tab refresh cannot overwrite newer discovery results', async () => {
  let readCount = 0, resolveRefresh;
  const ui = setup(url => !url.endsWith('/roku/devices') ? undefined
    : ++readCount === 1 ? {devices: [living]} : new Promise(resolve => { resolveRefresh = resolve; }));
  await ui.ready(); const opening = ui.open(); ui.changeRokus([bedroom], []);
  resolveRefresh({devices: [living]}); await opening;
  assert.deepEqual(ui.elements.get('uiLightingRoku').children.map(option => option.value), ['', bedroom.host]);
});
test('light discovery keeps checked lights and current room edits without saving', async () => {
  const ui = setup(url => url.endsWith('/lights/discover') ? {lights: [shelf, desk], responding: 2} : undefined);
  await ui.ready(); ui.choose(shelf.device_id); ui.elements.get('uiLightingRoku').value = living.host;
  ui.elements.get('uiLightingPlaying').value = '20'; await ui.discoverLights();
  assert.equal(ui.inputs().length, 2); assert.equal(ui.inputs()[0].checked, true);
  assert.equal(ui.elements.get('uiLightingRoku').value, living.host);
  assert.equal(ui.elements.get('uiLightingPlaying').value, '20');
  assert.equal(ui.calls.filter(call => call.options.method === 'PATCH').length, 0);
});
test('light scan blocks duplicate scans, room changes and saves until completed', async () => {
  let resolveScan; const ui = setup(url => url.endsWith('/lights/discover') ? new Promise(resolve => { resolveScan = resolve; }) : undefined);
  await ui.ready(); const scan = ui.discoverLights();
  assert.equal(ui.elements.get('uiLightingSave').disabled, true); assert.equal(ui.elements.get('uiLightingRoom').disabled, true);
  await ui.discoverLights(); await ui.submit(); ui.add();
  resolveScan({lights: [shelf], responding: 1}); await scan;
  assert.equal(ui.calls.filter(call => call.url.endsWith('/lights/discover')).length, 1);
  assert.equal(ui.calls.filter(call => call.options.method === 'PATCH').length, 0);
  assert.equal(ui.elements.get('uiLightingSave').disabled, false);
});
test('a failed light scan keeps choices and allows retry', async () => {
  const ui = setup(url => url.endsWith('/lights/discover') ? {error: 'LAN address is required.'} : undefined);
  await ui.ready(); ui.choose(shelf.device_id); await ui.discoverLights();
  assert.equal(ui.inputs()[0].checked, true); assert.equal(ui.elements.get('uiLightingDiscover').disabled, false);
  assert.match(ui.elements.get('uiLightingStatus').textContent, /LAN address is required/);
});
test('explicit submission saves one Roku and several lights to a named room', async () => {
  const ui = setup(url => url.endsWith('/lights/discover') ? {lights: [shelf, desk], responding: 2} : undefined);
  await ui.ready(); ui.changeRokus([], [bedroom]); await ui.discoverLights();
  ui.elements.get('uiLightingRoku').value = bedroom.host; ui.choose(shelf.device_id, desk.device_id);
  ui.elements.get('uiLightingRoomName').value = 'Den'; ui.elements.get('uiLightingPlaying').value = '25';
  await ui.submit(); const call = ui.calls.find(call => call.url === '/api/movie-lighting/rooms');
  const saved = JSON.parse(call.options.body);
  assert.equal(call.options.method, 'POST'); assert.equal(saved.roku_host, bedroom.host);
  assert.deepEqual(saved.targets, [shelf, desk]); assert.equal(saved.name, 'Den'); assert.equal(saved.brightness, 25);
  assert.equal(ui.elements.get('uiLightingRoom').value, 'saved');
});
test('switching rooms retains separate unsaved edits and prevents shared light selection', async () => {
  const second = {...room, id: 'bedroom', name: 'Bedroom', targets: [desk], roku_host: bedroom.host, brightness: 30};
  const ui = setup(url => url === '/api/movie-lighting' ? {rooms: [room, second]}
    : url === '/api/movie-lighting/lights' ? {lights: [shelf, desk]} : undefined);
  await ui.ready(); ui.elements.get('uiLightingPlaying').value = '22';
  assert.equal(ui.inputs()[1].disabled, true);
  ui.switch('bedroom'); assert.equal(ui.elements.get('uiLightingPlaying').value, 30);
  ui.elements.get('uiLightingPlaying').value = '35'; ui.switch('legacy');
  assert.equal(ui.elements.get('uiLightingPlaying').value, 22); assert.equal(ui.inputs()[0].checked, true);
  ui.switch('bedroom'); assert.equal(ui.elements.get('uiLightingPlaying').value, 35);
  assert.ok(ui.calls.every(call => !call.options.method));
});
test('saving an existing room uses its own endpoint and preserves another room', async () => {
  const second = {...room, id: 'bedroom', name: 'Bedroom', targets: [desk], roku_host: bedroom.host, brightness: 30};
  const ui = setup(url => url === '/api/movie-lighting' ? {rooms: [room, second]}
    : url === '/api/movie-lighting/lights' ? {lights: [shelf, desk]} : undefined);
  await ui.ready(); ui.elements.get('uiLightingPlaying').value = '22'; await ui.submit();
  const call = ui.calls.find(call => call.options.method === 'PATCH');
  assert.equal(call.url, '/api/movie-lighting/rooms/legacy');
  ui.switch('bedroom'); assert.equal(ui.elements.get('uiLightingPlaying').value, 30);
});
test('delete requires confirmation and removes only the selected saved room', async () => {
  const ui = setup(url => url === '/api/movie-lighting' ? {rooms: [room]} : undefined); await ui.ready(); await ui.delete();
  assert.match(ui.confirms[0], /Room 1/);
  assert.equal(ui.calls.find(call => call.options.method === 'DELETE').url, '/api/movie-lighting/rooms/legacy');
  assert.equal(ui.elements.get('uiLightingEnabled').checked, false);
  assert.equal(ui.elements.get('uiLightingRoomName').value, 'Room 1');
});
test('enabled room cannot save without a Roku and at least one light', async () => {
  const ui = setup(); await ui.ready(); ui.elements.get('uiLightingEnabled').checked = true; await ui.submit();
  assert.ok(ui.calls.every(call => !call.options.method));
  assert.match(ui.elements.get('uiLightingStatus').textContent, /at least one light/);
});
