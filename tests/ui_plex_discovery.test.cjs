const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

async function setup(overrides = {}, respond, options = {}) {
  class Element {
    constructor() {
      this.events = {}; this.children = []; this.value = ''; this.dataset = {};
      this.style = {}; this.classList = {add() {}, remove() {}};
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; this.value = items[0]?.value || ''; }
    addEventListener(name, handler) { this.events[name] = handler; }
    focus() { this.focused = true; }
  }
  const elements = new Map(), calls = [], timers = [], opens = [];
  const popup={closed:false,close(){this.closed=true;}};
  const el = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const data = {settings: {enabled: true, movies_enabled: false, minimum_episodes: 30},
    catalog: {servers: [], warnings: []}, job: {running: false},
    servers: [], shows: [], channels: [], movies: {channels: [], enabled: false},
    discovered_servers: [{id: 'one', name: 'Plex at 10.0.0.22', url: 'http://10.0.0.22:32400'}], ...overrides};
  const context = vm.createContext({
    document: {getElementById: el, createElement: () => new Element(), querySelectorAll: () => []},
    Option: class extends Element { constructor(label, value) { super(); this.textContent = label; this.value = value; } },
    fetch: async (url, options) => {
      calls.push({url, options});
      const result=respond?await respond(url,options,data):data;
      return {ok: !result.error, json: async () => result};
    },
    window:{open(...args){opens.push(args);return options.popupBlocked?null:popup;}},URL,
    setTimeout(fn) {timers.push(fn);return fn;}, clearTimeout(fn) {const i=timers.indexOf(fn);if(i>=0)timers.splice(i,1);}, console,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/custom_channels.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {el, calls, data, timers, popup, opens};
}

test('identified but unauthorized Plex servers appear separately from connected libraries', async () => {
  const ui = await setup();
  assert.equal(ui.el('ccDiscoveredServerField').hidden, false);
  assert.equal(ui.el('ccDiscoveredServer').children[1].textContent,
    'Plex at 10.0.0.22 · http://10.0.0.22:32400');
  assert.equal(ui.el('ccConnectionPanel').open, true);
  assert.equal(ui.el('ccDiscover').textContent, 'Refresh Custom Channels');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].options.method, 'GET');
});

test('choosing a discovered server fills its URL and focuses the private token input', async () => {
  const ui = await setup();
  ui.el('ccToken').value = 'draft-token';
  ui.el('ccDiscoveredServer').value = 'http://10.0.0.22:32400';
  ui.el('ccDiscoveredServer').events.change();
  assert.equal(ui.el('ccServerUrl').value, 'http://10.0.0.22:32400');
  assert.equal(ui.el('ccToken').focused, true);
  assert.equal(ui.el('ccToken').value, 'draft-token');
  assert.equal(ui.calls.length, 1);
});

test('older payloads and already connected servers keep the manual connection and refresh controls', async () => {
  const ui = await setup({discovered_servers: undefined, servers: [{id: 'one', name: 'Connected'}]});
  assert.equal(ui.el('ccDiscoveredServerField').hidden, true);
  assert.equal(ui.el('ccDiscover').textContent, 'Refresh Custom Channels');
  assert.equal(ui.el('ccServerUrl').disabled, false);
});

test('server names are rendered as text and do not become HTML', async () => {
  const ui = await setup({discovered_servers: [{id: 'x', name: '<img src=x onerror=bad()>', url: 'http://10.0.0.22:32400'}]});
  assert.match(ui.el('ccDiscoveredServer').children[1].textContent, /^<img/);
  assert.equal(ui.el('ccDiscoveredServer').children[1].innerHTML, undefined);
});

test('server discovery works before either channel feature is enabled and does not enable them', async () => {
  const ui=await setup({settings:{enabled:false,movies_enabled:false,minimum_episodes:30},discovery_count:1});
  assert.equal(ui.el('ccDiscoverServers').disabled,false);
  assert.equal(ui.el('ccDiscover').disabled,true);
  await ui.el('ccDiscoverServers').events.click();
  assert.equal(ui.calls[1].url,'/api/custom-channels/servers/discover');
  assert.equal(ui.calls[1].options.method,'POST');
  assert.deepEqual(ui.calls.map(call=>call.options.method),['GET','POST','GET']);
  assert.match(ui.el('ccDiscoveryStatus').textContent,/Found 1 Plex server/);
  assert.equal(ui.el('ccEnabled').checked,false);
  assert.equal(ui.el('ccMoviesEnabled').checked,false);
  assert.equal(ui.el('ccConnectionPanel').open,true);
});

test('busy discovery prevents duplicate requests and restores controls when done', async () => {
  let release;
  const ui=await setup({},(url,options,data)=>options.method==='POST'?new Promise(resolve=>{release=()=>resolve({...data,discovery_count:1});}):data);
  const pending=ui.el('ccDiscoverServers').events.click();
  assert.equal(ui.el('ccDiscoverServers').disabled,true);
  assert.equal(ui.el('ccConnect').disabled,true);
  await ui.el('ccDiscoverServers').events.click();
  assert.equal(ui.calls.filter(call=>call.options.method==='POST').length,1);
  release();await pending;
  assert.equal(ui.el('ccDiscoverServers').disabled,false);
  assert.equal(ui.el('ccDiscoverServers').textContent,'Discover Plex Servers');
});

