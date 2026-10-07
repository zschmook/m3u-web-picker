const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/js/guide.js'), 'utf8');

function setup(channels, session) {
  const storage = new Map([['session', JSON.stringify(session)]]), starts = [];
  const context = vm.createContext({
    guideState: {channels, currentChannel: channels[0], listen: {restoreAttempted:false, active:false, heartbeatTimer:null}},
    GUIDE_LISTEN_SESSION_KEY:'session', GUIDE_LISTEN_SESSION_MAX_AGE_MS:120000, guideListenClientId:'new-tab',
    localStorage: {getItem:key=>storage.get(key), setItem:(key,value)=>storage.set(key,value), removeItem:key=>storage.delete(key)},
    startListenMode:channel=>starts.push(channel), Date,
  });
  for (const [name, next] of [['storedListenSession','stopListenHeartbeat'],
    ['rememberListenSession','forgetOwnedListenSession'], ['restoreListenSession','playLocalChannel']]) {
    vm.runInContext(source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`)), context);
  }
  return {context, storage, starts};
}
const cached = () => ({owner:'old-tab', updated_at:Date.now(),
  channel:{play_url:'/guide/play/manual/alpha', name:'Old name', group:'Old group', logo:'old-logo'}});

test('listen handoff uses current server metadata instead of a cached channel snapshot', () => {
  const current={play_url:'/guide/play/manual/alpha', name:'Current name', group:'Current group', logo:'current-logo'};
  const ui=setup([current], cached());
  ui.context.restoreListenSession();
  assert.equal(ui.starts[0], current);
});

for (const channels of [[], [{play_url:'/guide/play/manual/bravo'}],
  [{play_url:'/guide/play/manual/alpha', available:false}]]) {
  test(`listen handoff cannot resurrect a removed or unavailable channel: ${JSON.stringify(channels)}`, () => {
    const ui=setup(channels, cached());
    ui.context.restoreListenSession();
    assert.deepEqual(ui.starts, []);
    assert.equal(ui.storage.has('session'), false);
  });
}

test('listen storage contains a playback reference without copying authoritative metadata', () => {
  const current={play_url:'/guide/play/manual/alpha', name:'Current name', group:'Current group', logo:'current-logo'};
  const ui=setup([current], cached());
  ui.context.guideState.listen.active=true;
  ui.context.rememberListenSession(current, {startHeartbeat:false, forceTakeover:true});
  const saved=JSON.parse(ui.storage.get('session'));
  assert.deepEqual(saved.channel, {play_url:current.play_url});
  assert.equal(saved.owner, 'new-tab');
});

test('Guide follows another device\'s server lineup changes without changing playback', async () => {
  const calls=[];
  const context=vm.createContext({guideLoadInFlight:false,guideStatusInFlight:false,document:{hidden:false},
    guideState:{channels:[{name:'Old'}],lineupRevision:'old',mode:'local',listen:{active:true}},
    guideEls:{status:{}},renderGuide(){},restoreListenSession(){},
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url==='/api/ui/status' ?
      {lineup_revision:'current',update:{stale:false}} : {lineup_revision:'current',channels:[{name:'Current'}]}};}});
  vm.runInContext(source.slice(source.indexOf('async function loadGuide('),source.indexOf('function isLoopbackHost(')),context);
  await context.synchronizeGuideState();
  await context.synchronizeGuideState();
  assert.deepEqual(calls,['/api/ui/status','/api/guide/channels','/api/ui/status']);
  assert.equal(context.guideState.channels[0].name,'Current');
  assert.equal(context.guideState.mode,'local');
  assert.equal(context.guideState.listen.active,true);
});

test('Guide ignores cached server-status fallback and does not poll hidden tabs', async () => {
  const calls=[];
  const context=vm.createContext({guideLoadInFlight:false,guideStatusInFlight:false,document:{hidden:false},
    guideState:{channels:[{name:'Current'}],lineupRevision:'current'},guideEls:{status:{}},
    renderGuide(){},restoreListenSession(){},
    fetch:async url=>{calls.push(url);return {ok:true,json:async()=>({lineup_revision:'old',update:{stale:true}})};}});
  vm.runInContext(source.slice(source.indexOf('async function loadGuide('),source.indexOf('function isLoopbackHost(')),context);
  await context.synchronizeGuideState();
  context.document.hidden=true;
  await context.synchronizeGuideState();
  assert.deepEqual(calls,['/api/ui/status']);
  assert.equal(context.guideState.channels[0].name,'Current');
});
