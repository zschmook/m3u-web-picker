const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('credits loads once on demand, renders metadata as text, and rejects executable links',async()=>{
  function node(tag){return {tag,children:[],textContent:'',append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items}}}
  const root=node('div'),status=node('p'),handlers={};let active=false,requests=0;
  const data={entries:[{group:'Python libraries',name:'<script>bad</script>',version:'1',license:'MIT',website:'javascript:alert(1)',source:'https://example.org/source',notices:['/static/licenses/generated/license.txt']}],common_licenses:[]};
  const document={createElement:node,getElementById:id=>id==='uiCreditsInventory'?root:status,querySelector:()=>active?{}:null};
  vm.runInNewContext(fs.readFileSync('static/js/ui_credits.js','utf8'),{document,window:{addEventListener:(type,fn)=>handlers[type]=fn},fetch:async url=>{requests++;assert.equal(url,'/static/licenses/generated/index.json');return {ok:true,json:async()=>data}}});
  assert.equal(requests,0);active=true;await handlers['ui:settings-panel']();assert.equal(requests,1);await handlers['ui:settings-panel']();assert.equal(requests,1);
  const all=[];function walk(n){all.push(n);n.children.forEach(walk)}walk(root);
  assert.ok(all.some(n=>n.tag==='strong'&&n.textContent.includes('<script>bad</script>')));
  assert.ok(!all.some(n=>n.href?.startsWith('javascript:')));
  assert.ok(all.some(n=>n.href==='/static/licenses/generated/license.txt'));
  assert.ok(all.some(n=>n.href==='https://example.org/source'&&n.rel==='noopener noreferrer'));
  assert.match(status.textContent,/1 installed components/);
});