test('discovery errors preserve the manual address/token and allow retry', async () => {
  const ui=await setup({},(url,options,data)=>options.method==='POST'?{error:'Discovery unavailable'}:data);
  ui.el('ccServerUrl').value='http://10.0.0.24:32400';ui.el('ccToken').value='draft';
  await ui.el('ccDiscoverServers').events.click();
  assert.equal(ui.el('ccDiscoveryStatus').textContent,'Discovery unavailable');
  assert.equal(ui.el('ccServerUrl').value,'http://10.0.0.24:32400');
  assert.equal(ui.el('ccToken').value,'draft');
  assert.equal(ui.el('ccDiscoverServers').disabled,false);
});

const pin={flow_id:'private-flow',auth_url:'https://app.plex.tv/auth#?clientID=picker&code=pin',expires_in:600};
const fresh={settings:{enabled:false,movies_enabled:false,minimum_episodes:30},plex_account:{signed_in:false,name:''},discovered_servers:[]};

test('sign-in works with channels off, then lists authorized servers without connecting or scanning',async()=>{
  const ui=await setup(fresh,(url,options,data)=>{
    if(url.endsWith('/plex/sign-in'))return pin;
    if(url.endsWith('/plex/sign-in/status')){data.plex_account={signed_in:true,name:'Zack'};return {status:'complete'};}
    if(url.endsWith('/plex/servers'))return {servers:[{id:'server-one',name:'My Plex'}],account:data.plex_account};
    return data;
  });
  const pending=ui.el('ccPlexSignIn').events.click();
  assert.equal(ui.opens.length,1);await pending;
  assert.equal(ui.popup.opener,null);assert.equal(ui.popup.location,pin.auth_url);
  assert.equal(ui.el('ccPlexSignIn').disabled,true);
  await ui.timers.shift()();
  assert.equal(ui.el('ccPlexServer').children[1].textContent,'My Plex');
  assert.equal(ui.el('ccPlexServersField').hidden,false);
  assert.equal(ui.el('ccPlexConnect').disabled,true);
  assert.equal(ui.popup.closed,true);
  assert.equal(ui.calls.some(call=>call.url.endsWith('/discover')||call.url.endsWith('/plex/connect')),false);
  assert.equal(ui.el('ccEnabled').checked,false);assert.equal(ui.el('ccMoviesEnabled').checked,false);
});

test('blocked popup provides an explicit Plex sign-in link',async()=>{
  const ui=await setup(fresh,(url,options,data)=>url.endsWith('/plex/sign-in')?pin:data,{popupBlocked:true});
  await ui.el('ccPlexSignIn').events.click();
  assert.equal(ui.el('ccPlexSignInLink').hidden,false);
  assert.equal(ui.el('ccPlexSignInLink').href,pin.auth_url);
  assert.match(ui.el('ccPlexStatus').textContent,/Open Plex sign-in/);
});

test('cancel stops polling, closes the owned window and leaves existing manual credentials intact',async()=>{
  const ui=await setup(fresh,(url,options,data)=>url.endsWith('/plex/sign-in')?pin:data);
  ui.el('ccToken').value='draft';await ui.el('ccPlexSignIn').events.click();
  await ui.el('ccPlexCancel').events.click();
  assert.equal(ui.timers.length,0);assert.equal(ui.popup.closed,true);
  assert.equal(ui.el('ccPlexSignIn').disabled,false);assert.equal(ui.el('ccToken').value,'draft');
  const cancel=ui.calls.at(-1);assert.equal(cancel.url,'/api/custom-channels/plex/sign-in/cancel');
  assert.deepEqual(JSON.parse(cancel.options.body),{flow_id:'private-flow'});
});

test('expired sign-in restores retry controls and stops polling',async()=>{
  const ui=await setup(fresh,(url,options,data)=>url.endsWith('/plex/sign-in')?pin:url.endsWith('/plex/sign-in/status')?{status:'expired'}:data);
  await ui.el('ccPlexSignIn').events.click();await ui.timers.shift()();
  assert.equal(ui.timers.length,0);assert.equal(ui.el('ccPlexSignIn').disabled,false);
  assert.match(ui.el('ccPlexStatus').textContent,/expired/);
});

test('saved sign-in loads choices and connects only the selected server without sending credentials',async()=>{
  const ui=await setup({...fresh,plex_account:{signed_in:true,name:'Zack'}},(url,options,data)=>url.endsWith('/plex/servers')?{servers:[{id:'one',name:'My Plex'}],account:data.plex_account}:data);
  assert.equal(ui.el('ccPlexServer').children[1].value,'one');
  ui.el('ccPlexServer').value='one';ui.el('ccPlexServer').events.change();
  assert.equal(ui.el('ccPlexConnect').disabled,false);
  await ui.el('ccPlexConnect').events.click();
  const connect=ui.calls.find(call=>call.url.endsWith('/plex/connect'));
  assert.deepEqual(JSON.parse(connect.options.body),{server_id:'one'});
  assert.equal(ui.calls.some(call=>call.url.endsWith('/discover')),false);
  assert.match(ui.el('ccStatus').textContent,/Enable TV show or movie channels/);
});

test('Plex sign-in startup errors close the empty window and leave manual fallback usable',async()=>{
  const ui=await setup(fresh,(url,options,data)=>url.endsWith('/plex/sign-in')?{error:'Plex is unavailable'}:data);
  await ui.el('ccPlexSignIn').events.click();
  assert.equal(ui.popup.closed,true);assert.equal(ui.el('ccPlexSignIn').disabled,false);
  assert.equal(ui.el('ccConnect').disabled,false);assert.match(ui.el('ccPlexStatus').textContent,/unavailable/);
});
