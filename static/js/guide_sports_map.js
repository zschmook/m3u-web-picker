(() => {
 const root=document.getElementById('sports-app');
 if(!root)return;
 const stage=document.getElementById('map-stage'),svg=document.getElementById('state-map'),shapes=document.getElementById('state-shapes'),lines=document.getElementById('connections'),layer=document.getElementById('bubble-layer');
 const $=id=>root.querySelector('#'+id),NS='http://www.w3.org/2000/svg',PAGE_SIZE=6;
 let data={states:[]},states=[],selected='PA',mode='map',gameIndex=0,page=0,positions=[],baseNodes=[],bubbleGeometry=null,league='',feedIndex=0;
 let active=false,loaded=false,pendingFocus=false;
 let refreshing=false,navZone='content',toolbarIndex=0,leagueMenuOpen=false,leagueIndex=0,leagueValues=[];
 const sportColors={Football:'var(--football)',Hockey:'var(--hockey)',Baseball:'var(--baseball)',Basketball:'var(--basketball)',Soccer:'var(--soccer)'};
 function visibleStates(){return data.states.map(s=>({...s,games:s.games.filter(g=>!league||g.league===league)})).filter(s=>!s.auxiliary||s.games.length);}
 function selectedState(){return states.find(s=>s.id===selected)||states[0];}
 function remember(){if(!loaded||!selectedState())return;try{localStorage.setItem('m3u-guide-sports-map-view-v1',JSON.stringify({state:selected,mode,league,gameId:selectedState().games[gameIndex]?.id,weighted:$('weighted').checked}));}catch(_){}}
 function restore(){try{const old=JSON.parse(localStorage.getItem('m3u-guide-sports-map-view-v1'));if(!old)return;if(!data.initialLeague)league=old.league||'';states=visibleStates();if(states.some(s=>s.id===old.state))selected=old.state;mode=['map','state','game'].includes(old.mode)?old.mode:'map';const found=selectedState().games.findIndex(g=>g.id===old.gameId);gameIndex=Math.max(0,found);page=Math.floor(gameIndex/PAGE_SIZE);$('weighted').checked=old.weighted===true;if(mode==='game'&&found<0)mode='state';}catch(_){}}
 function leagueOptions(){
  const values=[...new Set(data.states.flatMap(s=>s.games.map(g=>g.league)))].sort();if(league&&!values.includes(league))league='';
  if($('league-filter').dataset.values!==values.join('|')){
   const focusedValue=leagueValues[leagueIndex];leagueValues=['',...values];$('league-options').replaceChildren();
   for(const value of leagueValues){const option=document.createElement('button');option.type='button';option.setAttribute('role','option');option.tabIndex=-1;option.dataset.league=value;option.textContent=value||'All sports';option.addEventListener('click',()=>chooseLeague(value));$('league-options').append(option);}
   leagueIndex=Math.max(0,leagueValues.indexOf(focusedValue??league));$('league-filter').dataset.values=values.join('|');
  }
  $('league-filter').textContent=(league||'All sports')+' ▾';$('league-filter').setAttribute('aria-label','Sport or league: '+(league||'All sports'));
  for(const option of $('league-options').children)option.setAttribute('aria-selected',String(option.dataset.league===league));
 }
 function toolbarControls(){return [$('league-filter'),$('weighted'),$('collapse')].filter(el=>el.getClientRects().length&&!el.hidden);}
 function remoteHelp(){return navZone==='leagues'?'↑ / ↓: leagues · OK: choose · Back: cancel':navZone==='toolbar'?'← / →: controls · OK: change · ↓: map':mode==='map'?'Arrows: regions · ↑ past map: filters · OK: open':mode==='state'?'← / →: games · ↑: filters · OK: feeds':'Arrows: feeds · OK: watch · Back: games';}
 function focusToolbar(index=0){const controls=toolbarControls();toolbarIndex=(index+controls.length)%controls.length;navZone='toolbar';controls[toolbarIndex].focus({preventScroll:true});$('remote-help').textContent=remoteHelp();}
 function focusContent(){navZone='content';render(true);}
 function closeLeagueMenu(focus=true){leagueMenuOpen=false;$('league-options').hidden=true;$('league-filter').setAttribute('aria-expanded','false');if(focus)focusToolbar(0);}
 function focusLeagueOption(){const option=$('league-options').children[leagueIndex];option?.focus({preventScroll:true});option?.scrollIntoView({block:'nearest',inline:'nearest'});$('remote-help').textContent=remoteHelp();}
 function openLeagueMenu(){leagueMenuOpen=true;navZone='leagues';leagueIndex=Math.max(0,leagueValues.indexOf(league));$('league-options').hidden=false;$('league-filter').setAttribute('aria-expanded','true');focusLeagueOption();}
 function chooseLeague(value){league=value;mode='map';gameIndex=0;page=0;closeLeagueMenu(false);navZone='toolbar';render();remember();focusToolbar(0);}
 function announce(text){$('announcement').textContent=text;}
 function setSelection(id){closeLeagueMenu(false);navZone='content';selected=id;mode='state';gameIndex=0;page=0;render(true,true);remember();const state=selectedState();announce(`${state.name}, ${state.games.length} game${state.games.length===1?'':'s'}`);}
 function openGame(index){navZone='content';gameIndex=index;feedIndex=0;mode='game';render(true);remember();announce('Selected '+selectedState().games[gameIndex].name);}
 function goBack(){navZone='content';if(mode==='map')return;mode=mode==='game'?'state':'map';render(true);remember();}
 function drawMap(width,height,shiftX=0,shiftY=0){
  shapes.replaceChildren();
  for(const n of baseNodes){
   const state=states.find(s=>s.id===n.id),active=state.id===selected;
   const group=document.createElementNS(NS,'g');group.setAttribute('class','state'+(state.country==='CA'?' province':'')+(state.games.length?' has-games':'')+(active?' is-selected':''));group.setAttribute('data-state',state.id);group.setAttribute('role','button');group.setAttribute('tabindex',active?'0':'-1');group.setAttribute('aria-label',`${state.name}${state.country==='CA'?', Canada':''}, ${state.games.length} game${state.games.length===1?'':'s'}`);
   group.setAttribute('aria-expanded',String(active&&mode!=='map'));
   const rect=document.createElementNS(NS,'rect'),text=document.createElementNS(NS,'text');
   const side=n.side,x=n.x+shiftX,y=n.y+shiftY;
   rect.setAttribute('x',x-side/2);rect.setAttribute('y',y-side/2);rect.setAttribute('width',side);rect.setAttribute('height',side);
   text.setAttribute('x',x);text.setAttribute('y',y+.5);text.textContent=state.id==='??'?'?':state.id;
   if(state.id.length>2)text.style.fontSize='11px';
   group.append(rect,text);group.addEventListener('click',()=>{if(mode==='state'&&active)goBack();else setSelection(state.id);});shapes.append(group);
  }
  svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
 }
 function render(focus=false,animate=false){
  if(!active||!loaded||!data.states.length)return;
  const oldFocusedGame=document.activeElement?.dataset.gameId,oldFocusedFeed=document.activeElement?.dataset.feedId;
  leagueOptions();
  states=visibleStates();if(!states.some(s=>s.id===selected))selected=states[0]?.id;
  if(!selected)return;
  const state=selectedState(),games=state.games;
  if(!games.length){gameIndex=0;page=0;if(mode==='game')mode='state';}
  gameIndex=Math.max(0,Math.min(gameIndex,games.length-1));page=Math.floor(gameIndex/PAGE_SIZE);
  const width=stage.getBoundingClientRect().width,base=SportsTileLogic.layout(states.map(s=>s.auxiliary?{...s,games:s.games.slice(0,1)}:s),$('weighted').checked,width);
  let height=Math.max(360,Math.min(580,window.innerHeight-root.getBoundingClientRect().top-300,width*.64)),shiftX=0,shiftY=0;
  const scale=Math.min(1,height/base.height),offsetX=width*(1-scale)/2,offsetY=width<620&&mode==='state'?0:(height-base.height*scale)/2;
  baseNodes=base.nodes.map(n=>({...n,x:n.x*scale+offsetX,y:n.y*scale+offsetY,side:n.side*scale}));
  const anchor=baseNodes.find(n=>n.id===selected);
  anchor.mapHeight=base.height*scale;
  const chunk=games.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
  bubbleGeometry=mode==='state'?layoutBubbles(width,height,anchor,chunk.length):null;
  if(bubbleGeometry)height=bubbleGeometry.height;
  stage.style.height=height+'px';stage.classList.toggle('is-open',mode!=='map');drawMap(width,height,shiftX,shiftY);
  $('selection').textContent=state.name+(state.country==='CA'?' · Canada':'');
  const leagues=new Set(games.map(g=>g.league));
  $('selection-count').textContent=`${games.length} game${games.length===1?'':'s'}${games.length?' · '+leagues.size+' league'+(leagues.size===1?'':'s'):''}`;
  const filteredGames=states.flatMap(s=>s.games),feedCount=filteredGames.reduce((n,g)=>n+(g.feeds?.length||0),0);
  $('coverage').textContent=`${filteredGames.length} events · ${feedCount} generated channels · ${league||'All sports'}`;
  $('freshness').textContent=(data.cacheLoading?'Updating map · ':data.cacheStale?'Showing saved map · ':'Updated ')+data.snapshot+' · Refreshes every 30 seconds';
  $('location-status').textContent=`${data.unlocatedGames||0} events need venue details`;
  $('collapse').hidden=mode==='map';$('empty-state').hidden=mode!=='state'||games.length>0;
  $('game-detail').hidden=mode!=='game';$('page-controls').hidden=mode!=='state'||games.length<=PAGE_SIZE;
  $('page-count').textContent=`${page+1} / ${Math.ceil(games.length/PAGE_SIZE)}`;
  $('map-hint').textContent=mode==='map'?'Pick a state or province. Only generated sports channels appear.':mode==='state'?'One bubble per event. Open it to choose a generated feed.':'Choose a feed to watch through Picker.';
  $('remote-help').textContent=remoteHelp();
  layer.replaceChildren();lines.replaceChildren();positions=[];
  if(mode==='state'&&games.length){
   positions=bubbleGeometry.positions;
   chunk.forEach((game,index)=>{
    const point=positions[index],line=document.createElementNS(NS,'line');
    line.setAttribute('x1',bubbleGeometry.hub.x);line.setAttribute('y1',bubbleGeometry.hub.y);line.setAttribute('x2',point.x);line.setAttribute('y2',point.y);lines.append(line);
    const button=document.createElement('button');button.type='button';button.className='bubble'+(page*PAGE_SIZE+index===gameIndex?' is-focused':'')+(animate?' entering':'');
    button.style.left=point.x+'px';button.style.top=point.y+'px';button.style.width=button.style.height=bubbleGeometry.size+'px';button.style.setProperty('--sport',sportColors[game.sport]||'var(--focus)');
    if(animate)button.style.animationDelay=(index*35)+'ms';
    const gameLabel=game.away&&game.home?`${game.away} at ${game.home}`:game.title||game.name;
    button.setAttribute('data-game-id',game.id);button.setAttribute('aria-label',`${page*PAGE_SIZE+index+1} of ${games.length}. ${game.league}: ${gameLabel}, ${game.live?'Live, ':''}${game.clock}`);
    function label(className,value,parent=button){if(!value)return;const span=document.createElement('span');span.className=className;span.textContent=value;parent.append(span);}
    label('league',game.league);label('matchup',game.name);
    const statusRow=document.createElement('span');statusRow.className='game-status';
    if(game.live||game.phase==='on-air')label('live','● '+game.statusLabel,statusRow);
    label('score',game.score,statusRow);if(statusRow.childElementCount)button.append(statusRow);
    if(!(game.phase==='on-air'&&game.clock==='On air'))label('clock',game.clock);
    label('feed-count',`${game.feeds?.length||0} feed${game.feeds?.length===1?'':'s'}`);
    button.addEventListener('click',()=>openGame(page*PAGE_SIZE+index));
    button.addEventListener('focus',()=>{gameIndex=page*PAGE_SIZE+index;layer.querySelectorAll('.bubble').forEach((b,i)=>b.classList.toggle('is-focused',i===index));remember();});
    layer.append(button);
   });
  }
  if(mode==='game'&&games[gameIndex]){
   const game=games[gameIndex];$('detail-league').textContent=game.league+' · '+game.sport;$('detail-matchup').textContent=game.title||game.name;
   $('detail-time').textContent=[game.statusLabel,game.score,game.clock].filter(Boolean).join(' · ');$('detail-venue').textContent=[game.venue,game.city].filter(Boolean).join(' · ');
   $('feed-list').replaceChildren();$('feed-message').textContent='';
   for(const feed of game.feeds||[]){
    const row=document.createElement('div');row.className='feed-row';
    const caption=document.createElement('span');caption.textContent=`Ch ${feed.number} · ${feed.name}`;
    const link=document.createElement('button');link.type='button';link.textContent=feed.available===false?'Unavailable':'Watch';link.className='watch-feed';link.dataset.feedId=feed.id;
    link.disabled=feed.available===false;link.setAttribute('aria-label',`Watch channel ${feed.number}, ${feed.name}`);link.addEventListener('click',()=>root.dispatchEvent(new CustomEvent('picker:watch-channel',{bubbles:true,detail:{feed,event:game}})));
    link.addEventListener('focus',()=>{feedIndex=[...$('feed-list').querySelectorAll('.watch-feed:not(:disabled)')].indexOf(link);});row.append(caption,link);$('feed-list').append(row);
   }
  }
  if(focus||oldFocusedGame||oldFocusedFeed){if(mode==='state'&&games.length)layer.children[gameIndex%PAGE_SIZE]?.focus({preventScroll:true});else if(mode==='game'){const links=[...$('feed-list').querySelectorAll('.watch-feed:not(:disabled)')],target=links.find(l=>l.dataset.feedId===oldFocusedFeed)||links[feedIndex]||$('detail-back');target.focus({preventScroll:!focus});}else shapes.querySelector(`[data-state="${selected}"]`)?.focus({preventScroll:true});}
  announce(mode==='map'?`${state.name}, ${games.length} games`:mode==='state'?`${state.name}, game bubbles open`:mode==='game'?'Game selected':'');
 }
 function changePage(delta){const games=selectedState().games,pages=Math.ceil(games.length/PAGE_SIZE);if(!pages)return;page=(page+delta+pages)%pages;gameIndex=page*PAGE_SIZE;render(true,true);remember();}
 const remoteFlashTimers=new Map();
 function flashRemote(key){
  const button=root.querySelector(`.remote [data-key="${key}"]`);if(!button)return;
  clearTimeout(remoteFlashTimers.get(button));button.classList.add('is-key-pressed');
  remoteFlashTimers.set(button,setTimeout(()=>{button.classList.remove('is-key-pressed');remoteFlashTimers.delete(button);},300));
 }
 function action(key){
  if(!active||!loaded||!states.length)return;
  if(navZone==='leagues'){
   if(key==='back')return closeLeagueMenu();
   if(key==='ok')return chooseLeague(leagueValues[leagueIndex]);
   leagueIndex=(leagueIndex+(['up','left','prev'].includes(key)?-1:1)+leagueValues.length)%leagueValues.length;focusLeagueOption();return;
  }
  if(navZone==='toolbar'){
   if(key==='back'||key==='down')return focusContent();
   if(['left','right','prev','next'].includes(key))return focusToolbar(toolbarIndex+(['left','prev'].includes(key)?-1:1));
   if(key==='ok'){const control=toolbarControls()[toolbarIndex];control?.click();if(navZone==='toolbar')focusToolbar(toolbarIndex);}return;
  }
  if(key==='back')return goBack();
  if(key==='up'&&mode==='state')return focusToolbar();
  if(mode==='game'){const links=[...$('feed-list').querySelectorAll('.watch-feed:not(:disabled)')];if(key==='ok')links[feedIndex]?.click();else if(links.length){feedIndex=(feedIndex+(['left','up','prev'].includes(key)?-1:1)+links.length)%links.length;links[feedIndex].focus();}return;}
  if(key==='ok'){if(mode==='map')return setSelection(selected);if(mode==='state'&&selectedState().games.length)return openGame(gameIndex);return;}
  if(key==='next'||key==='prev'){
   if(mode==='state'&&selectedState().games.length>PAGE_SIZE)return changePage(key==='next'?1:-1);
   if(mode==='state'&&selectedState().games.length){gameIndex=(gameIndex+(key==='next'?1:-1)+selectedState().games.length)%selectedState().games.length;render(true);remember();return;}
   selected=SportsTileLogic.nextLive(states,selected,key==='next'?1:-1);gameIndex=0;page=0;
  }else if(mode==='map'){const next=SportsTileLogic.navigate(states,selected,key);if(key==='up'&&next===selected)return focusToolbar();selected=next;}
  else if(mode==='state'&&positions.length){
   if(key==='left'||key==='right')gameIndex=(gameIndex+(key==='right'?1:-1)+selectedState().games.length)%selectedState().games.length;
   else gameIndex=page*PAGE_SIZE+neighbor(positions,gameIndex%PAGE_SIZE,key);
  }
  render(true);remember();
 }
 root.querySelectorAll('[data-key]').forEach(button=>button.addEventListener('click',()=>action(button.dataset.key)));
 $('collapse').addEventListener('click',goBack);$('detail-back').addEventListener('click',goBack);
 $('previous-page').addEventListener('click',()=>changePage(-1));$('next-page').addEventListener('click',()=>changePage(1));
 $('weighted').addEventListener('change',()=>{render();remember();});
 $('league-filter').addEventListener('click',()=>leagueMenuOpen?closeLeagueMenu():openLeagueMenu());
 root.addEventListener('focusin',event=>{
  if(event.target.closest('#league-options'))navZone='leagues';
  else if(event.target.closest('.toolbar-actions')){navZone='toolbar';toolbarIndex=Math.max(0,toolbarControls().indexOf(event.target));}
  else if(!event.target.closest('.remote'))navZone='content';
  $('remote-help').textContent=remoteHelp();
 });
 root.addEventListener('click',event=>{
  if(!active||!loaded)return;
  if(leagueMenuOpen){if(!event.target.closest('.league-option,.remote'))closeLeagueMenu();return;}
  // Empty page space acts like Back; tiles, games and controls keep their own actions.
  if(mode==='map'||event.target.closest('[data-state],.bubble,.game-detail,button,a,input,select,label,[role="button"]'))return;
  goBack();
 });
 root.addEventListener('keydown',event=>{
  if(!active||!loaded)return;
  if(event.ctrlKey||event.metaKey||event.altKey||event.target.isContentEditable||event.target.closest('select,textarea,input:not([type="checkbox"]):not([type="radio"]):not([type="button"])'))return;
  const key={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',Enter:'ok',Escape:'back',Backspace:'back',PageUp:'prev',PageDown:'next','=':'next','+':'next','-':'prev',Add:'next',Subtract:'prev'}[event.key];
  if(navZone!=='content'&&(key||event.key===' ')){event.preventDefault();const command=event.key===' '?'ok':key;flashRemote(command);action(command);return;}
  if((event.key==='Enter'&&event.target.closest('button,a,input'))||(event.key===' '&&event.target.closest('button'))){
   if(!event.target.closest('input'))flashRemote(event.target.closest('[data-key]')?.dataset.key||(['collapse','detail-back'].includes(event.target.closest('button')?.id)?'back':'ok'));
   return;
  }
  if(event.key===' '&&event.target.closest('[data-state]')){event.preventDefault();flashRemote('ok');setSelection(event.target.closest('[data-state]').dataset.state);return;}
  if(!key)return;event.preventDefault();flashRemote(key);action(key);
 });
 async function refresh(){
  if(!active||document.hidden||refreshing)return;
  refreshing=true;
  try{
   const response=await fetch('/api/guide/sports-map',{cache:'no-store'});
   if(!response.ok)throw Error('Map update unavailable');
   const updated=await response.json();
   if(!Array.isArray(updated.states)||updated.source!=='picker-generated')throw Error('Invalid map data');
   updated.snapshot=updated.updatedAt?new Date(updated.updatedAt).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'saved snapshot';
   updated.unlocatedGames=updated.states.find(s=>s.id==='??')?.games.length||0;
   const gameId=selectedState()?.games[gameIndex]?.id;
   const changed=!loaded||updated.updatedAt!==data.updatedAt||updated.cacheLoading!==data.cacheLoading||updated.cacheStale!==data.cacheStale;
   if(changed){
    data=updated;states=visibleStates();
    if(!loaded){loaded=true;restore();}
    else{
     const index=states.find(s=>s.id===selected)?.games.findIndex(g=>g.id===gameId)??-1;
     if(index>=0)gameIndex=index;else{gameIndex=0;if(mode==='game')mode='state';}
    }
    render(pendingFocus);pendingFocus=false;
   }
   $('freshness').textContent=(updated.cacheLoading?'Updating map · ':updated.cacheStale?'Showing saved map · ':'Updated ')+data.snapshot+' · Refreshes every 30 seconds';
   if(updated.cacheRefreshFailed)$('freshness').textContent='Update unavailable · Showing saved map from '+data.snapshot;
  }catch(_){
   $('freshness').textContent=loaded?'Connection interrupted · Showing saved map from '+data.snapshot:'Sports map unavailable. Use Refresh to try again.';
  }finally{refreshing=false;}
 }
 window.GuideSportsMap={
  refresh,
  show({focus=false}={}){
   active=true;pendingFocus=focus;
   if(loaded){render(focus);pendingFocus=false;}
   void refresh();
  },
  hide(){active=false;closeLeagueMenu(false);navZone='content';},
  selectState:setSelection,
  getSelection:()=>({state:selected,mode,eventId:selectedState()?.games[gameIndex]?.id,league}),
 };
 let lastWidth=0;
 new ResizeObserver(()=>{
  const width=stage.getBoundingClientRect().width;
  if(active&&Math.abs(width-lastWidth)>1){lastWidth=width;render();}
 }).observe(stage);
 window.addEventListener('resize',()=>render());
 setInterval(refresh,30000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh();});
})();
