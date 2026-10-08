(() => {
  'use strict';
  if(!document.querySelector('link[href*="vpn_settings.css"]')){
    const styles=document.createElement('link');styles.rel='stylesheet';styles.href='/static/css/vpn_settings.css?v=auto-test-1';document.head.append(styles);
  }
  const session=Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
  // Page-lifetime state only. Reloading requires another upload.
  const draft={enabled:false,file:null,provider:null,providerSource:null,lan:null,profile:null,version:0,busy:false,error:'',test:null};
  const providerHints={mullvad:/\bmullvad\b/i,protonvpn:/\bproton(?:[ _-]*vpn)?\b/i,nordvpn:/\bnord[ _-]*vpn\b/i,surfshark:/\bsurfshark\b/i,airvpn:/\bair[ _-]*vpn\b/i,ivpn:/\bivpn\b/i,windscribe:/\bwindscribe\b/i,'private internet access':/\b(?:private[ _-]*internet[ _-]*access|pia)\b/i,cyberghost:/\bcyberghost\b/i,expressvpn:/\bexpress[ _-]*vpn\b/i,ipvanish:/\bipvanish\b/i,privado:/\bprivado(?:[ _-]*vpn)?\b/i,privatevpn:/\bprivate[ _-]*vpn\b/i,purevpn:/\bpure[ _-]*vpn\b/i,torguard:/\btorguard\b/i,vpnunlimited:/\bvpn[ _-]*unlimited\b/i,vyprvpn:/\bvypr[ _-]*vpn\b/i};
  function detectProvider(text,name){
    const comments=text.split(/\r?\n/).filter(line=>/^\s*[#;]/.test(line)).join('\n');
    for(const hint of [comments,name.replace(/_/g,' ')]){
      const matches=Object.entries(providerHints).filter(([,pattern])=>pattern.test(hint));
      if(matches.length===1)return matches[0][0];
      if(matches.length>1)return null;
    }
    return null;
  }
  window.vpnWizard={session,requested:()=>draft.enabled,canContinue:()=>!draft.enabled||(!draft.busy&&!draft.error&&draft.test?.status==='passed'&&!draft.test.profile_changed&&draft.test.profile_id===draft.profile)};
  const resourcesPromise=fetch('/static/vpn-provider-links.json',{cache:'no-cache'}).then(r=>r.ok?r.json():{providers:{}}).catch(()=>({providers:{}}));
  const isFileDrag=event=>Array.from(event.dataTransfer?.types||[]).includes('Files');
  // Prevent a missed drop from navigating away from the wizard to the local file.
  document.addEventListener('dragover',event=>{if(isFileDrag(event)&&!event.defaultPrevented){event.preventDefault();event.dataTransfer.dropEffect='none';}});
  document.addEventListener('drop',event=>{if(isFileDrag(event))event.preventDefault();});
  async function request(options={},path='/api/vpn-config'){
    const response=await fetch(path,{cache:'no-store',...options,headers:{...options.headers,'X-VPN-Session':session}});
    const data=await response.json();if(!response.ok){const error=Error(data.error||'VPN request failed.');error.status=response.status;throw error;}return data;
  }
  window.mountVpnConfiguration=()=>{
    for(const host of document.querySelectorAll('[data-vpn-config]:not([data-mounted])')){
      host.dataset.mounted='true';host.classList.add('vpn-config-card');
      host.innerHTML=`<label class="vpn-choice"><input type="checkbox" data-vpn-choice><span><strong>Use a VPN</strong><small>Optional · connection test</small></span></label>
      <p data-vpn-live class="vpn-help" role="status" hidden></p><div data-vpn-fields hidden><p class="vpn-help">Drag and drop your WireGuard configuration anywhere in this VPN box, or use Choose File. We’ll identify the provider when possible and test it automatically. Test uploads stay in temporary memory for up to 20 minutes. Applying the VPN saves its configuration privately for automatic startup.</p>
      <form class="vpn-form">
        <label class="vpn-field vpn-provider-field"><span>VPN provider</span><select name="provider"><option value="">Choose a VPN provider</option></select></label>
        <div class="vpn-field"><label for="vpnUpload">WireGuard configuration file</label><strong class="vpn-drop-prompt">Drag and drop your config file here</strong><div class="vpn-upload"><input id="vpnUpload" name="profile" type="file" accept=".conf,.config,.ini"><span data-vpn-indicator role="img" aria-label="No configuration selected"></span><button type="button" data-vpn-clear aria-label="Clear VPN configuration" title="Clear configuration" hidden>×</button></div></div>
        <div class="vpn-provider-resources vpn-field-wide"><nav data-vpn-links aria-label="VPN provider resources"></nav><small data-vpn-provider-note></small></div>
        <small data-vpn-provider-detected class="vpn-drop-hint vpn-field-wide" role="status" hidden></small>
        <label class="vpn-field vpn-field-wide"><span>Local network subnets</span><input name="lan" autocomplete="off" required><small data-vpn-lan-note></small></label>
        <p data-vpn-message role="status" aria-live="polite" class="vpn-help"></p>
        <button type="button" class="vpn-test" data-vpn-retry hidden>Retry VPN test</button>
        <section class="vpn-test-results vpn-field-wide" data-vpn-test-results aria-live="polite"></section>
      </form></div>`;
      const form=host.querySelector('form'),choice=host.querySelector('[data-vpn-choice]'),fields=host.querySelector('[data-vpn-fields]'),message=host.querySelector('[data-vpn-message]'),indicator=host.querySelector('[data-vpn-indicator]'),clear=host.querySelector('[data-vpn-clear]'),retry=host.querySelector('[data-vpn-retry]'),results=host.querySelector('[data-vpn-test-results]');
      choice.checked=draft.enabled;fields.hidden=!draft.enabled;
      if(draft.file){const transfer=new DataTransfer();transfer.items.add(draft.file);form.elements.profile.files=transfer.files;}
      let resources={},debounce,helper=true,runtime=null,preferenceBusy=false,preferenceError='',choiceEdited=false;
      let intentReady=Promise.resolve();
      const setupPage=Boolean(document.getElementById('setupNext'));
      if(!setupPage)choice.disabled=true;
      const syncIntent=()=>{
        const enabled=draft.enabled;
        if(setupPage){
          intentReady=request({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})},'/api/setup/vpn-intent');
          intentReady.catch(error=>{draft.error=error.message;render();});
        }else{
          preferenceBusy=true;preferenceError='';choice.disabled=true;
          intentReady=request({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})},'/api/vpn-preference');
          intentReady.then(state=>{runtime=state;window.refreshVpnPowerStatus?.();}).catch(error=>{
            preferenceError=error.message;draft.enabled=Boolean(runtime?.enabled);choice.checked=draft.enabled;fields.hidden=!draft.enabled;
          }).finally(()=>{preferenceBusy=false;renderLive(runtime);render();});
        }
      };
      const renderLive=state=>{
        const live=host.querySelector('[data-vpn-live]');runtime=state;
        if(!setupPage&&!preferenceBusy){draft.enabled=Boolean(state?.enabled);choice.checked=draft.enabled;fields.hidden=!draft.enabled;choice.disabled=['pending','applying'].includes(state?.control_status);}
        const switching=['pending','applying'].includes(state?.control_status);
        live.hidden=!preferenceError&&!state?.configured&&!state?.control_error;
        live.textContent=preferenceError||state?.control_error||(switching?'Switching connection…':state?.app_vpn_active?'VPN active for Picker'+(state.vpn_public_ip?' · '+state.vpn_public_ip:''):!state?.enabled?'VPN disabled in Settings. Normal internet is active.':!state?.desired_on?'VPN power is off. Normal internet is active.':'VPN unavailable. Protected playback is blocked.');
      };
      const labels={tunnel_healthy:'VPN tunnel',egress_changed:'VPN public IP',dns_resolved:'DNS resolution',internet_blocked_when_vpn_down:'Kill switch',lan_reachable:'LAN access',lan_reachable_when_vpn_down:'LAN access while VPN is down',tailscale_host_reachable:'Tailscale host access',tailscale_host_reachable_when_vpn_down:'Tailscale host access while VPN is down',tunnel_recovers:'VPN recovery'};
      const render=()=>{
        if(!host.isConnected)return;
        const passed=window.vpnWizard.canContinue()&&draft.enabled;
        const running=draft.busy||['pending','running'].includes(draft.test?.status);
        clear.hidden=!draft.file;retry.hidden=!draft.file||running||(!draft.error&&draft.test?.status!=='failed');
        indicator.className=running?'vpn-spinner':passed?'vpn-passed':'';indicator.textContent=passed?'✓':draft.error?'!':'';
        indicator.setAttribute('aria-label',running?'Testing VPN':passed?'VPN tests passed':draft.error?'VPN test failed':'No configuration selected');
        message.classList.toggle('vpn-success',passed);
        message.textContent=draft.error||(passed?'VPN tests passed':running?(draft.test?.stage||'Uploading and testing VPN configuration…'):draft.test?.status==='failed'?'VPN tests failed. Check your configuration and try again.':'Choose a WireGuard configuration file to test the VPN.');
        const next=document.getElementById('setupNext');if(next){const disabled=!window.vpnWizard.canContinue();next.dataset.modeDisabled=String(disabled);next.disabled=disabled;}
        results.replaceChildren();const note=document.createElement('p');note.className='vpn-help';
        note.textContent='Tests run in an isolated tunnel. Your app’s connection has not been changed.';results.append(note);
        if(!helper&&running){const info=document.createElement('p');info.className='vpn-help';info.textContent='Waiting for the VPN test service to become available.';results.append(info);}
        if(draft.test?.results?.checks){const list=document.createElement('ul');for(const [key,label] of Object.entries(labels)){if(!(key in draft.test.results.checks))continue;const item=document.createElement('li'),marker=document.createElement('span'),passed=draft.test.results.checks[key]===true;marker.className=passed?'vpn-check-pass':'vpn-check-fail';marker.textContent=passed?'✓':'✕';marker.setAttribute('role','img');marker.setAttribute('aria-label',passed?'Passed':'Failed');item.append(marker,document.createTextNode(' '+label));list.append(item);}results.append(list);}
      };
      const renderResources=()=>{
        const info=resources[form.elements.provider.value],links=host.querySelector('[data-vpn-links]');links.replaceChildren();host.querySelector('[data-vpn-provider-note]').textContent=info?.note||'';
        if(info)for(const [key,label] of [['website',info.name],['guide',info.guide_label||'Setup guide'],['pricing',info.pricing_label||'Pricing']]){
          if(!info[key])continue;let url;try{url=new URL(info[key]);}catch{continue;}if(url.protocol!=='https:'||url.username||url.password)continue;
          const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.textContent=label+' ↗';links.append(link);
        }
      };
      resourcesPromise.then(data=>{resources=data.providers||{};for(const option of form.elements.provider.options)if(resources[option.value])option.textContent=resources[option.value].name;renderResources();});
      const invalidate=()=>{draft.version++;draft.profile=null;draft.test=null;draft.busy=false;draft.error='';render();};
      const start=async()=>{
        if(!draft.enabled||!draft.file)return;
        const version=++draft.version,file=draft.file,lan=form.elements.lan.value;
        draft.lan=lan;draft.busy=true;draft.error='';draft.test=null;draft.profile=null;render();
        const current=()=>version===draft.version&&host.isConnected;
        try{
          await intentReady;if(!current())return;
          if(file.size>32768)throw Error('Use a configuration file under 32 KB.');
          const text=await file.text();if(!current())return;
          if(draft.providerSource!=='manual'){
            const detected=detectProvider(text,file.name);
            if(detected&&Array.from(form.elements.provider.options).some(option=>option.value===detected)){
              form.elements.provider.value=detected;draft.providerSource='file';renderResources();
              const notice=host.querySelector('[data-vpn-provider-detected]');notice.hidden=false;notice.textContent='Detected '+(resources[detected]?.name||form.elements.provider.selectedOptions[0].textContent)+' from the file. You can change the provider above.';
            }
          }
          const provider=form.elements.provider.value;
          if(!provider)throw Error('The file does not clearly identify its provider. Choose a VPN provider before testing.');
          draft.provider=provider;
          const uploaded=await request({method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider,lan_subnets:lan.split(',').map(v=>v.trim()).filter(Boolean),wireguard_config:text})});
          if(!current()){await request({method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:uploaded.profile_id})});return;}draft.profile=uploaded.profile_id;
          // A replacement waits for an existing isolated probe to finish before queuing its own test.
          const until=Date.now()+600000;
          while(current()){
            try{const state=await request({method:'POST'},'/api/vpn-test');if(!current())return;helper=state.helper_available;draft.test=state.test;draft.busy=false;render();return;}
            catch(error){if(error.status!==409||!error.message.includes('already queued')||Date.now()>until)throw error;message.textContent='Waiting for the previous VPN test to finish…';await new Promise(resolve=>setTimeout(resolve,3000));}
          }
        }catch(error){if(current()){draft.busy=false;draft.error=error.message;render();}}
      };
      form.onsubmit=event=>event.preventDefault();
      form.elements.profile.onchange=()=>{
        clearTimeout(debounce);draft.file=form.elements.profile.files[0]||null;
        if(draft.providerSource==='file'){draft.provider=null;draft.providerSource=null;form.elements.provider.value='';renderResources();}
        host.querySelector('[data-vpn-provider-detected]').hidden=true;invalidate();void start();
      };
      let dragDepth=0;
      const endDrag=()=>{dragDepth=0;host.classList.remove('vpn-drag-over');};
      host.addEventListener('dragenter',event=>{if(isFileDrag(event)){event.preventDefault();dragDepth++;host.classList.add('vpn-drag-over');}});
      host.addEventListener('dragleave',event=>{if(isFileDrag(event)&&--dragDepth<=0)endDrag();});
      host.addEventListener('dragover',event=>{if(isFileDrag(event)){event.preventDefault();event.dataTransfer.dropEffect='copy';}});
      host.addEventListener('drop',event=>{
        if(!isFileDrag(event))return;
        event.preventDefault();event.stopPropagation();endDrag();
        const files=Array.from(event.dataTransfer.files);
        if(files.length!==1){draft.error='Drop one WireGuard configuration file at a time.';render();return;}
        const file=files[0];
        if(!/\.(conf|config|ini)$/i.test(file.name)){draft.error='Use a WireGuard .conf, .config, or .ini file.';render();return;}
        if(file.size>32768){draft.error='Use a configuration file under 32 KB.';render();return;}
        choiceEdited=true;draft.enabled=true;choice.checked=true;fields.hidden=false;
        syncIntent();
        const transfer=new DataTransfer();transfer.items.add(file);form.elements.profile.files=transfer.files;
        form.elements.profile.dispatchEvent(new Event('change',{bubbles:true}));
      });
      const changed=()=>{draft.provider=form.elements.provider.value;draft.lan=form.elements.lan.value;invalidate();clearTimeout(debounce);debounce=setTimeout(start,650);};
      form.elements.provider.onchange=()=>{draft.providerSource=form.elements.provider.value?'manual':null;host.querySelector('[data-vpn-provider-detected]').hidden=true;renderResources();changed();};form.elements.lan.oninput=changed;
      choice.onchange=()=>{choiceEdited=true;draft.enabled=choice.checked;fields.hidden=!choice.checked;syncIntent();if(!choice.checked){clearTimeout(debounce);invalidate();void request({method:'DELETE'}).catch(()=>{});}else{render();void start();}};
      clear.onclick=async()=>{clearTimeout(debounce);draft.file=null;form.elements.profile.value='';host.querySelector('[data-vpn-provider-detected]').hidden=true;if(draft.providerSource==='file'){draft.provider=null;draft.providerSource=null;form.elements.provider.value='';renderResources();}invalidate();try{await request({method:'DELETE'});}catch(error){draft.error=error.message;render();}};
      retry.onclick=start;
      const poll=async()=>{
        if(!host.isConnected)return;const version=draft.version;
        try{const [state,connection]=await Promise.all([request({},'/api/vpn-test'),request({},'/api/vpn-state')]);if(host.isConnected&&!preferenceBusy)renderLive(connection);if(host.isConnected&&version===draft.version&&!draft.busy){helper=state.helper_available;
          if(draft.profile&&state.test?.profile_id===draft.profile){draft.test=state.test;if(state.test.profile_changed){draft.error='The temporary upload expired. Select the file again.';}else if(state.test.status==='failed'){draft.error=state.test.results?.error||'VPN tests failed. Check your configuration and retry.';}render();}
        }}catch{helper=false;render();}
        if(host.isConnected)setTimeout(poll,2000);
      };
      const initialVersion=draft.version;
      request().then(data=>{
        if(setupPage&&!choiceEdited&&draft.version===initialVersion){draft.enabled=Boolean(data.vpn_runtime?.enabled);choice.checked=draft.enabled;fields.hidden=!draft.enabled;}
        renderLive(data.vpn_runtime);
        for(const value of data.providers){const option=document.createElement('option');option.value=value;option.textContent=resources[value]?.name||(value==='custom'?'Custom WireGuard':value);form.elements.provider.append(option);}
        form.elements.provider.value=draft.provider??(!setupPage&&data.vpn_runtime?.configured?data.provider:'');form.elements.lan.value=draft.lan??(data.lan_subnets.length?data.lan_subnets.join(', '):data.suggested_lan_subnet||'');renderResources();
        const suggested=data.suggested_lan_subnet;
        host.querySelector('[data-vpn-lan-note]').textContent=data.lan_subnet_detection==='detected'?`Detected from your network adapter: ${suggested}. ${suggested.endsWith('/24')?`For normal home networks, ${suggested} is the most common configuration. `:''}Separate multiple subnets with commas.`:suggested?`For normal home networks, ${suggested} is the most common configuration. This is an estimate; confirm it matches your router.`:'Enter your router’s LAN subnet; separate multiple subnets with commas.';
        render();void poll();
      }).catch(error=>{draft.error=error.message;const live=host.querySelector('[data-vpn-live]');live.hidden=false;live.textContent=error.message;render();});
      render();
    }
  };
  window.mountVpnConfiguration();
})();
