(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  if(!el('ccEnabled'))return;
  let state=null,busy=false,poller=null,commercialPoller=null,importBusy=false;
  let editingChannel=null,deletingChannel=null;
  let bulkSelection=null,bulkRemoval=null,bulkMode='add';
  const choices=new Map();
  const text=(tag,value,className='')=>{const n=document.createElement(tag);n.textContent=value;n.className=className;return n;};
  const status=(message,error=false)=>{el('ccStatus').textContent=message;el('ccStatus').className='ui-settings-status'+(error?' is-error':'');if(error)el('ccStatusPanel').open=true;};
  async function api(path='',method='GET',body){
    const response=await fetch('/api/custom-channels'+path,{method,cache:'no-store',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Request failed');return data;
  }
  function locks(){
    for(const id of ['ccEnabled','ccMoviesEnabled','ccMinimum','ccAds'])el(id).disabled=busy||!state;
    el('ccImportChoose').disabled=busy||importBusy||!state;
    el('ccImportInput').disabled=el('ccImportChoose').disabled;
    el('ccDiscover').disabled=busy||(!state?.settings.enabled&&!state?.settings.movies_enabled)||state?.job.running;
    if(el('ccConnect'))el('ccConnect').disabled=busy||!state||state.job.running;
    el('ccAddAll').disabled=busy||!state||!state.settings.enabled||Number(el('ccAddAll').dataset.count||0)===0;
    el('ccRemoveAll').disabled=busy||!state||Number(el('ccRemoveAll').dataset.count||0)===0;
    for(const id of ['ccEditSave','ccDeleteConfirm'])el(id).disabled=busy;
    el('ccAddAllConfirm').disabled=busy||(bulkMode==='remove'?!bulkRemoval?.showIds.length:!bulkSelection?.showIds.length);
    document.querySelectorAll('.cc-channel-action').forEach(button=>button.disabled=busy);
    document.querySelectorAll('.cc-movie-toggle').forEach(input=>input.disabled=busy||!state?.movies?.enabled);
    document.querySelectorAll('.cc-create').forEach(button=>button.disabled=busy||button.dataset.allowed!=='true');
    for(const id of ['ccAdMode','ccInterval','ccAdCount','ccPreroll'])el(id).disabled=busy||!state||!el('ccAds').checked;
    el('ccIntervalLabel').textContent=el('ccAdMode').value==='minutes'?'Minutes between breaks':'Episodes between breaks';
    el('ccInterval').max=el('ccAdMode').value==='minutes'?'240':'100';
  }
  async function action(fn){if(busy)return;busy=true;locks();try{return await fn();}catch(e){if(state)renderSettings();status(e.message,true);}finally{busy=false;locks();}}
  function renderSettings(){const s=state.settings;
    el('ccMoviesEnabled').checked=Boolean(s.movies_enabled);
    for(const [id,key] of [['ccEnabled','enabled'],['ccAds','commercials_enabled'],['ccPreroll','preroll']])el(id).checked=s[key];
    for(const [id,key] of [['ccMinimum','minimum_episodes'],['ccAdMode','commercial_mode'],['ccAdCount','commercial_count']])el(id).value=s[key];
    el('ccInterval').value=s.commercial_mode==='minutes'?s.commercial_minutes:s.commercial_episodes;
    const interval=s.commercial_mode==='minutes'?`${s.commercial_minutes} minutes of show time`:`${s.commercial_episodes} episode${s.commercial_episodes===1?'':'s'}`;
    const opening=s.preroll?'1 opening ad, then ':'';
    const summary=s.commercials_enabled?`${opening}${s.commercial_count} ad${s.commercial_count===1?'':'s'} every ${interval}`:'Off';
    el('ccCommercialCollapsedSummary').textContent=summary;
    el('ccCommercialSummary').textContent=s.commercials_enabled?`TV show channels play ${summary.toLowerCase()}.`:'TV show channels play without commercials.';
  }
  const formatBytes=value=>{if(value<1024)return `${value} B`;const units=['KB','MB','GB','TB'];let n=value,i=-1;do{n/=1024;i++;}while(n>=1024&&i<units.length-1);return `${n.toFixed(n>=10?1:2)} ${units[i]}`;};
  function renderImports(){
    const host=el('ccImports');host.replaceChildren();
    const loaded=(state.commercial_imports||[]).filter(item=>item.status==='complete').reduce((total,item)=>total+Number(item.clips_found||0),0);
    el('ccCommercialLoaded').textContent=`| ${loaded.toLocaleString()} loaded`;
    for(const item of (state.commercial_imports||[]).slice(0,5)){
      const row=text('div','','cc-import-row');row.append(text('span',item.name,'cc-import-name'));
      const label=item.status==='complete'?`${Number(item.clips_found||0).toLocaleString()} commercials · ${formatDuration(item.duration)}`:(item.message||(item.status==='ready'?'Ready for processing':`${formatBytes(item.received)} of ${formatBytes(item.size)}`));
      row.append(text('span',label,'small-muted cc-import-result'));host.append(row);
    }
    const current=(state.commercial_imports||[])[0];
    el('ccImportProgress').hidden=true;
    if(current&&['processing','failed'].includes(current.status)){
      el('ccImportProgress').hidden=false;el('ccImportName').textContent=current.name;el('ccImportMessage').textContent=current.message||'';
      let value=0,label='';
      if(current.phase==='validating'&&current.duration){value=current.processed/current.duration;label=`${current.processed} of ${current.duration} commercials`;}
      else if(current.duration){value=current.processed/current.duration;label=`${formatDuration(current.processed)} of ${formatDuration(current.duration)} footage`;}
      if(current.status==='complete')value=1;
      el('ccImportBar').style.width=`${Math.min(100,Math.max(0,value*100))}%`;el('ccImportNumbers').textContent=label;
    }
  }
  const formatDuration=value=>{value=Math.max(0,Math.floor(value||0));const h=Math.floor(value/3600),m=Math.floor(value%3600/60),s=value%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`;};
  function renderCatalog(){
    const minimum=state.settings.minimum_episodes,term=el('ccSearch').value.toLowerCase(),sort=el('ccSort').value;
    const eligible=state.shows.filter(s=>!s.stale&&s.count>=minimum).length;
    const unavailable=(state.catalog.servers||[]).reduce((total,s)=>total+(s.unavailable_files||0),0);
    const shows=state.shows.filter(s=>(el('ccShowAll').checked||(!s.stale&&s.count>=minimum))&&(s.title+' '+s.server).toLowerCase().includes(term));
    shows.sort((a,b)=>sort==='count'||sort==='hours'?b[sort]-a[sort]:a[sort].localeCompare(b[sort]));
    const host=el('ccShows');host.replaceChildren();
    el('ccCatalogSummary').textContent=`${shows.length} shown · ${eligible} series meet the ${minimum}-episode minimum · ${state.shows.length} series with available files${unavailable?` · ${unavailable.toLocaleString()} unavailable files excluded`:''}`;
    const channelsByShow=new Map(state.channels.map(channel=>[channel.show_id,channel])),existing=new Set(channelsByShow.keys()),addable=shows.filter(show=>!show.stale&&!existing.has(show.id)),removable=shows.map(show=>channelsByShow.get(show.id)).filter(Boolean);
    const episodes=addable.reduce((total,show)=>total+show.count,0),hours=addable.reduce((total,show)=>total+show.hours,0);
    bulkSelection={showIds:addable.map(show=>show.id),shown:shows.length,skipped:shows.length-addable.length,episodes,hours};
    bulkRemoval={showIds:removable.map(channel=>channel.show_id),shown:shows.length};
    el('ccAddAll').dataset.count=String(addable.length);el('ccAddAll').textContent=`Add All${addable.length?` ${addable.length}`:''}`;
    el('ccRemoveAll').dataset.count=String(removable.length);el('ccRemoveAll').textContent=`Remove All${removable.length?` ${removable.length}`:''}`;
    el('ccAddAllImpact').textContent=addable.length?`${addable.length} new channel${addable.length===1?'':'s'} · ${episodes.toLocaleString()} episodes · ${hours.toLocaleString(undefined,{maximumFractionDigits:1})} hours${bulkSelection.skipped?` · ${bulkSelection.skipped} existing skipped`:''}`:'Every shown series already has a channel.';
    if(!shows.length)host.append(text('p',state.shows.length?'No shows match these filters.':'Discover Plex to catalog your TV libraries.'));
    for(const show of shows){
      const row=text('div','','cc-show'),title=text('div',show.title,'cc-show-title');row.append(title);
      const existingChannel=channelsByShow.get(show.id);
      const choice=choices.get(show.id)||{order:'ordered',first:Math.min(...show.seasons),last:Math.max(...show.seasons)};choices.set(show.id,choice);
      const controls=text('div','','cc-range');
      const order=document.createElement('select');order.className='form-select';order.setAttribute('aria-label','Episode order for '+show.title);
      for(const [v,t]of [['ordered','In order (starts now)'],['random','Random order']]){const option=text('option',t);option.value=v;order.append(option);}order.value=choice.order;
      const orderLabel=text('label','','cc-order');orderLabel.append(text('span','Episode order','cc-order-label'),order);controls.append(orderLabel);
      const button=text('button',existingChannel?'Remove Channel':'Create Channel',existingChannel?'btn btn-danger cc-create':'btn ui-btn-primary cc-create');button.type='button';controls.append(button);row.append(controls);
      const count=text('div','','small-muted cc-selection-summary'),countText=text('span',''),gapSeparator=text('span',' | ','cc-gap-separator'),gaps=text('span','','cc-gap');count.append(countText,gapSeparator,gaps);row.append(count);
      function update(){choice.order=order.value;
        const selected=show.files.filter(f=>f.season>=choice.first&&f.season<=choice.last);
        const n=selected.reduce((v,f)=>v+(f.covered_episodes?.length||1),0);
        countText.textContent=`Selected: ${n} episodes / ${selected.length} files · ${(selected.reduce((v,f)=>v+f.duration,0)/3600).toFixed(1)} hours`;
        const seasons=new Map();for(const f of selected){if(!seasons.has(f.season))seasons.set(f.season,new Set());for(const ep of f.covered_episodes||[f.episode])seasons.get(f.season).add(ep);}
        const missing=[];
        if(choice.first>=1&&choice.last-choice.first<=200){for(let s=choice.first;s<=choice.last;s++){const found=seasons.get(s);if(!found){missing.push(`S${s}: All`);continue;}if(Math.min(...found)>100){continue;}const epMissing=[];let previous=0;for(const ep of [...found].sort((a,b)=>a-b)){if(ep>previous+1)epMissing.push(ep===previous+2?`${previous+1}`:`${previous+1}–${ep-1}`);previous=ep;}if(epMissing.length)missing.push(`S${s}: E${epMissing.slice(0,10).join(', ')}`);}}
        const missingSummary=missing.length?`Missing: ${missing.slice(0,10).join(' | ')}${missing.length>10?' | …':''}`:'';
        gaps.textContent=show.stale?'Server unavailable — cached catalog.':missingSummary;
        gapSeparator.hidden=!gaps.textContent;
        button.dataset.allowed=String(Boolean(existingChannel||(state.settings.enabled&&!show.stale&&(n>=minimum||el('ccShowAll').checked))));
        button.disabled=busy||button.dataset.allowed!=='true';
      }
      order.addEventListener('change',update);update();
      button.addEventListener('click',()=>{
        if(existingChannel){deletingChannel=existingChannel;el('ccDeleteMessage').textContent=`Remove ${existingChannel.number} · ${existingChannel.name}?`;el('ccDeleteDialog').showModal();return;}
        void action(async()=>{button.disabled=true;status('Building the channel schedule…');const data=await api('','POST',{show_id:show.id,order:choice.order,season_start:choice.first,season_end:choice.last,allow_below_minimum:el('ccShowAll').checked});await load(false);status(`Channel ${data.channel.number} created. Its live clock started now.`);});
      });
      host.append(row);
    }
  }
  function renderChannels(){const host=el('ccChannels');host.replaceChildren();
    if(!state.channels.length)host.append(text('div','No custom channels created yet.','small-muted'));
    for(const c of state.channels){
      const row=text('div','','cc-channel');
      if(c.kind==='category'){
        row.append(text('div',`${c.number} · ${c.name} · Mixed Plex category · Randomized on Plex refresh${c.enabled?'':' · Disabled'}`));host.append(row);continue;
      }
      row.append(text('div',`${c.number} · ${c.name} · S${c.season_start}–S${c.season_end} · ${c.order==='ordered'?'From start':'Random'}${c.enabled?'':' · Disabled'}`));
      const actions=text('div','','cc-channel-actions');
      const edit=text('button','Edit','btn ui-btn-secondary cc-channel-action');edit.type='button';edit.addEventListener('click',()=>{
        editingChannel=c;el('ccEditName').value=c.name;el('ccEditNumber').value=c.number;el('ccEditOrder').value=c.order;el('ccEditFirst').value=c.season_start;el('ccEditLast').value=c.season_end;el('ccEditDialog').showModal();
      });actions.append(edit);
      const remove=text('button','Delete','btn btn-outline-danger cc-channel-action');remove.type='button';remove.addEventListener('click',()=>{deletingChannel=c;el('ccDeleteMessage').textContent=`Delete ${c.number} · ${c.name}?`;el('ccDeleteDialog').showModal();});actions.append(remove);
      row.append(actions);host.append(row);
    }
  }
  function renderMovieChannels(){
    const movie=state.movies||{},rows=movie.channels||[],host=el('ccMovieChannels');host.replaceChildren();
    el('ccMovieSummary').textContent=`${rows.filter(row=>row.enabled).length} channels · ${Number(movie.catalog?.count||0).toLocaleString()} movies`;
    if(!rows.length)host.append(text('p','Enable movie channels and refresh to scan your Plex movie libraries.','small-muted'));
    for(const channel of rows){
      const row=text('label','','cc-channel'),toggle=document.createElement('input');toggle.type='checkbox';toggle.className='cc-movie-toggle';toggle.checked=channel.enabled;toggle.disabled=busy||!movie.enabled;
      toggle.setAttribute('aria-label','Enable '+channel.name+' movie channel');
      toggle.addEventListener('change',()=>action(async()=>{
        const response=await fetch('/api/movie-channels/'+channel.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:toggle.checked})});
        const data=await response.json();if(!response.ok)throw Error(data.error||'Could not update movie channel');await load(false);
      }));
      const copy=text('span',`${channel.number} · ${channel.name} · ${channel.count.toLocaleString()} movies`);
      if(channel.movies?.length)copy.append(text('small',channel.movies.map(movie=>`${movie.title} (${movie.release_date})`).join(' · '),'small-muted d-block'));
      row.append(copy,toggle);host.append(row);
    }
  }
  async function load(fields=true){state=await api();if(fields)renderSettings();renderCatalog();renderChannels();renderMovieChannels();renderImports();locks();
    if(!state.servers.length)el('ccConnectionPanel').open=true;
    const observed=state.catalog.updated_at?new Date(state.catalog.updated_at*1000).toLocaleString():'';
    el('ccLastChecked').textContent=observed?`Catalog snapshot from ${observed}. Unavailable servers contribute zero series.`:'No Plex catalog snapshot yet.';
    el('ccServers').replaceChildren(...state.servers.map(s=>{const result=state.catalog.servers?.find(v=>v.id===s.id);let value;if(!result)value=s.name+' (not checked)';else if(result.status==='unavailable')value=s.name+' (scan unavailable, 0 current series)';else value=s.name+` (${result.shows} series${result.unavailable_files?`, ${result.unavailable_files} unavailable files`:''})`;return text('div',value,'cc-server-status');}));
    el('ccStatusSummary').textContent=state.job.running?state.job.message:(observed?`Last refreshed ${observed}`:'Not scanned yet');
    if(state.job.running)el('ccStatusPanel').open=true;
    if(!state.job.running&&state.catalog.warnings?.length)status(state.catalog.warnings.join(' '),true);
    if(state.job.running){status(state.job.message);if(!poller)poller=setTimeout(async()=>{poller=null;try{await load(false);if(!state.job.running)status([state.job.message,...(state.catalog.warnings||[])].join(' '),Boolean(state.catalog.warnings?.length));}catch(e){status(e.message,true);}},1500);}
    if((state.commercial_imports||[]).some(item=>item.status==='processing')&&!commercialPoller)commercialPoller=setTimeout(async()=>{commercialPoller=null;try{await load(false);}catch(e){status(e.message,true);}},1500);
  }
  async function persist(values){
    status('Saving…');state=await api('/settings','PATCH',values);renderSettings();renderCatalog();renderChannels();status('Saved automatically.');
  }
  el('ccEnabled').addEventListener('change',()=>{
    if(el('ccEnabled').checked){el('ccEnabled').checked=state.settings.enabled;el('ccEnableDialog').showModal();}
    else void action(()=>persist({enabled:false}));
  });
  el('ccEnableCancel').addEventListener('click',()=>el('ccEnableDialog').close());
  el('ccEnableConfirm').addEventListener('click',()=>{el('ccEnableDialog').close();void action(async()=>{await persist({enabled:true});await api('/discover','POST',{});await load(false);});});
  for(const [id,key]of [['ccMinimum','minimum_episodes'],['ccAdCount','commercial_count']])el(id).addEventListener('change',()=>{if(el(id).reportValidity())void action(()=>persist({[key]:Number(el(id).value)}));});
  el('ccPreroll').addEventListener('change',()=>action(()=>persist({preroll:el('ccPreroll').checked})));
  el('ccInterval').addEventListener('change',()=>{if(el('ccInterval').reportValidity())void action(()=>persist({[el('ccAdMode').value==='minutes'?'commercial_minutes':'commercial_episodes']:Number(el('ccInterval').value)}));});
  el('ccDiscover').addEventListener('click',()=>action(async()=>{await api('/discover','POST',{});await load(false);}));
  el('ccConnectForm').addEventListener('submit',event=>{
    event.preventDefault();
    if(busy||!state||state.job.running)return;
    void action(async()=>{
      el('ccStatusPanel').open=true;
      status('Connecting to Plex…');
      state=await api('/servers','POST',{url:el('ccServerUrl').value.trim(),token:el('ccToken').value.trim()});
      el('ccToken').value='';el('ccConnectionPanel').open=false;
      if(state.settings.enabled||state.settings.movies_enabled){
        try{await api('/discover','POST',{});}
        catch(error){await load(false);status('Plex connected, but the library refresh failed. '+error.message,true);return;}
        await load(false);
      }else{await load(false);status('Plex connected. Enable TV show or movie channels to scan its libraries.');}
    });
  });
  ['ccSearch','ccSort','ccShowAll'].forEach(id=>el(id).addEventListener(id==='ccSearch'?'input':'change',()=>{if(state){renderCatalog();locks();}}));
  el('ccAddAll').addEventListener('click',()=>{
    if(!bulkSelection?.showIds.length)return;
    bulkMode='add';el('ccAddAllTitle').textContent='Create channels for all shown series?';el('ccAddAllNote').textContent='The channels will begin now, appear in the existing playlists and EPG, and keep running on their shared live clocks.';el('ccAddAllConfirm').className='btn ui-btn-primary';
    const details=el('ccAddAllDetails');details.replaceChildren(
      text('p',`${bulkSelection.showIds.length} new channel${bulkSelection.showIds.length===1?'':'s'} from ${bulkSelection.shown} shown series.`),
      text('p',`${bulkSelection.episodes.toLocaleString()} episodes · ${bulkSelection.hours.toLocaleString(undefined,{maximumFractionDigits:1})} hours of programming.`),
      text('p',`Episode order: In order (starts now). Commercials: ${state.settings.commercials_enabled?el('ccCommercialCollapsedSummary').textContent:'off'}.`),
      text('p',`${bulkSelection.skipped} existing channel${bulkSelection.skipped===1?'':'s'} will be skipped.`)
    );
    el('ccAddAllConfirm').textContent=`Create ${bulkSelection.showIds.length} Channel${bulkSelection.showIds.length===1?'':'s'}`;el('ccAddAllDialog').showModal();locks();
  });
  el('ccMoviesEnabled').addEventListener('change',()=>action(async()=>{
    const enabled=el('ccMoviesEnabled').checked;await persist({movies_enabled:enabled});
    if(enabled)await api('/discover','POST',{});await load(false);
  }));
  el('ccRemoveAll').addEventListener('click',()=>{
    if(!bulkRemoval?.showIds.length)return;
    bulkMode='remove';el('ccAddAllTitle').textContent='Remove channels for all shown series?';el('ccAddAllDetails').replaceChildren(
      text('p',`${bulkRemoval.showIds.length} existing channel${bulkRemoval.showIds.length===1?'':'s'} from ${bulkRemoval.shown} shown series.`)
    );
    el('ccAddAllNote').textContent='This removes their custom schedules. Your Plex files and manually saved IPTV channels are not changed.';
    el('ccAddAllConfirm').textContent=`Remove ${bulkRemoval.showIds.length} Channel${bulkRemoval.showIds.length===1?'':'s'}`;el('ccAddAllConfirm').className='btn btn-outline-danger';el('ccAddAllDialog').showModal();locks();
  });
  el('ccAddAllCancel').addEventListener('click',()=>el('ccAddAllDialog').close());
  el('ccAddAllConfirm').addEventListener('click',()=>{
    if(bulkMode==='remove'){
      if(!bulkRemoval?.showIds.length)return;const requested=[...bulkRemoval.showIds];
      void action(async()=>{status(`Removing ${requested.length} channel schedules…`);const data=await api('/bulk','DELETE',{show_ids:requested});el('ccAddAllDialog').close();await load(false);status(`Removed ${data.deleted.length} channel${data.deleted.length===1?'':'s'}.`);});return;
    }
    if(!bulkSelection?.showIds.length)return;const requested=[...bulkSelection.showIds],includeBelow=el('ccShowAll').checked;
    void action(async()=>{status(`Building ${requested.length} channel schedules…`);const data=await api('/bulk','POST',{show_ids:requested,allow_below_minimum:includeBelow});el('ccAddAllDialog').close();await load(false);status(`Created ${data.created.length} channels${data.skipped?`; skipped ${data.skipped} existing`:''}. Their live clocks started now.`);});
  });
  el('ccAds').addEventListener('change',()=>action(()=>persist({commercials_enabled:el('ccAds').checked})));
  el('ccAdMode').addEventListener('change',()=>action(()=>persist({commercial_mode:el('ccAdMode').value})));
  function showImportProgress(file,received,message){
    el('ccImportProgress').hidden=false;el('ccImportName').textContent=file.name;
    el('ccImportNumbers').textContent=`${formatBytes(received)} of ${formatBytes(file.size)}`;
    el('ccImportBar').style.width=`${Math.min(100,received/file.size*100)}%`;el('ccImportMessage').textContent=message;
  }
  async function importCommercials(file){
    if(importBusy)return;if(!file||!file.name.toLowerCase().endsWith('.mp4')){showImportProgress(file||{name:'',size:1},0,'Choose an MP4 file.');return;}
    importBusy=true;locks();const started=performance.now();
    try{
      showImportProgress(file,0,'Preparing local copy…');const created=await api('/commercial-imports','POST',{name:file.name,size:file.size});let item=created.import_,offset=item.received;
      while(offset<file.size){
        const end=Math.min(file.size,offset+8*1024*1024),response=await fetch(`/api/custom-channels/commercial-imports/${item.id}`,{method:'PUT',cache:'no-store',headers:{'Content-Type':'application/octet-stream','Upload-Offset':String(offset)},body:file.slice(offset,end)});
        const data=await response.json();if(!response.ok)throw Error(data.error||'The MP4 could not be copied.');item=data.import_;offset=item.received;
        const seconds=Math.max(.1,(performance.now()-started)/1000),speed=formatBytes(offset/seconds)+'/s';showImportProgress(file,offset,offset===file.size?'Starting commercial scan…':`Copying locally · ${speed}`);
      }
      await load(false);
    }catch(e){showImportProgress(file,Number(el('ccImportBar').style.width.replace('%',''))/100*file.size,e.message);}
    finally{importBusy=false;el('ccImportInput').value='';locks();}
  }
  el('ccImportChoose').addEventListener('click',()=>el('ccImportInput').click());
  el('ccImportInput').addEventListener('change',()=>void importCommercials(el('ccImportInput').files[0]));
  const drop=el('ccImportDrop');
  for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();if(!el('ccImportChoose').disabled)drop.classList.add('is-dragging');});
  for(const event of ['dragleave','drop'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('is-dragging');});
  drop.addEventListener('drop',e=>{if(!el('ccImportChoose').disabled)void importCommercials(e.dataTransfer.files[0]);});
  el('ccEditCancel').addEventListener('click',()=>el('ccEditDialog').close());
  el('ccEditSave').addEventListener('click',()=>{
    if(!editingChannel)return;
    const inputs=['ccEditName','ccEditNumber','ccEditFirst','ccEditLast'].map(el);if(inputs.some(input=>!input.reportValidity()))return;
    const values={name:el('ccEditName').value,number:el('ccEditNumber').value,order:el('ccEditOrder').value,season_start:Number(el('ccEditFirst').value),season_end:Number(el('ccEditLast').value)};
    void action(async()=>{const changedClock=values.order!==editingChannel.order||values.season_start!==editingChannel.season_start||values.season_end!==editingChannel.season_end;await api('/'+editingChannel.id,'PATCH',values);el('ccEditDialog').close();editingChannel=null;await load(false);status(changedClock?'Channel updated. Its live clock restarted now.':'Channel updated.');});
  });
  el('ccDeleteCancel').addEventListener('click',()=>el('ccDeleteDialog').close());
  el('ccDeleteConfirm').addEventListener('click',()=>{if(!deletingChannel)return;const label=`${deletingChannel.number} · ${deletingChannel.name}`;void action(async()=>{await api('/'+deletingChannel.id,'DELETE');el('ccDeleteDialog').close();deletingChannel=null;await load(false);status(`Deleted ${label}.`);});});
  locks();
  void load().catch(e=>status(e.message,true));
})();
