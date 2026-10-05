const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const root = path.resolve(__dirname, '..');
const guide = fs.readFileSync(path.join(root, 'static/js/guide.js'), 'utf8');
const multi = fs.readFileSync(path.join(root, 'static/js/guide_multi_roku.js'), 'utf8');

function guideFunction(name, next) {
  return guide.slice(guide.indexOf(`async function ${name}(`), guide.indexOf(`function ${next}(`));
}

for (const mode of ['local', 'listen', 'stopped', 'cast', 'roku']) {
  test(`Stop in ${mode} mode only controls its playback destination`, async () => {
    const calls = [];
    const context = vm.createContext({
      guideState: {mode, currentChannel: {}},
      guideEls: {playerPanel: {classList: {add() {}}}, playerMessage: {}, playerMeta: {}},
      stopLocalStream() { calls.push('local'); },
      stopListenStream() { calls.push('listen'); },
      stopRemoteMedia() { calls.push('cast'); },
      stopCastRelay() { calls.push('cast-relay'); },
      stopRokuPlayback() { calls.push('roku'); },
      clearRestartPlayback() {}, showLocalPlayer() {}, renderGuide() {},
    });
    vm.runInContext(guideFunction('stopPlayback', 'setCurrentChannel'), context);
    await context.stopPlayback();
    const remote = mode === 'cast' ? ['cast', 'cast-relay'] : mode === 'roku' ? ['roku'] : [];
    assert.deepEqual(calls, ['local', 'listen', ...remote]);
    assert.equal(context.guideState.currentChannel, null);
  });
}

function baseRoku(active, token = '') {
  const requests = [];
  const context = vm.createContext({
    guideState: {roku: {active, relayToken: token, host: '192.0.2.10'}},
    configuredRokuHost: () => '192.0.2.10', updateRokuControls() {}, console,
    fetch: async (url, options) => { requests.push(JSON.parse(options.body)); },
  });
  vm.runInContext(guideFunction('stopRokuPlayback', 'showRokuPlayer'), context);
  return {context, requests};
}

test('base Roku stop ignores a remembered TV with no guide session', async () => {
  const {context, requests} = baseRoku(false);
  await context.stopRokuPlayback();
  assert.deepEqual(requests, []);
});

test('base Roku direct playback can stop without a relay token', async () => {
  const {context, requests} = baseRoku(true);
  await context.stopRokuPlayback();
  assert.deepEqual(requests, [{token: '', roku_host: '192.0.2.10'}]);
});

async function multiRoku() {
  const requests = [];
  const element = () => ({value: '', options: [], addEventListener() {}});
  const select = element();
  select.value = '192.0.2.10';
  const context = vm.createContext({
    document: {getElementById: () => select, createElement: element},
    guideState: {roku: {}, currentChannel: null},
    guideEls: {rokuHost: {value: select.value}, rokuBtn: {}, rokuStatus: {}},
    configuredRokuHost: () => select.value,
    localStorage: {getItem() { return ''; }, setItem() {}},
    MutationObserver: class { observe() {} }, setTimeout() {}, console,
    fetch: async (url, options) => {
      if (url === '/api/guide/roku/stop') requests.push(JSON.parse(options.body));
      return {ok: true, json: async () => ({ok: true, devices: []})};
    },
  });
  context.window = context;
  vm.runInContext(multi, context);
  await new Promise(resolve => setImmediate(resolve));
  return {context, requests};
}

test('multi Roku stop ignores a selected TV without a guide session', async () => {
  const {context, requests} = await multiRoku();
  await context.stopRokuPlayback();
  assert.deepEqual(requests, []);
});

test('multi Roku explicit stop targets the session TV, not the selected TV', async () => {
  const {context, requests} = await multiRoku();
  const sessions = context.guideState.roku.sessions;
  sessions.set('other', {deviceKey: 'other', host: '192.0.2.20', token: ''});
  await context.stopRokuPlayback({deviceKey: 'missing'});
  assert.deepEqual(requests, []);
  await context.stopRokuPlayback({deviceKey: 'other'});
  assert.deepEqual(requests, [{token: '', roku_host: '192.0.2.20', roku_device_key: ''}]);
  assert.equal(sessions.has('other'), false);
});

test('multi Roku relay cleanup does not send Home when disabled', async () => {
  const {context, requests} = await multiRoku();
  context.guideState.roku.sessions.set('host:192.0.2.10', {
    deviceKey: 'host:192.0.2.10', host: '192.0.2.10', token: 'relay-token',
  });
  await context.stopRokuPlayback({sendHome: false});
  assert.deepEqual(requests, [{token: 'relay-token', roku_host: '', roku_device_key: ''}]);
});
