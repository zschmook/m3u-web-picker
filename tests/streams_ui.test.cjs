const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/ui_streams.js','utf8');
function node() {
  const classes = new Set();
  return {textContent:'',innerHTML:'',disabled:false,handlers:{},classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c),toggle(c,on){on?classes.add(c):classes.delete(c)}},addEventListener(type,fn){this.handlers[type]=fn}};
}
const ids = Object.fromEntries(['uiStreamsRefresh','uiStreamsRows','uiStreamsLan','uiStreamsVpn','uiStreamsRoute','uiStreamsTotals','uiStreamsClients','uiStreamsEvents','uiStreamsStatus','uiStreamsMonitorStatus','uiPage-settings'].map(id=>[id,node()]));
for (const name of ['Cpu','Gpu','Ram']) {ids[`uiStreams${name}`]=node();ids[`uiStreams${name}Scope`]=node();}
ids.uiStreamsStatus.textContent = '3 active';
const panel = node();panel.classList.add('is-active');
const events={},docEvents={};let interval,requests=0,fail=false;
const data = {network:{lan_ip:'10.0.0.18',vpn_ip:'195.181.163.29',route:'vpn'},streams:[{channel:'<script>bad</script>',provider:'Primary',client_ip:'10.0.0.30',client:'VLC',mode:'passthrough',transport:'MPEG-TS',elapsed_seconds:65,mbps:8,bytes_per_second:1000000,state:'receiving'}],clients:[{client_ip:'10.0.0.30',client:'VLC',connections:2,bytes_per_second:2000000,bytes_sent:30000000}],events:[],observed_stream_count:1,client_count:1,ffmpeg_sessions:1,relay_requests:0,recoveries:0};
const document={hidden:false,getElementById:id=>ids[id],querySelector:selector=>selector.includes('[role="status"]')?ids.uiStreamsMonitorStatus:panel,addEventListener:(type,fn)=>docEvents[type]=fn};
vm.runInNewContext(source,{document,window:{addEventListener:(type,fn)=>events[type]=fn},fetch:async url=>{assert.equal(url,'/api/streams');requests++;return {ok:!fail,json:async()=>data}},setInterval:fn=>{interval=fn;return 1},clearInterval:()=>{interval=null},Date});
async function settle(){for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  await settle();assert.equal(requests,1);assert.match(ids.uiStreamsRows.innerHTML,/1\.00 MB\/s/);assert.match(ids.uiStreamsRows.innerHTML,/8\.00 Mbps/);assert.match(ids.uiStreamsClients.innerHTML,/2\.00 MB\/s/);assert.match(ids.uiStreamsClients.innerHTML,/16\.00 Mbps/);assert.match(ids.uiStreamsRows.innerHTML,/FFmpeg passthrough/);assert.match(ids.uiStreamsRows.innerHTML,/01:05/);assert.ok(!ids.uiStreamsRows.innerHTML.includes('<script>'));assert.equal(ids.uiStreamsLan.textContent,'10.0.0.18');assert.equal(ids.uiStreamsVpn.textContent,'195.181.163.29');
  assert.equal(ids.uiStreamsGpu.textContent,'Unavailable');
  data.resources={cpu_percent:12.3,ram_percent:45.6,gpu_percent:0,cpu_scope:'Picker container',ram_scope:'Picker container',gpu_scope:'Whole device'};
  await interval();assert.equal(ids.uiStreamsCpu.textContent,'12.3%');assert.equal(ids.uiStreamsRam.textContent,'45.6%');assert.equal(ids.uiStreamsGpu.textContent,'0.0%');
  requests=1;
  document.hidden=true;docEvents.visibilitychange();await settle();assert.equal(interval,null);assert.equal(requests,1);
  document.hidden=false;ids['uiPage-settings'].classList.add('ui-page-hidden');events['ui:page']();await settle();assert.equal(requests,1);
  ids['uiPage-settings'].classList.remove('ui-page-hidden');events['ui:page']();await settle();assert.equal(requests,2);
  fail=true;await interval();assert.match(ids.uiStreamsMonitorStatus.textContent,/out of date/);assert.match(ids.uiStreamsRows.innerHTML,/1\.00 MB\/s/);assert.equal(ids.uiStreamsStatus.textContent,'3 active');
  fail=false;data.streams=[];data.clients=[];data.network={route:'normal'};await interval();assert.match(ids.uiStreamsRows.innerHTML,/No streams/);assert.match(ids.uiStreamsClients.innerHTML,/No active client/);assert.equal(ids.uiStreamsVpn.textContent,'Off');
  console.log('PASS: connection/client byte rates, Mbps conversion, escaped labels, hidden-page polling, error state and empty/off state');
})().catch(error=>{console.error(error);process.exitCode=1});
