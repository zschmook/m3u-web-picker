const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const resources=JSON.parse(fs.readFileSync('static/vpn-provider-links.json','utf8'));let posted,identity=0,job=null,deletes=0,featureEnabled=false;
 const status={provider:'protonvpn',providers:Object.keys(resources.providers),lan_subnets:[],configuration_present:false,activation:'not_applied',suggested_lan_subnet:'10.0.0.0/24'};
 await page.route('http://vpn-preview.test/**',async route=>{const req=route.request(),url=req.url();
  if(url.endsWith('/api/vpn-test')){
   assert.match(req.headers()['x-vpn-session'],/^[a-f0-9]{32}$/);
   if(req.method()==='POST')job={id:String(identity),profile_id:String(identity),status:'running',stage:'Testing tunnel',profile_changed:false,results:{}};
   return route.fulfill({json:{helper_available:true,test:job,app_vpn_active:false}});
  }
  if(url.endsWith('/api/setup/vpn-intent')){featureEnabled=req.postDataJSON().enabled;return route.fulfill({json:{enabled:featureEnabled}});}
  if(url.endsWith('/api/vpn-state'))return route.fulfill({json:{enabled:featureEnabled,configured:false,app_vpn_active:false,desired_on:featureEnabled}});
  if(url.includes('/static/vpn-provider-links.json'))return route.fulfill({json:resources});
  if(url.endsWith('/api/vpn-config')){
   if(req.method()==='PATCH'){posted=req.postDataJSON();identity++;return route.fulfill({json:{...status,configuration_present:true,profile_id:String(identity)}});}
   if(req.method()==='DELETE')deletes++;
   return route.fulfill({json:status});
  }
  if(url.endsWith('/ui.js'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync('static/js/ui_vpn_settings.js','utf8')});
  if(url.includes('.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync('static/css/vpn_settings.css','utf8')});
  return route.fulfill({contentType:'text/html',body:'<div data-vpn-config></div><button id="setupNext">Continue</button><script>Storage.prototype.setItem=()=>{throw Error("No browser storage")};</script><script src="/ui.js"></script>'});
 });
 await page.goto('http://vpn-preview.test/');await page.waitForFunction(n=>document.querySelector('select').options.length===n,status.providers.length+1);
 assert.equal(await page.locator('select[name=provider]').inputValue(),'');assert.equal(await page.locator('[data-vpn-links] a').count(),0);
 assert(await page.locator('[data-vpn-fields]').isHidden());assert(await page.locator('#setupNext').isEnabled());await page.locator('[data-vpn-choice]').check();assert(await page.locator('#setupNext').isDisabled());
 for(const [id,info] of Object.entries(resources.providers)){
  await page.selectOption('select',id);const links=await page.locator('[data-vpn-links] a').evaluateAll(nodes=>nodes.map(a=>({href:a.href,target:a.target,rel:a.rel})));
  assert.deepEqual(links.map(a=>a.href),['website','guide','pricing'].filter(k=>info[k]).map(k=>new URL(info[k]).href));assert(links.every(a=>a.target==='_blank'&&a.rel.includes('noopener')));
 }
 await page.selectOption('select','');
 const dropped=await page.evaluateHandle(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['# ProtonVPN configuration\ntest-profile'],'test.config',{type:'text/plain'}));return transfer;});
 await page.locator('[data-vpn-config]').dispatchEvent('dragenter',{dataTransfer:dropped});assert(await page.locator('[data-vpn-config]').evaluate(node=>node.classList.contains('vpn-drag-over')));
 await page.locator('[data-vpn-config]').dispatchEvent('drop',{dataTransfer:dropped});await dropped.dispose();assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files[0].name),'test.config');
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='Testing tunnel');assert.equal(posted.wireguard_config,'# ProtonVPN configuration\ntest-profile');assert.equal(posted.provider,'protonvpn');assert((await page.locator('[data-vpn-provider-detected]').textContent()).includes('Detected Proton VPN'));assert(await page.locator('#setupNext').isDisabled());assert(await page.locator('[data-vpn-indicator]').getAttribute('class')==='vpn-spinner');
 job={...job,status:'passed',results:{checks:{tunnel_healthy:true,dns_resolved:true}}};
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='VPN tests passed');assert(await page.locator('#setupNext').isEnabled());
 await page.selectOption('select','custom');assert(await page.locator('#setupNext').isDisabled());
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='Testing tunnel');assert.equal(posted.provider,'custom');assert(await page.locator('[data-vpn-provider-detected]').isHidden());
 job={...job,status:'passed'};await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='VPN tests passed');
 // Edits invalidate success immediately; an old successful job cannot unlock the new upload.
 await page.locator('input[name=lan]').fill('192.168.50.0/24');assert(await page.locator('#setupNext').isDisabled());
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='Testing tunnel');job={...job,status:'failed',results:{error:'Tunnel unavailable'}};
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='Tunnel unavailable');assert(await page.locator('#setupNext').isDisabled());assert(await page.locator('[data-vpn-retry]').isVisible());
 await page.locator('[data-vpn-retry]').click();await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent==='Testing tunnel');
 job={...job,status:'passed',profile_changed:true};await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent.includes('expired'));assert(await page.locator('#setupNext').isDisabled());
 await page.locator('[data-vpn-clear]').click();assert.equal(await page.locator('input[type=file]').inputValue(),'');assert(await page.locator('#setupNext').isDisabled());
 await page.selectOption('select','');const beforeUnknown=identity;
 await page.locator('input[type=file]').setInputFiles({name:'unknown.conf',mimeType:'text/plain',buffer:Buffer.from('provider-neutral-profile')});
 await page.waitForFunction(()=>document.querySelector('[data-vpn-message]').textContent.includes('does not clearly identify'));assert.equal(identity,beforeUnknown);assert.equal(await page.locator('select[name=provider]').inputValue(),'');
 await page.locator('[data-vpn-choice]').uncheck();assert(await page.locator('#setupNext').isEnabled());assert(deletes>=1);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);assert.deepEqual(errors,[]);
 console.log('PASS: auto upload/test, spinner, pass/fail/expiry gate, edits invalidate success, retry/clear, all provider links, and no browser storage');
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
