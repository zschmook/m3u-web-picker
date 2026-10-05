const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const root = path.resolve(__dirname, '..');
const vlc = fs.readFileSync(path.join(root, 'static/js/guide_vlc.js'), 'utf8');
const guide = fs.readFileSync(path.join(root, 'static/js/guide.js'), 'utf8');

function setup({ua = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/140 Mobile', hints,
  preference, search = '', storageBlocked = false} = {}) {
  const calls = [];
  const elements = new Map();
  const context = vm.createContext({
    URL, console, navigator: {userAgent: ua, userAgentData: hints},
    document: {getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        value: 'vlc', textContent: '', classList: {remove() {}},
        addEventListener(event, fn) { this[event] = fn; },
      });
      return elements.get(id);
    }},
    location: {href: `http://192.0.2.1:9998/guide${search}`, origin: 'http://192.0.2.1:9998',
      assign(url) { calls.push(['launch', url]); }},
    localStorage: {
      getItem() { if (storageBlocked) throw Error('blocked'); return preference; },
      setItem() { if (storageBlocked) throw Error('blocked'); },
    },
    guideState: {roku: {active: false}, currentChannel: {}, mode: 'local'},
    currentCastSession: () => null,
    stopLocalStream() { calls.push('stop-local'); }, stopListenStream() { calls.push('stop-listen'); },
    clearRestartPlayback() {}, renderGuide() {},
    startRokuChannel() { calls.push('roku'); }, castChannel() { calls.push('cast'); },
    playLocalChannel() { calls.push('browser'); },
  });
  context.window = context;
  vm.runInContext(vlc, context);
  vm.runInContext(guide.slice(guide.indexOf('async function playChannel('),
    guide.indexOf('async function toggleCast(')), context);
  return {context, calls, elements};
}

test('Pixel Chrome defaults to VLC and hands off the absolute stream URL synchronously', async () => {
  const {context, calls} = setup();
  assert.equal(context.guidePlayLabel(false), 'Play in VLC');
  const result = context.playChannel({play_url: '/guide/play/manual/token?x=1&y=2'});
  assert.equal(calls[2][0], 'launch');
  assert.match(calls[2][1], /^intent:\/\/192\.0\.2\.1:9998\/guide\/play\/manual\/token\?x=1&y=2#Intent;/);
  assert.match(calls[2][1], /package=org\.videolan\.vlc;/);
  assert.match(calls[2][1], /type=video\/\*;/);
  assert.match(decodeURIComponent(calls[2][1]), /browser_fallback_url=http:\/\/192\.0\.2\.1:9998\/guide\?player=browser/);
  assert.equal(context.guideState.currentChannel, null);
  await result;
});

for (const ua of ['Mozilla/5.0 (Windows NT 10.0) Chrome/140',
  'Mozilla/5.0 (iPhone) Mobile Safari', 'Mozilla/5.0 (Linux; Android 14) Chrome/140 Safari']) {
  test(`keeps browser playback for ${ua}`, async () => {
    const {context, calls} = setup({ua});
    assert.equal(context.guidePlayLabel(false), 'Play');
    await context.playChannel({play_url: '/guide/play/manual/token'});
    assert.deepEqual(calls, ['browser']);
  });
}

test('Android mobile client hints work with a reduced user agent', () => {
  const {context} = setup({ua: '', hints: {platform: 'Android', mobile: true}});
  assert.equal(context.guidePrefersVlc(), true);
});

for (const options of [{preference: 'browser'}, {search: '?player=browser'}]) {
  test(`browser choice or failed-launch fallback bypasses VLC: ${JSON.stringify(options)}`, async () => {
    const {context, calls} = setup(options);
    await context.playChannel({play_url: '/guide/play/manual/token'});
    assert.deepEqual(calls, ['browser']);
  });
}

test('blocked storage does not break phone controls', () => {
  const {context, elements} = setup({storageBlocked: true});
  const select = elements.get('guidePhonePlayer');
  select.value = 'browser';
  select.change();
  assert.equal(context.guidePrefersVlc(), false);
});

for (const destination of ['roku', 'cast']) {
  test(`explicit remote ${destination} session retains control`, async () => {
    const {context, calls} = setup();
    if (destination === 'roku') context.guideState.roku.active = true;
    else context.currentCastSession = () => ({});
    assert.equal(context.guidePlayLabel(false), 'Play');
    await context.playChannel({play_url: '/guide/play/manual/token'});
    assert.deepEqual(calls, [destination]);
  });
}

test('unavailable channels never launch VLC', async () => {
  const {context, calls} = setup();
  await context.playChannel({available: false, play_url: '/guide/play/manual/token'});
  assert.deepEqual(calls, []);
});

test('intent rejects external and non-HTTP URLs and prevents injected intent fields', () => {
  const {context} = setup();
  for (const url of ['', 'javascript:alert(1)', 'https://other.example/stream', '//other.example/stream']) {
    assert.throws(() => context.guideVlcIntent(url));
  }
  const intent = context.guideVlcIntent('/guide/play/manual/token?value=one;package=bad#Intent;package=bad;end');
  assert.equal((intent.match(/#Intent;/g) || []).length, 1);
  assert.equal(intent.includes(';package=bad'), false);
});
