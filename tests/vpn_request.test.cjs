const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('static/js/ui_vpn_settings.js','utf8');
const request=source.slice(source.indexOf('  async function request('),source.indexOf('  window.mountVpnConfiguration='));
let response;
const context={session:'a'.repeat(32),fetch:async()=>response};
vm.createContext(context);vm.runInContext(request+'\nthis.request=request;',context);
(async()=>{
 response={status:500,ok:false,text:async()=>'<!doctype html><h1>Server error</h1>'};
 await assert.rejects(context.request(),error=>error.status===500&&error.message.includes('HTTP 500')&&!error.message.includes('doctype')&&!error.message.includes('Unexpected token'));
 response={status:409,ok:false,text:async()=>JSON.stringify({error:'VPN connection manager is unavailable.'})};
 await assert.rejects(context.request(),error=>error.status===409&&error.message==='VPN connection manager is unavailable.');
 response={status:200,ok:true,text:async()=>'null'};
 await assert.rejects(context.request(),/unexpected response/);
 response={status:200,ok:true,text:async()=>JSON.stringify({configuration_present:false})};
 assert.equal((await context.request()).configuration_present,false);
 console.log('PASS: VPN HTML failures, JSON errors and valid responses');
})().catch(error=>{console.error(error);process.exitCode=1;});
