const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const {test,before}=require('node:test');
const brs=require('brs');
const root=path.join(__dirname,'..');
const logic=fs.readFileSync(path.join(root,'roku-receiver/components/SportsMapLogic.brs'),'utf8');
const compactTask=fs.readFileSync(path.join(root,'roku-receiver/components/ApiTask.brs'),'utf8').match(/sub compactSportsMapData\(data as Object\)[\s\S]*?end sub/)[0];
const originalUi=fs.readFileSync(path.join(root,'roku-receiver/components/SportsMap.brs'),'utf8');
const actualDraw=originalUi.match(/sub drawSportsMap\(\)[\s\S]*?end sub/)[0].replace('sub drawSportsMap()', 'sub drawSportsMapActual()');
const ui=originalUi
 // brs does not turn Roku's system-font alias into a Font node as firmware does.
 .replace('font = holder.font','font = CreateObject("roSGNode", "Font")')
 .replace(/sub drawSportsMap\(\)[\s\S]*?end sub/,'sub drawSportsMap()\nend sub')
 .replace(/sub requestSportsLayout\(\)[\s\S]*?end sub/,'sub requestSportsLayout()\n m.layoutRequests++\nend sub');
const regions=JSON.parse(fs.readFileSync(path.join(root,'static/sports-map/regions.json'),'utf8')).map(r=>({...r,games:[]}));
const games=Array.from({length:13},(_,i)=>({id:'game-'+i,league:i===12?'NHL':'AHL',name:'Game '+i,title:'Providence Bruins at Hershey Bears',statusLabel:'LIVE',score:'2â€“1',clock:'2nd 10:22',venue:'Giant Center',feeds:[{id:'feed-'+i,number:22000+i},{id:'backup-'+i,number:23000+i}]}));
regions.find(r=>r.id==='PA').games=games;
regions.find(r=>r.id==='AB').games=[{id:'canada',league:'NHL',name:'Canada game',feeds:[]}];
const fixture=`
sub main()
 m.top={data:ParseJson("${JSON.stringify({states:regions,totalGames:14,totalChannels:27,metadataLoading:false,metadataStale:false}).replaceAll('"','""')}"),tabsRequested:false,status:""}
 m.layoutRequests=0
 m.data=m.top.data : m.dataRevision=1 : m.layoutKey="fixture"
 m.tileLayoutKey=invalid : m.tileFills={} : m.drawnRegionId="" : m.bubbleKey="" : m.toolbarKey=""
 m.leagues=sportsMapLeagues(m.top.data) : m.menuIndex=0 : m.league="All sports"
 m.regions=sportsMapRegions(m.top.data,m.league) : m.regionId="PA"
 m.gameIndex=0 : m.feedIndex=0 : m.mode="map" : m.zone="content" : m.toolbarIndex=0 : m.weighted=false
 result={}
 result.toCanada=sportsMapNavigate(m.regions,"WA","up")
 result.active=sportsMapNextActive(m.regions,"PA",1)
 onKeyEvent("OK",true)
 onKeyEvent("right",true) : onKeyEvent("left",true) : onKeyEvent("left",true)
 result.wrapped=m.gameIndex
 onKeyEvent("right",true)
 result.first=m.gameIndex
 onKeyEvent("OK",true) : onKeyEvent("right",true)
 result.feed=m.feedIndex
 onKeyEvent("OK",true)
 result.selected=m.top.feedSelected
 onKeyEvent("back",true) : result.backFromFeeds=m.mode
 onKeyEvent("back",true) : result.backFromGames=m.mode : result.highlight=m.regionId
 onKeyEvent("back",true) : result.tabs=m.top.tabsRequested
 m.mode="games" : m.gameIndex=8
 onKeyEvent("up",true) : result.filters=m.zone
 onKeyEvent("right",true) : onKeyEvent("OK",true) : result.weighted=m.weighted
 onKeyEvent("left",true) : onKeyEvent("OK",true) : result.menu=m.zone
 onKeyEvent("down",true) : onKeyEvent("back",true)
 result.cancelLeague=m.league
 onKeyEvent("OK",true) : onKeyEvent("down",true) : onKeyEvent("OK",true)
 result.appliedLeague=m.league
 result.filteredGames=sportsMapRegion(m.regions,"PA").games.count()
 onKeyEvent("back",true) : result.returnToGames=m.zone
 m.league="All sports" : m.leagues=sportsMapLeagues(m.top.data)
 m.regions=sportsMapRegions(m.top.data,m.league)
 m.mode="feeds" : m.gameIndex=8 : m.feedIndex=1
 old=m.top.data.states[0].games
 for each region in m.top.data.states
  if region.id="PA" then region.games.unshift(region.games.pop())
 end for
 updateSportsData()
 result.retainedGame=selectedSportsGame().id : result.retainedFeed=selectedSportsGame().feeds[m.feedIndex].id
 for each region in m.top.data.states
  if region.id="PA" then region.games.delete(m.gameIndex)
 end for
 updateSportsData() : result.removedGameMode=m.mode
 result.layout=sportsMapLayout(m.regions,false)
 result.weightedLayout=sportsMapLayout(m.regions,true)
 result.positions=sportsMapBubblePositions({x:600,y:340},6)
 m.canvas=CreateObject("roSGNode","Group") : m.bubbles=CreateObject("roSGNode","Group")
 m.details=CreateObject("roSGNode","Group") : m.toolbar=CreateObject("roSGNode","Group")
 m.menu=CreateObject("roSGNode","Group") : m.summary=CreateObject("roSGNode","Label") : m.hint=CreateObject("roSGNode","Label")
 m.fonts={} : m.nodes=result.layout : m.mode="games" : m.zone="content" : m.gameIndex=0
 drawSportsMapActual()
 result.drawnTiles=m.canvas.getChildCount()
 result.drawnBubbles=m.bubbles.getChildCount()
 largeGames=[]
 for i=0 to 7413
  largeGames.push({id:"large-"+i.toStr(),league:"NHL",name:"Team A at Team B",title:"Team A at Team B",statusLabel:"LIVE",score:"2–1",clock:"2nd",venue:"Arena",feeds:[{id:"feed-"+i.toStr(),number:i}]})
 end for
 for each region in m.top.data.states
  region.games=[]
  if region.id="PA" then region.games=largeGames
 end for
 m.top.data.totalGames=7414 : m.top.data.totalChannels=7414
 m.league="All sports" : m.mode="map" : m.gameIndex=0 : m.feedIndex=0
 updateSportsData()
 result.largeGames=sportsMapRegion(m.regions,"PA").games.count()
 m.nodes=sportsMapLayout(m.regions,false) : m.layoutKey="large-fixture"
 drawSportsMapActual() : result.largeMapTiles=m.canvas.getChildCount()
 m.mode="games" : drawSportsMapActual()
 result.largeBubbles=m.bubbles.getChildCount()
 counts=[]
 for each region in m.regions
  counts.push({id:region.id,row:region.row,col:region.col,gameCount:region.games.count()})
 end for
 result.compactLayout=sportsMapLayout(counts,false)
 oldTile=m.tileFills["PA"].fill
 oldBubble=m.bubbles.getChild(0)
 saved=m.top.data : m.top.data=invalid
 m.gameIndex=1 : drawSportsMapActual()
 result.reusedBubble=oldBubble.isSameNode(m.bubbles.getChild(0))
 result.reusedTile=oldTile.isSameNode(m.tileFills["PA"].fill)
 result.focusedPoster=m.bubblePosters[1].poster.uri
 m.gameIndex=6 : drawSportsMapActual()
 result.newPage=not oldBubble.isSameNode(m.bubbles.getChild(0))
 m.mode="map" : m.regionId="AB" : drawSportsMapActual()
 result.reusedTileAfterMove=oldTile.isSameNode(m.tileFills["PA"].fill)
 result.mapSelected=m.drawnRegionId
 result.restoredTileColor=m.tileFills["PA"].fill.color
 result.hiddenBubbles=m.bubbles.getChildCount()
 m.top.data=saved
 compact={states:[{games:[{id:"keep",sport:"Hockey",phase:"live",locationSource:"venue",feeds:[{id:"feed-id",generated_id:"stable-id",play_url:"/guide/play/sports/22000",available:false,programmes:[{title:"Game title",start:100,stop:200,description:"Long guide description"}]}]}]}]}
 compactSportsMapData(compact)
 result.compactFeed=compact.states[0].games[0].feeds[0]
 result.compactGame=compact.states[0].games[0].id
 print FormatJson(result)
end sub
`;
let result;
before(async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'roku-map-'));
 const filename=path.join(dir,'map.brs');fs.writeFileSync(filename,logic+'\n'+ui+'\n'+actualDraw+'\n'+compactTask+'\n'+fixture);
 const cwd=process.cwd();process.chdir(dir);
 let stdout='',stderr='';
 try{await brs.execute([filename],{root:dir.replaceAll('\\','/'),componentDirs:[],stdout:{write(s){stdout+=s;}},stderr:{write(s){stderr+=s;}}});assert.equal(stderr,'');const parsed=JSON.parse(stdout.trim());result=new Proxy(parsed,{get:(object,key)=>object[String(key).toLowerCase()]??object[key]});}
 catch(error){throw Error(stderr||String(error));}
 finally{process.chdir(cwd);fs.rmSync(dir,{recursive:true,force:true});}
});
test('Remote cycles all thirteen bubbles across pages and wraps both ways',()=>{assert.equal(result.wrapped,12);assert.equal(result.first,0);});
test('Feeds select by identity and Back preserves the state highlight',()=>{
 assert.equal(result.feed,1);assert.equal(result.selected.id,'backup-0');
 assert.equal(result.backFromFeeds,'games');assert.equal(result.backFromGames,'map');assert.equal(result.highlight,'PA');assert.equal(result.tabs,true);
});
test('Filters and sizing are reachable with arrows; cancelling and committing leagues behave independently',()=>{
 assert.equal(result.filters,'toolbar');assert.equal(result.weighted,true);assert.equal(result.menu,'leagues');
 assert.equal(result.cancelLeague,'All sports');assert.equal(result.appliedLeague,'AHL');assert.equal(result.filteredGames,12);assert.equal(result.returnToGames,'content');
});
test('Refresh preserves game and feed identity; a removed game closes its feed chooser',()=>{
 assert.equal(result.retainedGame,'game-8');assert.equal(result.retainedFeed,'backup-8');assert.equal(result.removedGameMode,'games');
});
test('Canadian regions participate in navigation and active-region skipping',()=>{assert.equal(result.toCanada,'BC');assert.equal(result.active,'AB');});
test('Actual native layout stays within the canvas and weighted tiles do not overlap',()=>{
 for(const nodes of [result.layout,result.weightedLayout]){
  assert.equal(nodes.length,66);
  for(const n of nodes){assert(n.x-n.side/2>=-0.01);assert(n.y-n.side/2>=-0.01);assert(n.x+n.side/2<=1220.01);assert(n.y+n.side/2<=680.01);}
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
   const a=nodes[i],b=nodes[j],min=(a.side+b.side)/2;
   assert(Math.abs(a.x-b.x)>=min-.1||Math.abs(a.y-b.y)>=min-.1,`${a.id} overlaps ${b.id}`);
  }
 }
});
test('Bubble positions fit, do not overlap, and follow the same left-to-right order as the remote',()=>{
 let previous=-1;
 for(const p of result.positions){assert(p.x>=108&&p.x<=1112);assert(p.y>=108&&p.y<=572);assert(p.order>=previous);previous=p.order;}
 for(let i=0;i<result.positions.length;i++)for(let j=i+1;j<result.positions.length;j++)assert(Math.hypot(result.positions[i].x-result.positions[j].x,result.positions[i].y-result.positions[j].y)>=216);
});
test('The actual native render function creates the map, six circular game groups, and page caption',()=>{
 assert(result.drawnTiles>=132);assert.equal(result.drawnBubbles,7);
});

test('A 7,414-game map keeps all games while rendering only a bubble page',()=>{assert.equal(result.largeGames,7414);assert(result.largeMapTiles>=132);assert.equal(result.largeBubbles,7);assert.equal(result.compactLayout.length,66);});

test('Navigation uses the local snapshot and preserves tiles and same-page bubbles',()=>{assert.equal(result.reusedTile,true);assert.equal(result.reusedBubble,true);assert.equal(result.focusedPoster,'pkg:/images/sports-bubble-focused.png');assert.equal(result.newPage,true);assert.equal(result.reusedTileAfterMove,true);assert.equal(result.mapSelected,'AB');assert.equal(result.hiddenBubbles,0);});

test('Task compaction removes bulky descriptions while keeping playback identity, availability and timing',()=>{const f=result.compactFeed;assert.equal(result.compactGame,'keep');assert.equal(f.generated_id,'stable-id');assert.equal(f.play_url,'/guide/play/sports/22000');assert.equal(f.available,false);assert.equal(f.programmes[0].title,'Game title');assert.equal(f.programmes[0].start,100);assert.equal(f.programmes[0].stop,200);assert.equal(f.programmes[0].description,'');});
