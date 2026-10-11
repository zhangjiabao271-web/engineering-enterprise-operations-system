// Focused browser acceptance against a fresh synthetic, writable database only.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin = process.argv[2];
assert(new URL(origin).hostname === '127.0.0.1', 'Loopback only');
const output = path.resolve(process.argv[3] || 'qa/web-iteration');
fs.mkdirSync(output, {recursive: true});
const profile = path.join(output, 'edge-profile');
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const browser = spawn(edge, ['--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`, '--no-first-run', '--disable-background-networking', '--disable-component-update', 'about:blank'],
  {windowsHide: true, stdio: 'ignore'});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const activePort = path.join(profile, 'DevToolsActivePort');
for (let index = 0; !fs.existsSync(activePort) && index < 100; index++) await sleep(60);
const port = fs.readFileSync(activePort, 'utf8').split('\n')[0].trim();
const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(pages.find(item => item.type === 'page').webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, {once: true}));
let sequence = 0;
const pending = new Map();
const errors = [];
const requests = [];
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const waiting = pending.get(message.id);
    pending.delete(message.id);
    message.error ? waiting.reject(message.error) : waiting.resolve(message.result);
  }
  if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
});
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression) {
  for (let index = 0; index < 120; index++) {
    if (await evaluate(expression)) return;
    await sleep(60);
  }
  throw new Error('Timed out: ' + expression + ' / ' + await evaluate('main.textContent + document.querySelector("#form-error").textContent'));
}
async function route(name) {
  await evaluate(`location.hash=${JSON.stringify(name)}`);
  await until(`state.route===${JSON.stringify(name)} && !main.querySelector('.loading')`);
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  fs.writeFileSync(path.join(output, name + '.png'), Buffer.from(result.data, 'base64'));
}
async function choose(selector, keyword) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).closest('.search-choice').querySelector('input').focus(); document.querySelector(${JSON.stringify(selector)}).closest('.search-choice').querySelector('input').select()`);
  await call('Input.insertText', {text: keyword});
  await call('Input.dispatchKeyEvent', {type: 'keyDown', key: 'ArrowDown', code: 'ArrowDown'});
  await call('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Enter', code: 'Enter'});
  await call('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Enter', code: 'Enter'});
}
try {
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 960, deviceScaleFactor: 1, mobile: false});
  await call('Page.navigate', {url: origin});
  await until('typeof state !== "undefined" && state.boot && !main.querySelector(".loading")');
  assert.equal(await evaluate('state.boot.demo && !state.boot.read_only'), true, 'Only fresh synthetic writable data');
  await route('assistant');
  assert.equal(await evaluate('document.querySelector("#chat-model").checked'), false);
  await screenshot('01_chat_empty');
  await choose('#project-scope', 'DEMO-001');
  await until('assistantView.snapshot.project_id===1 && !main.querySelector(".loading")');
  await evaluate(`document.querySelector('#chat-question').value='哪些发票还有余额';document.querySelector('#chat-form').requestSubmit()`);
  await until(`assistantView.snapshot.messages.length===2 && !assistantView.snapshot.busy`);
  assert.equal(await evaluate(`assistantView.snapshot.messages[1].message_type==='error'`), false);
  assert.equal(await evaluate(`assistantView.snapshot.messages[1].sources.length>0`), true);
  await screenshot('02_chat_local_answer');
  const sessionId = await evaluate('assistantView.session');
  await call('Page.reload', {ignoreCache:true});
  await until('typeof state!=="undefined" && state.boot && document.querySelector("#chat-form") && assistantView.snapshot?.messages.length===2');
  assert.equal(await evaluate('assistantView.session'), sessionId, 'Refresh reuses in-memory conversation');
  assert.equal(await evaluate('document.querySelector("#chat-model").checked'), false);
  await evaluate(`document.querySelector('#chat-reset').click()`);
  await until('assistantView.snapshot.messages.length===0');
  await evaluate(`document.querySelector('#chat-question').value='收入最多的客户是谁？';document.querySelector('#chat-form').requestSubmit()`);
  await until('assistantView.snapshot.messages.length===2 && !assistantView.snapshot.busy');
  assert.equal(await evaluate('assistantView.snapshot.messages[1].error_code'), 'model_consent_required');
  await choose('#project-scope', 'DEMO-001');
  await until(`assistantView.snapshot.project_id===1 && !main.querySelector('.loading')`);
  await route('purchases');
  await evaluate('document.querySelector("#new-record").click()');
  await until('editor.open && document.querySelector(".purchase-item")');
  assert.equal(await evaluate('form.querySelector("[name=item-price_basis]").value'), 'inclusive', 'New item defaults to inclusive price');
  await choose('select[name=project_id]', 'DEMO-001');
  await choose('select[name=supplier_id]', '钢材供应商');
  await evaluate(`form.querySelector('[name=item-material_name_snapshot]').value='合成批量验收PVC瓦';form.querySelector('[name=item-unit_snapshot]').value='米';form.querySelector('[name=item-quantity]').value='996';form.querySelector('[name=item-unit_price]').value='1.89';form.querySelector('[name=item-price_basis]').value='inclusive';form.querySelector('[name=item-tax_percent]').value='13';form.elements.freight_amount.value='1.01';form.querySelector('[name=item-unit_price]').dispatchEvent(new Event('input',{bubbles:true}))`);
  await until(`document.querySelector('.item-preview').textContent.includes('1,882.44') && document.querySelector('#purchase-preview').textContent.includes('1,883.45')`);
  await evaluate(`document.querySelector('.item-preview').scrollIntoView({block:'center'})`);
  await screenshot('03_purchase_preview');
  await evaluate(`form.elements.namedItem('continue-entry').checked=true;form.elements.confirmed.checked=true;form.requestSubmit()`);
  await until(`editor.open && form.elements.confirmed.checked===false && Number(form.querySelector('[name=item-unit_price]').value)===0 && form.elements.namedItem('continue-entry').checked`);
  assert.equal(await evaluate('form.elements.project_id.value'), '1');
  assert.equal(await evaluate('form.elements.supplier_id.value'), '2');
  const purchase = await evaluate(`api('rows?module=purchases').then(result=>result.rows.find(row=>row.total_amount_cents==='188345'))`);
  assert(purchase, 'Exact preview amount saved');
  await evaluate('document.querySelector("#editor-cancel").click()');
  await until('!editor.open');
  await route('labor');
  await evaluate('document.querySelector("#new-record").click()');
  await until('editor.open && form.elements.work_days');
  await choose('select[name=worker_id]', '安装工');
  await choose('select[name=project_id]', 'DEMO-001');
  await evaluate(`form.elements.construction_site.value='合成连续录入作业地';form.elements.work_date.value='2026-10-03';form.querySelector('[data-workday="0.5"]').click();form.elements.confirmed.checked=true;form.elements.namedItem('continue-entry').checked=true`);
  assert.equal(await evaluate('form.elements.work_days.value'), '0.5');
  await screenshot('04_workday_form');
  await evaluate('form.requestSubmit()');
  await until('editor.open && !form.elements.worker_id.value && form.elements.work_date.value==="2026-10-03" && !form.elements.confirmed.checked');
  await evaluate('document.querySelector("#editor-cancel").click()');
  await until('!editor.open');
  await route('finance');
  await evaluate(`main.querySelector('[data-tab="receipts"]').click()`);
  await until('main.querySelector("[data-tab=receipts].active")');
  await evaluate(`document.querySelector('#new-record').click()`);
  await until('editor.open && form.elements.payer_name');
  await choose('select[name=project_id]', 'DEMO-001');
  await until(`form.elements.payer_name.value.includes('建设单位')`);
  await choose('select[name=contract_id]', 'DEMO-HT-001');
  await evaluate(`form.elements.payer_name.value='合成代付款人';form.elements.contract_id.dispatchEvent(new Event('change'))`);
  await sleep(100);
  assert.equal(await evaluate('form.elements.payer_name.value'), '合成代付款人');
  await screenshot('05_receipt_payer');
  await evaluate('document.querySelector("#editor-cancel").click()');
  await until('!editor.open');
  await route('assistant');
  await evaluate(`drawAssistant({messages:[{role:'assistant',generation:100,content:'## 已核对结论\\n\\n客户 **合成建设单位** 贡献最多。\\n\\n| 客户 | 金额 |\\n|---|---|\\n| 合成建设单位 | ¥100,000.00 |\\n\\n<img src=x onerror="window.unsafeRender=true">',sources:[{module:'收入',label:'核对收入明细',record_count:1,columns:[{key:'amount_minor',label:'金额'}],details:[{amount_minor:'10000000'}]}]}],project_id:1,generation:100,busy:false,stream:''})`);
  assert.equal(await evaluate('!!document.querySelector(".chat-answer table")'), true);
  assert.equal(await evaluate('!!document.querySelector(".chat-answer img") || !!window.unsafeRender'), false);
  await evaluate(`assistantView.snapshot.messages[0].content=assistantView.snapshot.messages[0].content.split('<img')[0];assistantView.snapshot.messages[0].project_id=1;drawAssistant(assistantView.snapshot);document.querySelector('#project-scope').closest('.search-choice').querySelector('input').blur();document.querySelector('#toast').hidden=true`);
  await screenshot('06_chat_structured');
  await call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  await screenshot('07_chat_mobile');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'), true);
  await route('purchases');
  await evaluate('document.querySelector("#new-record").click()');
  await until('editor.open && document.querySelector(".purchase-item")');
  await screenshot('08_purchase_mobile');
  assert.equal(await evaluate('editor.scrollWidth<=editor.clientWidth'), true);
  assert.equal(await evaluate('document.querySelector(".dialog-foot").getBoundingClientRect().bottom <= innerHeight'), true);
  await evaluate(`form.querySelector('[name=item-settlement_mode]').value='weight';form.querySelector('[name=item-settlement_mode]').dispatchEvent(new Event('change'))`);
  assert.equal(await evaluate('form.querySelector("[name=item-unit_price]").required'), false);
  assert.equal(await evaluate('form.querySelector("[name=item-net_weight]").required'), true);
  assert.deepEqual(errors, [], 'No browser exceptions');
  assert.deepEqual(requests.filter(url => url.startsWith('http') && !url.startsWith(origin)), [], 'No external calls without consent');
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({checks: [
    'default model consent off', 'real local assistant sources', 'refresh restores session', 'no model without consent',
    'project search keyboard selection', 'exact purchase preview and save', 'continuous purchase and workday entry',
    'half-day shortcut', 'automatic payer preserving manual changes', 'safe structured answer',
    'mobile chat and purchase form', 'progressive weight fields', 'no external requests', 'no JS errors'], errors}, null, 2));
  console.log('Focused browser checks passed; evidence:', output);
} catch (error) {
  await screenshot('failure');
  console.error(await evaluate(`JSON.stringify({editorOpen:editor.open,formError:document.querySelector('#form-error').textContent,toast:document.querySelector('#toast').textContent,fields:[...form.querySelectorAll('[name]')].map(input=>({name:input.name,value:input.value,valid:input.validity.valid}))})`));
  throw error;
} finally {
  try { await call('Browser.close'); } catch {}
  ws.close();
  browser.unref();
}
