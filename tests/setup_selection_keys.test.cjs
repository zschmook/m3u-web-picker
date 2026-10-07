const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source=fs.readFileSync(path.join(__dirname, '../static/js/setup_wizard.js'), 'utf8');

test('setup keeps draft channel identities stable across provider reordering and filtered reads', async () => {
  let payload={channels:[{id:0,key:'manual:alpha',name:'Alpha'},{id:1,key:'manual:bravo',name:'Bravo'}],
    selected_keys:['manual:alpha'], selection_revision:'initial', groups:[], total:2};
  const elements=new Map();
  const el=id=>{if(!elements.has(id)) elements.set(id,{value:'',checked:false,innerHTML:'',add(){}});return elements.get(id);};
  const context=vm.createContext({document:{getElementById:el},api:async()=>payload,
    updateChannelResultCount(){},esc:value=>String(value)});
  vm.runInContext(source.slice(source.indexOf('const ctx ='),source.indexOf('const body =')),context);
  vm.runInContext(source.slice(source.indexOf('async function fetchChannels('),source.indexOf('function renderChannels(')),context);
  await vm.runInContext('fetchChannels()',context);
  payload={...payload,channels:[{id:0,key:'manual:bravo',name:'Bravo'},{id:1,key:'manual:alpha',name:'Alpha'}],
    selected_keys:['manual:bravo'],selection_revision:'other-tab'};
  await vm.runInContext('fetchChannels()',context);
  assert.match(el('channelList').innerHTML,/value="manual:alpha" checked/);
  assert.doesNotMatch(el('channelList').innerHTML,/value="manual:bravo" checked/);
  assert.equal(vm.runInContext('ctx.selectionRevision',context),'initial');
});
