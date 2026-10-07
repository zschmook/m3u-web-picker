const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');
function section(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end));
}
function setup(fetch) {
  const timers = [], statuses = [];
  const context = vm.createContext({
    fetch, channelKey: row => row.key, render() {}, rebuildProviderGroupFilter() {},
    renderProviderSources() {}, setSourceMode() {}, setStatus: text => statuses.push(text),
    setTimeout(fn) {timers.push(fn); return fn;},
    clearTimeout(fn) {const index = timers.indexOf(fn); if (index >= 0) timers.splice(index, 1);},
  });
  vm.runInContext(source.slice(0, source.indexOf('const els =')), context);
  vm.runInContext(section('function applyChannelPayload(', 'async function loadFromUrl('), context);
  vm.runInContext(section('async function loadInitialChannels(', 'els.table.addEventListener('), context);
  vm.runInContext(section('function refreshChannelState(', 'window.addEventListener("focus"'), context);
  vm.runInContext(`applyChannelPayload({channels:[{id:0,key:'manual:alpha'},{id:1,key:'manual:bravo'}], selected_ids:[0], selection_revision:'initial'});`, context);
  return {context, timers, statuses, run: code => vm.runInContext(code, context)};
}
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}
const response = (data, status=200) => ({ok: status === 200, status, json: async () => data});

test('selection saves send stable keys and the saved revision', async () => {
  const calls = [];
  const ui = setup(async (_, options) => {calls.push(JSON.parse(options.body)); return response({selection_revision:'saved'});});
  await ui.context.saveSelected();
  assert.deepEqual(calls, [{keys:['manual:alpha'], selection_revision:'initial'}]);
  assert.equal(ui.run('selectionRevision'), 'saved');
});

test('rapid edits serialize saves and use the revision from the first commit', async () => {
  const first = deferred(), calls = [];
  const ui = setup(async (_, options) => {
    calls.push(JSON.parse(options.body));
    return calls.length === 1 ? first.promise : response({selection_revision:'second'});
  });
  ui.context.scheduleSaveSelected();
  const saving = ui.context.saveSelected();
  ui.run('selected.add(1)');
  ui.context.scheduleSaveSelected();
  await ui.context.saveSelected();
  assert.equal(calls.length, 1);
  first.resolve(response({selection_revision:'first'}));
  await saving;
  await ui.timers.shift()();
  assert.deepEqual(calls[1], {keys:['manual:alpha','manual:bravo'], selection_revision:'first'});
  assert.equal(ui.run('selectionDirty'), false);
});

test('a delayed catalog response cannot undo an edit', async () => {
  const read = deferred();
  const ui = setup(async () => read.promise);
  const loading = ui.context.loadInitialChannels({quiet:true});
  ui.run('selected.add(1)');
  ui.context.scheduleSaveSelected();
  read.resolve(response({channels:[{id:0,key:'manual:alpha'}], selected_ids:[], selection_revision:'old'}));
  await loading;
  assert.equal(ui.run('selected.has(1)'), true);
  assert.equal(ui.run('selectionRevision'), 'initial');
});

test('a catalog response fetched during saving cannot undo the committed selection', async () => {
  const read = deferred();
  const ui = setup(async url => url === '/api/channels' ? read.promise : response({selection_revision:'saved'}));
  const loading = ui.context.loadInitialChannels({quiet:true});
  await ui.context.saveSelected();
  read.resolve(response({channels:[{id:0,key:'manual:alpha'}], selected_ids:[], selection_revision:'old'}));
  await loading;
  assert.equal(ui.run('selected.has(0)'), true);
  assert.equal(ui.run('selectionRevision'), 'saved');
});

test('a stale-save rejection reloads authoritative state without retrying the old snapshot', async () => {
  const calls = [];
  const ui = setup(async (url, options) => {
    calls.push({url, options});
    return url === '/api/selection' ? response({error:'Selections changed. Try again.'}, 409) :
      response({channels:[{id:0,key:'manual:alpha'},{id:1,key:'manual:bravo'}], selected_ids:[1], selection_revision:'current'});
  });
  ui.context.scheduleSaveSelected();
  await ui.context.saveSelected();
  assert.equal(ui.run('selected.has(0)'), false);
  assert.equal(ui.run('selected.has(1)'), true);
  assert.equal(ui.run('selectionRevision'), 'current');
  assert.equal(ui.timers.length, 0);
  assert.equal(calls[1].options.cache, 'no-store');
  assert.equal(ui.statuses.at(-1), 'Selections changed. Try again.');
});

test('network failure retains the draft and does not start an automatic retry loop', async () => {
  const ui = setup(async () => {throw new Error('offline');});
  ui.context.scheduleSaveSelected();
  await ui.context.saveSelected();
  assert.equal(ui.run('selectionDirty'), true);
  assert.equal(ui.run('selected.has(0)'), true);
  assert.equal(ui.timers.length, 0);
});

test('an idle tab follows another device\'s committed selections without reloading the page', async () => {
  const calls=[];
  const ui=setup(async (url,options)=>{calls.push({url,options});return response({
    channels:[{id:0,key:'manual:alpha'},{id:1,key:'manual:bravo'}],selected_ids:[1],selection_revision:'other-device'});});
  await ui.context.synchronizeChannelState('other-device');
  assert.equal(ui.run('selected.has(0)'),false);
  assert.equal(ui.run('selected.has(1)'),true);
  await ui.context.synchronizeChannelState('other-device');
  assert.equal(calls.length,1);
  assert.equal(calls[0].options.cache,'no-store');
});

test('server synchronization leaves an unsaved draft intact and avoids duplicate reloads', async () => {
  const read=deferred();let calls=0;
  const ui=setup(async()=>{calls+=1;return read.promise;});
  ui.context.scheduleSaveSelected();
  await ui.context.synchronizeChannelState('other-device');
  assert.equal(calls,0);
  ui.run('selectionDirty=false');
  const updating=ui.context.synchronizeChannelState('other-device');
  ui.context.synchronizeChannelState('other-device');
  assert.equal(calls,1);
  read.resolve(response({channels:[{id:0,key:'manual:alpha'}],selected_ids:[0],selection_revision:'other-device'}));
  await updating;
});
