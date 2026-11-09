/* Browser/API integration with injected fake source agents; no Windows access. */
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'opc-discovery-browser-'));
const python=String.raw`
import sys
from opc_da_bridge.management.server import make_server
from opc_da_bridge.management.deployment import AgentError
class Fake:
 def request(self,target,request):
  host=target['host']
  if host=='error.invalid': raise AgentError('Busca simulada falhou')
  return {'ok':True,'identity':{'hostname':'SOURCE-LAB','addresses':[host],'registry_view':'32-bit'},'servers':[{'prog_id':'Lab.Source.%d'%n,'clsid':'{11111111-2222-3333-4444-555555555555}','registry_view':'32-bit'} for n in range(0 if host=='empty.invalid' else 2)]}
s=make_server(sys.argv[1],port=0)
s.sources.channel=Fake()
def provision(host,transport=None,prog_id=None):
 if host not in ('empty.invalid','source.invalid','error.invalid'): raise AgentError('Não provisionada')
 if transport not in (None,'opc_da_automation'): raise AgentError('Transporte não provisionado')
 return {'host':host,'expected_hostname':'SOURCE-LAB','enabled':True,'read_transport':'opc_da_automation'}
s.sources.provision=provision
print(s.server_port,flush=True)
try:s.serve_forever()
finally:s.server_close()
`;
let backend,browser;
(async()=>{
 backend=spawn('python3',['-c',python,dir],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},stdio:['ignore','pipe','pipe']});
 backend.stderr.on('data',b=>fs.appendFileSync(path.join(dir,'backend.log'),b));
 const port=await new Promise((resolve,reject)=>{backend.stdout.once('data',b=>resolve(Number(String(b).trim())));backend.once('exit',code=>reject(Error('backend exited '+code)));});
 const base=`http://127.0.0.1:${port}`;
 const api=async route=>(await fetch(base+'/api/v1/'+route)).json();
 browser=await chromium.launch({headless:true,executablePath:process.env.OPC_BROWSER_EXECUTABLE});
 const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);const test=id=>page.getByTestId(id);
 const field=async(name,value)=>{const e=test('field-'+name);if(await e.evaluate(x=>x.tagName)==='SELECT')await e.selectOption(value);else await e.fill(value);};
 const save=async()=>{await test('dialog-save').click();await page.getByRole('dialog').waitFor({state:'hidden'});};
 await test('new-connection').click();await field('name','Discovery');await save();
 await test('new-machine').click();await field('name','Destination');await field('host','dest.invalid');await save();
 await test('new-module').click();await field('name','Real source');await field('source_kind','opc_da');
 await field('source_host','empty.invalid');await test('discover-opc-da').click();await test('source-discovery-status').filter({hasText:'Nenhum servidor'}).waitFor();
 assert.equal(await test('field-source_progid').locator('option').count(),1);
 await field('source_host','error.invalid');await test('discover-opc-da').click();await test('source-discovery-status').filter({hasText:'falhou'}).waitFor();
 assert.equal(await test('field-name').inputValue(),'Real source');
 await field('source_host','source.invalid');await test('discover-opc-da').click();await test('source-discovery-status').filter({hasText:'SOURCE-LAB'}).waitFor();
 assert.equal(await test('field-source_progid').inputValue(),'');
 await test('dialog-save').click();await page.getByRole('dialog').waitFor({state:'visible'});
 await field('source_progid','Lab.Source.1');await save();
 let state=await api('state'),module=state.connections[0].machines[0].modules[0];
 assert.equal(module.source_host,'source.invalid');assert.equal(module.source_progid,'Lab.Source.1');assert.equal(module.source_kind,'opc_da');
 await test('new-folder').click();await field('name','Folder');await save();
 await test('new-variable').click();await field('name','Level');await field('item_id','Destination.Level');await field('source_id','Source.Exact:Level');
 assert.equal(await test('field-initial_value').isVisible(),false);await save();
 state=await api('state');const variable=state.connections[0].machines[0].modules[0].folders[0].variables[0];
 assert.equal(variable.source_id,'Source.Exact:Level');assert.equal(variable.item_id,'Destination.Level');
 assert.equal(variable.source_data_type,'Auto');assert.equal(variable.source_conversion,'automatic');
 for(const [type,name,initial] of [['Integer','Counter',0],['String','Text',''],['Boolean','Digital',false]]){
  await test('new-variable').click();await field('name',name);await field('data_type',type);
  await field('item_id','Destination.'+name);await field('source_id','Source.Exact:'+name);
  assert.equal(await test('field-source_conversion').inputValue(),'automatic');
  assert.equal(await test('field-initial_value').isVisible(),false);await save();
  const saved=(await api('state')).connections[0].machines[0].modules[0].folders[0].variables.find(v=>v.name===name);
  assert.equal(saved.data_type,type);assert.equal(saved.initial_value,initial);
  assert.equal(saved.source_data_type,'Auto');assert.equal(saved.source_conversion,'automatic');
 }
 state=await api('state');
 await test('variable-row-'+variable.id).dblclick();await page.locator('#notice').filter({hasText:/exige aplicação|pendente|falhou/}).waitFor();
 assert.equal((await api('state')).revision,state.revision);
 assert.deepEqual(errors,[]);
 console.log('OPC_DA_DISCOVERY_BROWSER_PASS empty multiple explicit_selection error_preserves_form distinct_ids no_simulation_fallback four_types auto_conversion real_scope_save');
 console.log('ARTIFACT_DIRECTORY='+dir);
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(backend)backend.kill('SIGINT');});
