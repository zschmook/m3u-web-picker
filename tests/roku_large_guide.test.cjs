const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const {test,before}=require('node:test');
const brs=require('brs');
const root=path.join(__dirname,'..','roku-receiver','components');
const read=n=>fs.readFileSync(path.join(root,n+'.brs'),'utf8');
const grid=read('GuideGrid').replace('font = holder.font','font = CreateObject("roSGNode", "Font")');
const fixture=`
sub main()
 channels=[]
 now=CreateObject("roDateTime") : t=now.asSeconds()
 for i=0 to 7413
  programmes=[{title:"Team A at Team B",description:"Game description",start:t-900,stop:t+3600,subtitle:"Live"},{title:"Next game",description:"",start:t+3600,stop:t+7200,subtitle:""}]
  channels.push({number:i.toStr(),name:"NHL • Team A at Team B — Feed "+i.toStr(),group:"Sports",logo:"",play_url:"/guide/play/sports/"+i.toStr(),is_movie:false,is_sports:true,programmes:programmes})
 end for
 groups=guideChannelIndices(channels)
 m.top={channels:channels,serverBase:"http://picker:9999",contentStartTime:t-1800,active:true,numRows:9,duration:10800,jumpToChannel:0}
 m.canvas=CreateObject("roSGNode","Group")
 m.width=1760 : m.stationWidth=360 : m.headerHeight=46 : m.rowHeight=88
 m.firstRow=0 : m.channel=0 : m.programme=0 : m.channelFocus=false : m.windowStart=0 : m.focusTime=0
 m.titleFont=makeFont(23) : m.timeFont=makeFont(19) : m.smallFont=makeFont(17)
 onChannelsChanged()
 result={total:m.rawChannels.count(),leagueCount:groups.NHL.count(),initialCache:m.rowOrder.count(),drawn:m.canvas.getChildCount()}
 for i=0 to 99
  row=guideRow(i*70)
 end for
 result.cached=m.rowOrder.count() : result.cacheEntries=m.rowCache.count()
 m.top.jumpToChannel=7413 : onJumpChannel()
 result.lastNumber=rowContent().number : result.lastFocused=m.top.channelFocused
 result.currentProgramme=m.programme
 onKeyEvent("OK",true) : result.programSelected=m.top.programSelected
 m.channelFocus=true : onKeyEvent("OK",true) : result.channelSelected=m.top.channelInfoSelected
 channels[7413].name="Updated feed" : m.top.channels=channels
 onChannelsChanged() : result.refreshedName=rowContent().name
 m.top.channels=[] : onChannelsChanged()
 result.emptyRows=m.rawChannels.count() : result.emptyCache=m.rowCache.count() : result.emptyCanvas=m.canvas.getChildCount()
 print FormatJson(result)
end sub
`;
let result;
before(async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'roku-large-guide-'));
 const filename=path.join(dir,'guide.brs');
 fs.writeFileSync(filename,[read('GuideCategories'),read('GuideDetails'),read('GuideRows'),grid,fixture].join('\n'));
 const cwd=process.cwd();process.chdir(dir);
 let stdout='',stderr='';
 try{await brs.execute([filename],{root:dir.replaceAll('\\','/'),componentDirs:[],stdout:{write(s){stdout+=s;}},stderr:{write(s){stderr+=s;}}});assert.equal(stderr,'');result=Object.fromEntries(Object.entries(JSON.parse(stdout.trim())).map(([k,v])=>[k.toLowerCase(),v]));}
 catch(error){throw Error(stderr||String(error));}
 finally{process.chdir(cwd);fs.rmSync(dir,{recursive:true,force:true});}
});
test('7,414-channel lineup builds only visible guide rows',()=>{assert.equal(result.total,7414);assert.equal(result.leaguecount,7414);assert(result.initialcache<=7);assert(result.drawn>0);});
test('Scrolling keeps the row cache bounded and reaches the last channel',()=>{assert.equal(result.cached,32);assert.equal(result.cacheentries,32);assert.equal(result.lastnumber,'7413');assert.equal(result.lastfocused,7413);});
test('Programme and channel selection still refer to the full lineup',()=>{assert.equal(result.currentprogramme,0);assert.equal(result.programselected,0);assert.equal(result.channelselected,7413);});
test('Refreshing invalidates cached rows and an empty scan clears the screen',()=>{assert.equal(result.refreshedname,'Updated feed');assert.equal(result.emptyrows,0);assert.equal(result.emptycache,0);assert.equal(result.emptycanvas,0);});
