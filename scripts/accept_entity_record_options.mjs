// Verify generic entity-operation choices in tools; the main registry has its own acceptance script.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';

const origin = process.argv[2];
const output = path.resolve(process.argv[3]);
const root = path.resolve(import.meta.dirname, '..');
assert.equal(new URL(origin).hostname, '127.0.0.1');
assert(output.startsWith(path.join(root, 'qa') + path.sep));
assert(!fs.existsSync(output), 'Use a fresh acceptance directory');
fs.mkdirSync(output, {recursive:true});
const profile = path.join(output, 'edge-profile');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, socket;
let sequence = 0;
const pending = new Map();
const errors = [], requests = [], checks = [];
function receiveMessage(event) {
  const message = JSON.parse(event.data);
  if (message.id) {
    const waiter = pending.get(message.id);
    pending.delete(message.id);
    if (waiter) {
      clearTimeout(waiter.timeout);
      message.error ? waiter.reject(message.error) : waiter.resolve(message.result);
    }
  }
  if (message.method==='Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  if (message.method==='Network.requestWillBeSent') requests.push(message.params.request.url);
}
function call(method, params={}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => {pending.delete(id); reject(new Error('Browser timeout: ' + method));}, 20000);
    pending.set(id, {resolve, reject, timeout});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression) {
  for (let index=0; index<200; index++) {
    if (await evaluate(expression)) return;
    await sleep(60);
  }
  throw new Error('Timed out: ' + expression);
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
  await sleep(120);
  const point = await evaluate(`(()=>{const item=document.querySelector(${JSON.stringify(selector)}),box=item.getBoundingClientRect(),x=box.x+box.width/2,y=box.y+box.height/2;return {x,y,hit:item.contains(document.elementFromPoint(x,y))};})()`);
  assert(point.hit, 'Pointer cannot reach ' + selector);
  for (const type of ['mouseMoved','mousePressed','mouseReleased']) {
    await call('Input.dispatchMouseEvent', {type, x:point.x, y:point.y,
      button:type==='mouseMoved'?'none':'left', clickCount:type==='mouseMoved'?0:1});
  }
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
  fs.writeFileSync(path.join(output, name + '.png'), Buffer.from(result.data, 'base64'));
}

try {
  const response = await fetch(origin, {signal:AbortSignal.timeout(5000)});
  assert(response.ok, 'Synthetic fixture server is unreachable');
  browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--user-data-dir=' + profile, '--no-first-run', '--disable-background-networking',
    '--disable-component-update', 'about:blank',
  ], {windowsHide:true, stdio:'ignore'});
  const portFile = path.join(profile, 'DevToolsActivePort');
  for (let index=0; !fs.existsSync(portFile) && index<100; index++) await sleep(60);
  assert(fs.existsSync(portFile), 'Acceptance browser did not start');
  const port = fs.readFileSync(portFile, 'utf8').split('\n')[0].trim();
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {
    socket.addEventListener('open', resolve, {once:true});
    socket.addEventListener('error', reject, {once:true});
  });
  socket.addEventListener('message', receiveMessage);
  await call('Runtime.enable'); await call('Page.enable'); await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride', {width:1366, height:768, deviceScaleFactor:1, mobile:false});
  await call('Page.navigate', {url:origin});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'), 'Only synthetic writable ledgers are allowed');
  const before = await evaluate("api('qa_state').then(result=>result.digest)");
  await evaluate("location.hash='tools'");
  await until('state.route==="tools" && main.getAttribute("aria-busy")==="false"');
  await click('[data-operation="entity_assign"]');
  await until('editor.open && !!form.elements.namedItem("ids")');
  const kinds = await evaluate('[...form.elements.kind.options].map(option=>option.value).filter(Boolean)');
  assert.equal(kinds.length, 9);
  for (const kind of kinds) {
    await evaluate(`form.elements.kind.value=${JSON.stringify(kind)};form.elements.kind.dispatchEvent(new Event('change'))`);
    await until('!form.elements.namedItem("ids").disabled && !editorLookups.size');
    const {rows, options} = await evaluate(`(async()=>({rows:await api('operation_read?'+query({name:'entity_records',kind:${JSON.stringify(kind)},assignable:'1'})),options:[...form.elements.namedItem('ids').options].filter(option=>option.value).map(option=>({value:option.value,text:option.textContent}))}))()`);
    assert.equal(options.length, rows.length, kind);
    for (const row of rows) {
      const option = options.find(item => item.value===String(row.id));
      assert(option && option.text.includes(row.label), kind + ': missing record label');
      assert(option.text.startsWith('#' + row.id + ' · '), kind + ': missing record ID');
      if (row.date) assert(option.text.includes(row.date), kind + ': missing date');
      if (row.amount_minor!==null && row.amount_minor!==undefined) assert(option.text.includes('¥'), kind + ': missing amount');
    }
    checks.push(kind + ': names and IDs match backend records');
  }
  await evaluate("form.elements.kind.value='contracts';form.elements.kind.dispatchEvent(new Event('change'))");
  await until('!form.elements.namedItem("ids").disabled && !editorLookups.size');
  await screenshot('01_contract_choices');
  const selected = await evaluate("(()=>{const select=form.elements.namedItem('ids');for(const option of select.options)option.selected=Boolean(option.value);form.elements.entity_id.value=[...form.elements.entity_id.options].find(option=>option.value).value;form.elements.entity_id.dispatchEvent(new Event('change'));form.elements.reason.value='合成验收：仅预览，不保存';return [...select.selectedOptions].map(option=>option.value);})()");
  assert(selected.length>0);
  await click('#editor-save');
  await until('editor.open && document.querySelector("#dialog-title").textContent.startsWith("确认预览") && !editorSubmitting');
  assert(await evaluate('editor.textContent.includes("合成演示 · 厂房钢结构工程")'));
  await screenshot('02_named_preview');
  await click('#editor-cancel');
  await until('!editor.open');
  assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"), before);
  checks.push('selected records preview by name; cancel keeps the ledger unchanged');
  assert.deepEqual(errors, []);
  assert.deepEqual(requests.filter(url=>url.startsWith('http') && !url.startsWith(origin)), []);
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({checks, errors, external_requests:0}, null, 2));
  console.log(JSON.stringify({passed:checks.length, checks, errors}));
} catch (error) {
  if (socket?.readyState===WebSocket.OPEN) {
    try { await screenshot('FAILED'); } catch {}
  }
  throw error;
} finally {
  if (socket?.readyState===WebSocket.OPEN) {
    try { await call('Browser.close'); } catch {}
  }
  socket?.close();
  browser?.kill();
}
