const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto').webcrypto;
const source=fs.readFileSync('static/js/ui_vpn_settings.js','utf8');
let server={enabled:true,desired_on:false,configured:true,persistent:true,app_vpn_active:false,network_protected:false,control_status:'applied'};
let posts=[],reject=false;
function element(){return {hidden:false,disabled:false,textContent:'',dataset:{},children:[],classList:{add(){},remove(){},toggle(){}},setAttribute(){},append(...nodes){this.children.push(...nodes)},replaceChildren(){this.children=[]},addEventListener(){}};}
function mount(){
 const provider=element();provider.value='';provider.options=[{value:'',textContent:'Choose a VPN provider'}];provider.append=function(option){this.options.push(option)};
 const profile=element();profile.files=[];profile.value='';
 const lan=element();lan.value='';const form=element();form.elements={provider,profile,lan};
 const nodes={'form':form};for(const key of ['choice','fields','message','indicator','clear','retry','test-results','links','provider-note','provider-detected','lan-note','live'])nodes[`[data-vpn-${key}]`]=element();
 const host=element();host.isConnected=true;host.querySelector=key=>{assert(nodes[key],key);return nodes[key]};
 const scheduled=[],refreshes=[];
 const window={refreshVpnPowerStatus:()=>refreshes.push(1)};
 const document={querySelector:()=>({}),querySelectorAll:()=>[host],getElementById:()=>null,addEventListener(){},createElement:()=>element(),createTextNode:text=>({textContent:text})};
 const fetch=async(path,options={})=>{
  if(path.includes('vpn-provider-links'))return {ok:true,json:async()=>({providers:{}})};
  if(path==='/api/vpn-preference'){
   const body=JSON.parse(options.body);posts.push(body);
   if(reject)return {ok:false,status:409,json:async()=>({error:'Manager unavailable'})};
   server={...server,enabled:body.enabled};return {ok:true,json:async()=>({...server})};
  }
  if(path==='/api/vpn-state')return {ok:true,json:async()=>({...server})};
  if(path==='/api/vpn-test')return {ok:true,json:async()=>({helper_available:true,test:null})};
  assert.equal(path,'/api/vpn-config');return {ok:true,json:async()=>({provider:'protonvpn',providers:['custom','protonvpn'],lan_subnets:['10.0.0.0/24'],suggested_lan_subnet:'10.0.0.0/24',lan_subnet_detection:'detected',vpn_runtime:{...server}})};
 };
 vm.runInNewContext(source,{document,window,fetch,crypto,Uint8Array,URL,setTimeout:fn=>{scheduled.push(fn);return scheduled.length},clearTimeout(){}});
 return {host,choice:nodes['[data-vpn-choice]'],fields:nodes['[data-vpn-fields]'],live:nodes['[data-vpn-live]'],scheduled,refreshes,provider};
}
async function settle(){for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
 let view=mount();await settle();assert.equal(view.choice.checked,true);assert.equal(view.choice.disabled,false);assert.equal(view.fields.hidden,false);assert.equal(view.provider.value,'protonvpn');assert.match(view.live.textContent,/power is off/);
 view.choice.checked=false;view.choice.onchange();await settle();assert.deepEqual(posts.at(-1),{enabled:false});assert.equal(view.choice.checked,false);assert.equal(view.fields.hidden,true);assert.equal(server.configured,true);assert.equal(view.refreshes.length,1);
 view.host.isConnected=false;view=mount();await settle();assert.equal(view.choice.checked,false);assert.equal(view.fields.hidden,true);assert.match(view.live.textContent,/disabled in Settings/);
 server={...server,enabled:true};view.scheduled.shift()();await settle();assert.equal(view.choice.checked,true);assert.equal(view.fields.hidden,false);
 reject=true;view.choice.checked=false;view.choice.onchange();await settle();assert.equal(view.choice.checked,true);assert.equal(server.enabled,true);assert.match(view.live.textContent,/Manager unavailable/);
 console.log('PASS: saved checkbox hydration, independent power-off, preference POST, reload persistence, other-client sync, and rejected-change rollback');
})().catch(error=>{console.error(error);process.exitCode=1});
