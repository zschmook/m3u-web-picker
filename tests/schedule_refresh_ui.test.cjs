const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
test('API refresh applies cross-referenced channels and refreshed dataset list',async()=>{
  const source=fs.readFileSync('static/js/app.js','utf8');
  const fn=source.slice(source.indexOf('async function forceSportsScheduleApiRefresh()'),source.indexOf('\nfunction applySportsState()',source.indexOf('async function forceSportsScheduleApiRefresh()')));
  const button={disabled:false,textContent:''},calls=[],state={scan:{},schedule_api:{apis:[]}};
  const response={sports:{scan:{running:false},schedule_api:{apis:[{id:'nhl'},{id:'ncaa'}]}},channels:[{id:'matched-game'}],selected_ids:[],scan_result:{message:'Matched NHL stream'},result:{warning:''}};
  const ctx={sportsState:state,Date,sportsElement:()=>button,applySportsState:()=>calls.push('state'),scheduleSportsStatusPoll:()=>{},renderSportsScheduleApi:()=>{},pollSportsStatus:async()=>{},setSportsError:()=>{},setStatus:text=>calls.push(text),applyChannelPayload:data=>calls.push(data.channels),fetch:async(url,options)=>{assert.equal(url,'/api/sports/schedule-api/refresh');assert.equal(options.method,'POST');return {ok:true,json:async()=>response}}};
  vm.createContext(ctx);vm.runInContext(fn,ctx);await ctx.forceSportsScheduleApiRefresh();
  assert.deepEqual(ctx.sportsState.schedule_api.apis,response.sports.schedule_api.apis);
  assert.ok(calls.includes(response.channels));assert.ok(calls.includes('Matched NHL stream'));
});
