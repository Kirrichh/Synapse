/** Installed application access, all source classes and cold protected recovery. */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { readFile, writeFile, access } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { openWindow, delay } from './windows_webview_peer.mjs';

const options=Object.fromEntries(process.argv.slice(2).reduce((rows,value,index,all)=>index%2===0?[...rows,[value.slice(2),all[index+1]]]:rows,[]));
assert(options.executable && options.workspace && options.run && options.output && options.python);
const checks=[];
let peer;
async function record(name, condition) {
  checks.push({name,status:condition?'PASS':'FAIL'});
  await writeFile(options.output,JSON.stringify({schema:1,checks},null,2)+'\n');
  assert(condition,name);console.log('PASS',name);
}
async function waitFile(name,seconds=900) {
  for (let count=0;count<seconds*10;count++) {
    try {await access(join(options.run,name));return;} catch {}
    await delay(100);
  }
  throw new Error('host coordination deadline');
}
async function start() {
  peer=await openWindow(resolve(options.executable),resolve(options.workspace));
  for (let index=0;index<300;index++) {
    if (await peer.evaluate(`[...document.querySelectorAll('button')].some(b=>b.textContent==='Refresh access' && !b.disabled)`)) return;
    await delay(100);
  }
  throw new Error('application access surface did not become ready');
}
async function channel() {
  return peer.evaluate(`(async()=>{
    window.__appObserved=[];
    const id=window.__TAURI_INTERNALS__.transformCallback(raw=>{
      if(typeof raw.message==='string') window.__appObserved.push(JSON.parse(raw.message));
    });
    window.__appSubscription=await window.__TAURI_INTERNALS__.invoke('desktop_application_subscribe',{channel:'__CHANNEL__:'+id});
    return window.__appSubscription;
  })()`);
}
async function operation(request, acknowledge=true) {
  return peer.evaluate(`(async()=>{
    const subscriptionId=window.__appSubscription;
    const requestId=await window.__TAURI_INTERNALS__.invoke('desktop_application',{subscriptionId,request:${JSON.stringify(JSON.stringify(request))}});
    for(let index=0;index<500;index++) {
      const message=window.__appObserved.find(item=>item.request_id===requestId && item.subscription_id===subscriptionId);
      if(message) {
        ${acknowledge?"await window.__TAURI_INTERNALS__.invoke('desktop_application_ack',{subscriptionId,requestId});":""}
        return message;
      }
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    throw new Error('application observation deadline');
  })()`);
}
async function result(request) {
  const response=await operation(request);
  if(response.type!=='result') {
    const code=/^[A-Z_]+$/.test(response.code??'')?response.code:'INVALID_REFUSAL';
    throw new Error('application refusal: '+code);
  }
  return response.result;
}
const delivery=(method,params={})=>result({action:'delivery',method:'delivery.'+method,params});
try {
  const preparation=spawnSync(options.python,['-B','-m','acceptance.ui_bridge.windows_application_registry','prepare','--run',options.run],
    {cwd:process.cwd(),encoding:'utf8',windowsHide:true,timeout:15000});
  if(preparation.status!==0) await writeFile(join(options.run,'preparation-diagnostic.txt'),preparation.stderr??'process failed');
  assert.equal(preparation.status,0,'ordinary owner preparation');
  await start();
  const key=await peer.evaluate(`window.__TAURI_INTERNALS__.invoke('desktop_identity')`);
  await record('installed native registration key has the declared public format',/^[0-9a-f]{64}$/.test(key));
  await channel();
  const denied=await operation({action:'grants'});
  await record('ordinary installed application cannot enroll its own access',denied.type==='error' && denied.code==='ACCESS_UNAVAILABLE');
  const ordinary=spawnSync(join(dirname(options.executable),'core','python.exe'),['-I','-B','-m','synapse','access','register','--public-key',key],{cwd:options.run,encoding:'utf8',windowsHide:true,timeout:15000});
  await record('installed canonical CLI requires separate administrative context',ordinary.status===1 && ordinary.stderr.trim()==='ADMIN_REQUIRED');
  await writeFile(join(options.run,'registration.json'),JSON.stringify({public_key:key})+'\n');
  console.log('WAIT canonical administrative enrollment');
  await waitFile('registered.json');
  const registered=JSON.parse(await readFile(join(options.run,'registered.json'),'utf8'));
  const registryRead=spawnSync(join(dirname(options.executable),'core','python.exe'),['-I','-B','-c',
    'from synapse.access.windows_registry import WindowsAccessRegistry\nfrom synapse.access.windows_invocations import WindowsInvocationResources\nwith WindowsAccessRegistry().transaction(administrative=False) as tx:\n assert len(tx.registry.grants)==1\n with WindowsInvocationResources().hold(next(iter(tx.registry.grants.values())).resource):\n  pass\n'],
    {cwd:options.run,encoding:'utf8',windowsHide:true,timeout:15000});
  if(registryRead.status!==0) await writeFile(join(options.run,'registry-reader-diagnostic.txt'),registryRead.stderr);
  await record('ordinary installed core reads the protected canonical registry',registryRead.status===0);
  const grants=await result({action:'grants'});
  await record('installed canonical session discovers only the explicit synthetic grant',grants.grants.length===1 && grants.grants[0].grant_id===registered.grant_id);
  const grant=registered.grant_id, object=registered.invocation_id;

  // Use the shipped React interaction before replacing its Channel for probes.
  await peer.command('Page.reload');await delay(700);
  await peer.evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='Refresh access').click();true`);
  for(let index=0;index<100;index++) {
    if(await peer.evaluate(`document.querySelector('.application-panel select')?.options.length===2`)) break;
    await delay(100);
  }
  await peer.evaluate(`(()=>{const select=document.querySelector('.application-panel select');select.value=${JSON.stringify(grant)};select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
  await delay(100);
  await peer.evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='Inspect').click();true`);
  let inspected=false;
  for(let index=0;index<150;index++) {
    inspected=await peer.evaluate(`document.body.innerText.includes('EXECUTION_STATE_UNKNOWN') && document.body.innerText.includes('The core responded at the last refresh')`);
    if(inspected)break;await delay(100);
  }
  if(!inspected) {
    const ui=await peer.evaluate(`(()=>{const panel=document.querySelector('.application-panel');return {
      error:panel.querySelector('[role="alert"]')?.textContent.match(/attention: ([A-Z_]+)/)?.[1]??null,
      hasUnknown:panel.textContent.includes('EXECUTION_STATE_UNKNOWN'),
      hasConnection:panel.textContent.includes('The core responded at the last refresh'),
      selected:panel.querySelector('select')?.selectedIndex??null,
      busy:[...panel.querySelectorAll('button')].find(b=>b.textContent==='Refresh access')?.disabled
    }})()`);
    await writeFile(join(options.run,'ui-observation.json'),JSON.stringify(ui));
    console.log('UI observation',JSON.stringify(ui));
  }
  await record('shipped UI reads StateSync, retained evidence and connection observation',inspected);
  await channel();
  const foreign=await operation({action:'read',grant_id:grant,method:'invocation.inspect',params:{invocation_id:'foreign-invocation'}});
  await record('foreign object identifier cannot cross the installed workspace scope',foreign.type==='error' && foreign.code==='ACCESS_DENIED');
  const observed=await result({action:'read',grant_id:grant,method:'invocation.inspect',params:{invocation_id:object}});
  const issued=await result({action:'issue',grant_id:grant});
  const draft={method:'invocation.cancel',method_version:'1',recipient:object,expected_revision:observed.revision,params:{invocation_id:object}};
  const receipt=await operation({action:'submit',grant_id:grant,intent_id:issued.intent_id,draft:JSON.stringify(draft)},false);
  await record('owner accepts cancellation before an unacknowledged renderer result',receipt.type==='result' && receipt.result.outcome==='CANCEL_REQUESTED' && !receipt.result.replayed);
  const busy=await peer.evaluate(`window.__TAURI_INTERNALS__.invoke('desktop_application',{subscriptionId:window.__appSubscription,request:'{"action":"grants"}'}).then(()=>false,code=>code==='BUSY')`);
  await record('lost application ACK bounds request admission',busy);
  await peer.close();peer=null;await delay(300);
  await start();await channel();
  const reopened=await result({action:'pending',grant_id:grant});
  const saved=reopened.intents.find(item=>item.intent_id===issued.intent_id);
  await record('cold installed restart recovers the original native command identity',saved?.command_id===receipt.result.command_id && saved.prepared===true);
  const recovered=await result({action:'recover',grant_id:grant,intent_id:issued.intent_id,resend_if_absent:true});
  await record('recovery without a renderer draft reads the same durable receipt',recovered.replayed===true && recovered.command_id===receipt.result.command_id && recovered.outcome==='CANCEL_REQUESTED');
  const confirm=await result({action:'read',grant_id:grant,method:'delivery.subscribe',params:{source:'invocation.record',invocation_ids:[object]}});
  await delivery('collect',{subscription_id:confirm.identity.subscription_id});
  const reader=spawn(options.python,['-B','-m','acceptance.ui_bridge.windows_application_registry','reader','--run',options.run],{cwd:process.cwd(),windowsHide:true,stdio:'ignore'});
  const readerExit=new Promise(resolve=>reader.once('exit',resolve));
  await waitFile('revoked',45);
  await record('real separate-process read custody serializes canonical revocation',await readerExit===0);
  const after=await operation({action:'delivery',method:'delivery.next',params:{}});
  await record('revocation prevents disclosure of queued protected evidence',after.type==='error' && after.code==='ACCESS_DENIED');
  const deniedRecovery=await operation({action:'recover',grant_id:grant,intent_id:issued.intent_id,resend_if_absent:true});
  await record('revoked access cannot read retained receipts or resend commands',deniedRecovery.type==='error' && deniedRecovery.code==='ACCESS_DENIED');
  await peer.close();peer=null;
  await writeFile(join(options.run,'done'),'');await waitFile('cleaned',30);
  await record('temporary installation registry is removed after actual window checks',true);
} catch(error) {
  await writeFile(join(options.run,'abort'),'');
  await writeFile(options.output,JSON.stringify({schema:1,checks,failure:error instanceof assert.AssertionError?'ASSERTION':'OBSERVATION_ERROR',
    ...(/^application refusal: [A-Z_]+$/.test(error.message)?{code:error.message.slice(21)}:{})},null,2)+'\n');
  console.error(error.message);process.exitCode=1;
} finally {await peer?.close();}
