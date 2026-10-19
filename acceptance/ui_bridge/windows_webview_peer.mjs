/** Owned installed WebView2 process and bounded CDP observation, with CSP intact. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
export async function openWindow(executable, workspace) {
  const profile = await mkdtemp(join(workspace, 'application-webview-'));
  const reserve = createServer();
  await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const child = spawn(executable, [], {cwd:dirname(executable),windowsHide:true,stdio:'ignore',env:{...process.env,
    WEBVIEW2_USER_DATA_FOLDER:profile,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port} --remote-debugging-address=127.0.0.1`}});
  let socket;
  async function close() {
    socket?.close();
    if (child.exitCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      child.kill(); await Promise.race([exited,delay(5000)]);
    }
  }
  try {
    let target;
    for (let attempt=0;attempt<300;attempt++) {
      try { target=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json())
        .find(page => page.type==='page' && /^https?:\/\/tauri\.localhost\//.test(page.url)); } catch {}
      if (target) break;
      assert.equal(child.exitCode,null,'owned installed process exited');
      await delay(100);
    }
    assert(target,'owned installed page unavailable');
    socket=new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
    let sequence=0;
    const pending=new Map();
    socket.onmessage=event=>{
      const value=JSON.parse(event.data), entry=pending.get(value.id);
      if (!entry) return;
      pending.delete(value.id);clearTimeout(entry.timer);
      value.error?entry.reject(new Error('CDP command refused')):entry.resolve(value.result);
    };
    function command(method,params={}) {
      return new Promise((resolve,reject)=>{
        const id=++sequence;
        const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP deadline'));},15000);
        pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
      });
    }
    async function evaluate(expression) {
      const result=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,allowUnsafeEvalBlockedByCSP:false});
      assert(!result.exceptionDetails,'page observation threw');
      return result.result.value;
    }
    await command('Runtime.enable');await command('Page.enable');
    return {evaluate,command,close,pid:child.pid};
  } catch (error) { await close();throw error; }
}
