// Production controls, restricted to a synthetic ledger or an isolated rehearsal copy.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';

const origin = process.argv[2];
assert.equal(new URL(origin).hostname, '127.0.0.1');
const output = path.resolve(process.argv[3]);
const root = path.resolve(import.meta.dirname, '..');
assert(output.startsWith(path.join(root, 'qa') + path.sep));
assert(!fs.existsSync(output), 'Use a fresh acceptance directory');
fs.mkdirSync(output, {recursive:true});
const profile = path.join(output, 'edge-profile');
const downloads = path.join(output, 'downloads');
fs.mkdirSync(downloads);
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`, '--no-first-run', '--disable-extensions',
  '--disable-background-networking', '--disable-component-update', 'about:blank',
], {windowsHide:true, stdio:'ignore'});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const pending = new Map();
const errors = [], requests = [], checks = [];
let socket, counter = 0;

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++counter;
    const timeout = setTimeout(() => {
      pending.delete(id); reject(new Error('CDP timeout: ' + method));
    }, 45000);
    pending.set(id, {resolve,reject,timeout});
    socket.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression,awaitPromise:true,returnByValue:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression) {
  for (let index = 0; index < 450; index++) {
    if (await evaluate(expression)) return;
    await sleep(100);
  }
  throw new Error('Timed out: ' + expression);
}
async function route(name) {
  await evaluate(`location.hash=${JSON.stringify(name)}`);
  await until(`state.route===${JSON.stringify(name.split('?')[0])} && main.getAttribute('aria-busy')==='false'`);
  await sleep(220);
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot', {format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,name + '.png'), Buffer.from(result.data,'base64'));
}
async function selectFile(file) {
  const document = await call('DOM.getDocument');
  const input = await call('DOM.querySelector', {nodeId:document.root.nodeId,selector:'#restore-file'});
  assert(input.nodeId);
  await call('DOM.setFileInputFiles', {nodeId:input.nodeId,files:[file]});
}
async function closeEditor() {
  await evaluate("document.querySelector('#editor-cancel').click()");
  await until('!editor.open && !document.querySelector("#editor-save").disabled');
}
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

try {
  const activePort = path.join(profile,'DevToolsActivePort');
  for (let index=0; !fs.existsSync(activePort) && index<100; index++) await sleep(70);
  const port = fs.readFileSync(activePort,'utf8').split('\n')[0].trim();
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(pages.find(page=>page.type==='page').webSocketDebuggerUrl);
  await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
  socket.addEventListener('message', event => {
    const message=JSON.parse(event.data);
    if(message.id) {
      const waiter=pending.get(message.id);
      if(!waiter)return;
      pending.delete(message.id);clearTimeout(waiter.timeout);
      message.error?waiter.reject(message.error):waiter.resolve(message.result);
    }
    if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
    if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);
  });
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Page.setDownloadBehavior', {behavior:'allow',downloadPath:downloads});
  await call('Emulation.setDeviceMetricsOverride', {width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('(state.boot.demo || state.boot.rehearsal) && !state.boot.read_only'),
    'Never edit or restore the live ledger during acceptance');
  const baselineDashboard = await evaluate("api('dashboard?view=finance')");
  const baselineCosts = await evaluate("api('rows?module=costs')");
  const id = baselineCosts.rows.find(row=>row.id)?.id;
  assert(id, 'Rehearsal requires an existing cost to test correction');
  const recordQuery = 'record?module=costs&id=' + encodeURIComponent(id);
  const baselineRecord = await evaluate(`api(${JSON.stringify(recordQuery)})`);
  const oldToken = await evaluate('state.boot.csrf');
  await route('tools');
  await evaluate("document.querySelector('#create-backup').click()");
  await until('!!document.querySelector("#backup-result a") && !document.querySelector("#create-backup").disabled');
  await screenshot('01_complete_backup');
  await evaluate("document.querySelector('#backup-result a').click()");
  let zip;
  for(let index=0;index<200;index++) {
    zip=fs.readdirSync(downloads).find(name=>name.endsWith('.zip'));
    if(zip && !fs.readdirSync(downloads).some(name=>name.endsWith('.crdownload')))break;
    await sleep(100);
  }
  assert(zip, 'Backup download completed');
  const archive=path.join(downloads,zip);
  checks.push('Full ZIP downloaded through the visible backup link');

  await route('cost?tab=costs');
  await evaluate(`editRecord('costs',${id})`);
  await until('editor.open && !!form.elements.confirmed');
  await evaluate("form.elements.notes.value+=' [隔离副本恢复验收]'");
  await closeEditor();
  assert.deepEqual(await evaluate(`api(${JSON.stringify(recordQuery)})`),baselineRecord);
  await evaluate(`editRecord('costs',${id})`);
  await until('editor.open && !!form.elements.confirmed');
  await evaluate("form.elements.notes.value+=' [隔离副本恢复验收]';form.elements.confirmed.checked=true;form.requestSubmit()");
  await until('!editor.open && !document.querySelector("#editor-save").disabled && main.getAttribute("aria-busy")==="false"');
  const changedRecord=await evaluate(`api(${JSON.stringify(recordQuery)})`);
  assert(changedRecord.notes.includes('[隔离副本恢复验收]'));
  assert.equal(changedRecord.amount,baselineRecord.amount);
  checks.push('Cancel preserves the record; a confirmed note correction preserves its amount');

  async function preview() {
    await selectFile(archive);
    await evaluate("main.querySelector('details').open=true;document.querySelector('#preview-restore').click()");
    await until('editor.open && !!form.elements.namedItem("restore-confirmation")');
  }
  await route('tools');await preview();
  assert.equal(await evaluate("form.elements.namedItem('restore-confirmed').checked"),false);
  await screenshot('02_restore_preflight');
  await closeEditor();
  assert.deepEqual(await evaluate(`api(${JSON.stringify(recordQuery)})`),changedRecord);
  checks.push('Chunked upload and preflight cancel do not overwrite the ledger');
  await preview();
  await evaluate("form.elements.namedItem('restore-confirmation').value='恢复该账本';form.elements.namedItem('restore-confirmed').checked=true;form.requestSubmit()");
  await until('!!document.querySelector("#reload-restored") && !document.querySelector("#editor-save").disabled');
  await screenshot('03_restored_with_safety_copy');
  await evaluate("document.querySelector('#reload-restored').click()");
  await until('typeof state!=="undefined" && state.boot && !editor.open && main.getAttribute("aria-busy")==="false"');
  assert.notEqual(await evaluate('state.boot.csrf'),oldToken);
  assert.deepEqual(await evaluate(`api(${JSON.stringify(recordQuery)})`),baselineRecord);
  assert.deepEqual(await evaluate("api('rows?module=costs')"),baselineCosts);
  assert.deepEqual(await evaluate("api('dashboard?view=finance')"),baselineDashboard);
  checks.push('Confirmed restoration returns the exact records and financial dashboard to baseline');

  const routes=await evaluate('state.boot.navigation.flatMap(group=>group[1].map(item=>item[0]))');
  for(const name of routes)await route(name);
  checks.push(`${routes.length} navigation pages loaded after restoring and rotating the form token`);
  await route('finance?tab=receipts');
  assert(await evaluate('main.textContent.includes("查看已作废回款记录")'));
  await screenshot('04_individual_receipts');
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:720,deviceScaleFactor:1,mobile:false});
  await route('tools');await screenshot('05_backup_960px');
  assert.deepEqual(errors,[]);
  assert.deepEqual(requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin)),[]);
  const result={checks,errors,external_requests:0,archive_bytes:fs.statSync(archive).size,
    finance_dashboard_sha256:hash(baselineDashboard),cost_record_sha256:hash(baselineRecord),
    archive,scope:'Isolated real-ledger copy; no live business writes'};
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
} catch(error) {
  if(socket?.readyState===WebSocket.OPEN)await screenshot('FAILED');
  throw error;
} finally {
  if(socket?.readyState===WebSocket.OPEN) {
    try {await call('Browser.close');}catch{}
    socket.close();
  }
  browser.kill();
}
