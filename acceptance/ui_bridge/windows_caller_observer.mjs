/** Real native caller checks in explicitly weakened external topology copies.
 * Capability grants/navigation changes permit reaching the unchanged command
 * filter. They are never installed or included in the distributed application.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { createServer as tcpServer } from 'node:net';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';

const options = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) =>
  index % 2 === 0 ? [...rows, [value.slice(2), all[index + 1]]] : rows, []));
assert(options.executable && options.workspace && options.output && ['label', 'origin'].includes(options.context));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const server = createServer((_request, response) => {
  response.setHeader('content-type', 'text/html');
  response.end('<!doctype html><title>Synthetic foreign caller</title><p>Acceptance-owned context</p>');
});
// A default-port origin isolates the host guard from the independent port guard.
// Refuse an occupied port; never terminate another service to obtain it.
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(options.context === 'origin' ? 80 : 0, '127.0.0.1', resolve);
});
const foreign = new URL(`http://127.0.0.1:${server.address().port}/`).href;
const reserve = tcpServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const profile = await mkdtemp(join(resolve(options.workspace), 'caller-profile-'));
const executable = resolve(options.executable);
const child = spawn(executable, [], {cwd: dirname(executable), windowsHide: true, stdio: 'ignore', env: {
  ...process.env, WEBVIEW2_USER_DATA_FOLDER: profile, SYNAPSE_ACCEPTANCE_FOREIGN_URL: foreign,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1`,
}});
const record = {schema: 1, context: options.context, status: 'INFRASTRUCTURE_FAILURE', commands: []};
let socket;
try {
  let target;
  for (let attempt = 0; attempt < 300; attempt++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = pages.find(page => page.type === 'page' && (options.context === 'origin' ? page.url === foreign : /^http:\/\/tauri\.localhost\//.test(page.url)));
      if (target) break;
    } catch {}
    assert(child.exitCode === null);
    await delay(100);
  }
  assert(target, 'owned foreign-context WebView was not exposed');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;
  const pending=new Map();
  socket.onmessage=event=>{
    const message=JSON.parse(event.data);
    if (pending.has(message.id)) {
      const {resolve,reject,timer}=pending.get(message.id);clearTimeout(timer);pending.delete(message.id);
      message.error ? reject(Error('CDP command error')) : resolve(message.result);
    }
  };
  function evaluate(expression) {
    return new Promise((resolve,reject)=>{
      const id=++sequence;
      const timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP deadline'));},10000);
      pending.set(id,{resolve:result=>result.exceptionDetails?reject(Error('probe exception')):resolve(result.result.value),reject,timer});
      socket.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true,allowUnsafeEvalBlockedByCSP:false}}));
    });
  }
  assert(await evaluate('typeof window.__TAURI_INTERNALS__?.invoke === "function"'), 'native IPC is not available in the intended context');
  record.actualContext = options.context === 'origin'
    ? await evaluate(`location.origin === ${JSON.stringify(new URL(foreign).origin)}`)
    : await evaluate('window.__TAURI_INTERNALS__.metadata.currentWindow.label === "foreign"');
  assert(record.actualContext, 'the foreign native context was not established');
  for (const [command,args] of [
    ['desktop_subscribe',{channel:'__CHANNEL__:987654'}], ['desktop_start',{}], ['desktop_stop',{}],
    ['desktop_ack',{subscriptionId:'synthetic-foreign',sequence:0}], ['desktop_force_close',{}],
    ['desktop_identity',{}], ['desktop_application_subscribe',{channel:'__CHANNEL__:987654'}],
    ['desktop_application',{subscriptionId:'synthetic-foreign',request:'{"action":"grants"}'}],
    ['desktop_application_ack',{subscriptionId:'synthetic-foreign',requestId:'synthetic-request'}],
    ['desktop_application_unsubscribe',{subscriptionId:'synthetic-foreign'}],
  ]) {
    const result=await evaluate(`window.__TAURI_INTERNALS__.invoke(${JSON.stringify(command)},${JSON.stringify(args)}).then(()=>"ALLOWED",error=>error==="CALLER_REFUSED"?"CALLER_REFUSED":"OTHER_REFUSAL")`);
    record.commands.push({command,result});
  }
  record.status=record.commands.every(row=>row.result==='CALLER_REFUSED') ? 'PASS'
    : record.commands.some(row=>row.result==='ALLOWED') ? 'BYPASS_DETECTED' : 'INFRASTRUCTURE_FAILURE';
} catch {
  record.status='INFRASTRUCTURE_FAILURE';
} finally {
  socket?.close();
  if (child.exitCode===null) {
    const exited=new Promise(resolve=>child.once('exit',resolve));child.kill();await Promise.race([exited,delay(5000)]);
  }
  server.close();
  await writeFile(options.output,JSON.stringify(record,null,2)+'\n');
}
console.log(options.context,record.status);
process.exitCode=record.status==='INFRASTRUCTURE_FAILURE'?2:0;
