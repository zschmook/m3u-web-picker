const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const source=fs.readFileSync('static/js/guide_programmes.js','utf8');
test('failed-probe row stays red with enabled play and listen actions',()=>{
 const context=vm.createContext({guideState:{currentChannel:{play_url:'/guide/play/manual/fs1'},mode:'local'},GUIDE_PX_PER_MINUTE:5,escapeHtml:String,renderStationLogo:()=>'',renderProgrammeBlocks:()=>'',guidePlayLabel:()=> 'Play'});
 const start=source.indexOf('  function renderChannelRow('),end=source.indexOf('  filteredGuideChannels =',start);
 vm.runInContext(source.slice(start,end),context);
 const html=context.renderChannelRow({available:false,play_url:'/guide/play/manual/fs1',number:60,name:'FS1'},{now:new Date(),start:new Date(),width:200,slotWidth:50},'');
 assert.match(html,/guide-unavailable-row/);assert.match(html,/guide-current-row/);
 assert.doesNotMatch(html,/aria-disabled|\sdisabled(?:\s|=|>)/);
 assert.match(html,/Last probe failed; select to retry playback/);
 assert.match(html,/guide-station-play/);assert.match(html,/guide-station-listen/);
});
