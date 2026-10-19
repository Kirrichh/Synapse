/** Real installed WebView2 checks through its documented per-process debugger.
 * No CSP bypass, alternate renderer, product hook, or persistent debug setting.
 * Debugger evaluation only injects synthetic probes; DOM-created content must
 * still pass the actual CSP. Output contains verdicts, never raw page/log data.
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createServer as tcpServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { mkdtemp, writeFile } from 'node:fs/promises';

const options = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) =>
  index % 2 === 0 ? [...rows, [value.slice(2), all[index + 1]]] : rows, []));
assert(options.executable && options.workspace && options.output);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const profile = await mkdtemp(join(resolve(options.workspace), 'webview-security-'));
const reserve = tcpServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const debugPort = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
let hits = 0;
let phase = 'startup';
const requests = [];
const trap = createServer((request, response) => {
  hits++;
  const known = (value, allowed) => allowed.includes(value) ? value : 'other';
  if (requests.length < 16) requests.push({ phase,
    method: known(request.method, ['GET', 'POST', 'OPTIONS', 'HEAD']),
    probe: known(request.url?.split('/').at(-1), ['capability', 'fetch', 'navigation', 'new-window', 'synthetic-canary']),
    destination: known(request.headers['sec-fetch-dest'], ['document', 'image', 'iframe', 'empty']),
    mode: known(request.headers['sec-fetch-mode'], ['navigate', 'cors', 'no-cors', 'same-origin']) });
  response.end('synthetic canary');
});
await new Promise(resolve => trap.listen(0, '127.0.0.1', resolve));
const canary = `http://127.0.0.1:${trap.address().port}/synthetic-canary`;
const executable = resolve(options.executable);
const child = spawn(executable, [], { cwd: dirname(executable), windowsHide: true, stdio: 'ignore', env: {
  ...process.env, WEBVIEW2_USER_DATA_FOLDER: profile,
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort} --remote-debugging-address=127.0.0.1`,
} });
const checks = [];
let socket;
let stage = 'debugger_connection';
async function record(name, condition, observed) {
  checks.push({ name, status: condition ? 'PASS' : 'FAIL', ...(observed ? { observed } : {}) });
  await writeFile(options.output, JSON.stringify({ schema: 1, instrumentation: 'per-process WebView2 CDP; CSP enforced', checks }, null, 2) + '\n');
  assert(condition, name);
  console.log('PASS', name);
}
try {
  let target;
  let discovery = 'no response';
  for (let attempt = 0; attempt < 300; attempt++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      discovery = pages.map(page => `${page.type}:${new URL(page.url).protocol}`).join(',');
      target = pages.find(page => page.type === 'page' && /^https?:\/\/tauri\.localhost\//.test(page.url));
      if (target) break;
    } catch (error) { discovery = error.cause?.code ?? error.name; }
    assert(child.exitCode === null, 'installed application exited before debugger connection');
    await delay(100);
  }
  assert(target, `owned installed Tauri target was not exposed (${discovery})`);
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  let dialog;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Page.javascriptDialogOpening') dialog = message.params;
    if (message.id && pending.has(message.id)) {
      const { resolve, reject, timer } = pending.get(message.id);
      clearTimeout(timer); pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    }
  };
  function command(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} deadline`)); }, 10000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, allowUnsafeEvalBlockedByCSP: false });
    assert(!result.exceptionDetails, 'synthetic page probe threw unexpectedly');
    return result.result.value;
  }
  await command('Runtime.enable');
  await command('Page.enable');
  stage = 'bootstrap';
  await evaluate(`window.__security = {deliveries: [], violations: []};
    document.addEventListener('securitypolicyviolation', event => window.__security.violations.push(event.effectiveDirective));
    window.__probeInvoke = (name, args={}) => window.__TAURI_INTERNALS__.invoke(name, args);
    window.__probeSubscribe = async () => {
      const id = window.__TAURI_INTERNALS__.transformCallback(raw => {
        if (typeof raw.message === 'string') window.__security.deliveries.push(JSON.parse(raw.message));
      });
      return JSON.parse(await window.__probeInvoke('desktop_subscribe', {channel: '__CHANNEL__:' + id}));
    }; true`);
  let ready;
  for (let index = 0; index < 300; index++) {
    ready = await evaluate('window.__probeSubscribe()');
    if (ready.state === 'ready' || ready.state === 'fail') break;
    await delay(100);
  }
  console.log('Native bootstrap:', ready.state, /^[A-Z_]+$/.test(ready.code ?? '') ? ready.code : '');
  await record('actual installed native session ready', ready.state === 'ready' && ready.domain_ready === false);
  stage = 'window_checks';
  const packagedOrigin = await evaluate('location.origin');
  assert(/^https?:\/\/tauri\.localhost$/.test(packagedOrigin), 'unexpected packaged origin');
  if (options.scenario === 'crash') {
    assert(options.python, 'crash observation requires development Python with psutil');
    await command('Page.reload');
    await delay(700);
    const killed = spawnSync(options.python, ['-B', '-c',
      'import pathlib,psutil,sys; p=psutil.Process(int(sys.argv[1])); assert p.ppid()==int(sys.argv[2]); assert pathlib.Path(p.exe()).resolve()==pathlib.Path(sys.argv[3]).resolve(); assert p.cmdline()[1:]==["-I","-B","-m","synapse","machine"]; p.kill(); p.wait(timeout=10)',
      String(ready.process_id), String(child.pid), join(dirname(executable), 'core', 'python.exe')],
      { windowsHide: true, encoding: 'utf8', timeout: 15000 });
    await record('crash target is the owned installed canonical core', killed.status === 0);
    let visibleFailure = false;
    for (let index = 0; index < 100; index++) {
      visibleFailure = await evaluate(`document.body.innerText.includes('CORE_EXITED') && [...document.querySelectorAll('button')].some(b=>b.textContent.includes('Force close desktop'))`);
      if (visibleFailure) break;
      await delay(100);
    }
    await record('actual renderer displays core loss and explicit recovery', visibleFailure);
    const clickForce = () => evaluate(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Force close desktop')); if(!b || b.disabled) throw Error('force action unavailable'); b.click(); return true})()`);
    for (const accept of [false, true]) {
      dialog = undefined;
      const clicked = clickForce().catch(() => false);
      for (let index = 0; !dialog && index < 50; index++) await delay(100);
      await record(accept ? 'second force close asks for confirmation' : 'force close asks for confirmation',
        dialog?.type === 'confirm' && dialog.message.includes('does not undo external effects'));
      await command('Page.handleJavaScriptDialog', { accept });
      if (!accept) {
        await clicked;
        await delay(300);
        await record('cancel keeps failed shell open', child.exitCode === null && await evaluate(`document.body.innerText.includes('CORE_EXITED')`));
      } else {
        for (let index = 0; child.exitCode === null && index < 100; index++) await delay(100);
        await record('confirmed force close exits actual installed shell', child.exitCode !== null);
      }
    }
  } else {
  if (options.scenario !== 'channels') {
  phase = 'capabilities';
  await record('force close refused while core is ready', await evaluate(`window.__probeInvoke('desktop_force_close').then(()=>false, code=>code==='CALLER_REFUSED')`));
  await record('foreign subscription ACK refused', await evaluate(`window.__probeInvoke('desktop_ack',{subscriptionId:'synthetic-foreign',sequence:0}).then(()=>false,code=>code==='CALLER_REFUSED')`));
  for (const [name, args] of [
    ['plugin:shell|execute', {program: 'synthetic-no-executable', args: []}],
    ['plugin:fs|read_file', {path: 'synthetic-no-file'}],
    ['plugin:webview|create_webview_window', {label: 'synthetic-foreign', url: canary + '/capability'}],
  ]) {
    await record(`ungranted native command refused: ${name}`, await evaluate(`window.__probeInvoke(${JSON.stringify(name)},${JSON.stringify(args)}).then(()=>false,()=>true)`));
  }
  await record('CSP blocks JavaScript eval', await evaluate(`(()=>{try{eval('window.__syntheticEval=1');return false}catch{return window.__syntheticEval===undefined}})()`));
  await evaluate(`(()=>{const script=document.createElement('script');script.textContent='window.__syntheticInline=1';document.body.append(script);return true})()`);
  await delay(100);
  await record('CSP blocks DOM inline script', await evaluate('window.__syntheticInline === undefined'));
  phase = 'csp';
  await record('CSP blocks non-IPC fetch', await evaluate(`fetch(${JSON.stringify(canary + '/fetch')}).then(()=>false,()=>true)`));
  await evaluate(`(()=>{const frame=document.createElement('iframe');frame.src=${JSON.stringify(canary)};document.body.append(frame);const image=document.createElement('img');image.src=${JSON.stringify(canary)};document.body.append(image);return true})()`);
  await delay(200);
  const violations = await evaluate('window.__security.violations');
  await record('CSP blocks frame and image loads', violations.includes('frame-src') && violations.includes('img-src') && hits === 0);
  phase = 'new_window';
  await record('new window denied', await evaluate(`window.open(${JSON.stringify(canary + '/new-window')},'_blank') === null`));
  await delay(500);
  phase = 'navigation';
  await evaluate(`location.href = ${JSON.stringify(canary + '/navigation')}; true`);
  await delay(200);
  const navigation = { sameOrigin: await evaluate('location.origin') === packagedOrigin, networkRequests: hits, requests };
  console.log('Navigation observation:', JSON.stringify(navigation));
  await record('foreign top-level navigation denied', navigation.sameOrigin && hits === 0, navigation);
  }

  // A real Channel is left without ACKs; the controller must keep progressing.
  const first = await evaluate('window.__security.deliveries=[]; window.__probeSubscribe()');
  await delay(2500);
  const count = await evaluate('window.__security.deliveries.length');
  await record('unacknowledged Channel has one outstanding snapshot', count === 1);
  const second = await evaluate('window.__probeSubscribe()');
  await record('native heartbeat progresses despite missing Channel ACK', second.state === 'ready' && second.sequence > first.sequence && second.process_id === first.process_id);
  await record('old Channel cannot acknowledge replacement subscription', await evaluate(`window.__probeInvoke('desktop_ack',{subscriptionId:${JSON.stringify(first.subscription_id)},sequence:${first.sequence}}).then(()=>false,code=>code==='CALLER_REFUSED')`));
  await evaluate(`window.__probeInvoke('desktop_stop')`);
  let stopped;
  for (let index = 0; index < 60; index++) {
    stopped = await evaluate('window.__probeSubscribe()');
    if (stopped.state === 'stopped' || stopped.state === 'fail') break;
    await delay(100);
  }
  await record('shutdown completes despite unacknowledged real Channel', stopped.state === 'stopped');
  await command('Page.reload');
  await delay(700);
  await record('renderer recovers confirmed stopped state after reload', await evaluate(`document.body.innerText.includes('Shutdown was confirmed')`));
  await record('blocked content made no network request', hits === 0);
  }
} catch (error) {
  await writeFile(options.output, JSON.stringify({ schema: 1,
    instrumentation: 'per-process WebView2 CDP; CSP enforced', checks,
    failure: { stage, kind: error instanceof assert.AssertionError ? 'ASSERTION' : 'OBSERVATION_ERROR',
      shellExited: child.exitCode !== null } }, null, 2) + '\n');
  console.error(error.message);
  process.exitCode = 1;
} finally {
  socket?.close();
  if (child.exitCode === null) {
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill();
    await Promise.race([exited, delay(5000)]);
  }
  trap.close();
}
