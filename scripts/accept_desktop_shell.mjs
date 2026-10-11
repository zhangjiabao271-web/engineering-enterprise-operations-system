// Real Edge acceptance for the native window's shared frontend; synthetic ledger only.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin = process.argv[2];
assert.equal(new URL(origin).hostname, '127.0.0.1');
const output = path.resolve(process.argv[3] || 'qa/desktop-shell');
fs.mkdirSync(output, {recursive: true});
const profile = path.join(output, 'edge-profile');
const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`, '--no-first-run', '--disable-extensions',
  '--disable-background-networking', '--disable-component-update', 'about:blank',
], {windowsHide: true, stdio: 'ignore'});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const activePort = path.join(profile, 'DevToolsActivePort');
let ws;
let sequence = 0;
const pending = new Map();
const errors = [];
const results = {checks: [], routeSamples: [], frameIntervals: []};

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 15000);
    pending.set(id, {resolve, reject, timeout});
    ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression) {
  for (let index = 0; index < 150; index++) {
    if (await evaluate(expression)) return;
    await sleep(50);
  }
  throw new Error('Timed out: ' + expression);
}
async function route(name) {
  await evaluate(`location.hash=${JSON.stringify(name)}`);
  await until(`state.route===${JSON.stringify(name.split('?')[0])} && main.getAttribute('aria-busy')==='false'`);
  await sleep(210);
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  fs.writeFileSync(path.join(output, name + '.png'), Buffer.from(result.data, 'base64'));
}

try {
  for (let index = 0; !fs.existsSync(activePort) && index < 100; index++) await sleep(60);
  const port = fs.readFileSync(activePort, 'utf8').split('\n')[0].trim();
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(pages.find(item => item.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, {once: true}));
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id); clearTimeout(waiter.timeout);
      message.error ? waiter.reject(message.error) : waiter.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  });
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 960, deviceScaleFactor: 1, mobile: false});
  await call('Page.navigate', {url: origin});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert.equal(await evaluate('state.boot.demo && !state.boot.read_only'), true, 'Synthetic writable data required');
  await sleep(220);
  assert.equal(await evaluate('document.querySelectorAll("[aria-current=page]").length'), 1);
  assert.equal(await evaluate('main.querySelector("a[href=\\"#finance?tab=receipts\\"]")!==null'), true);
  await screenshot('01_home');
  await route('finance?tab=receipts');
  assert.equal(await evaluate('main.querySelector("[data-tab=receipts].active")!==null'), true);
  results.checks.push('Home shortcut opens individual receipts, not invoice summaries');
  await route('profit');
  await evaluate('main.querySelector("[data-project-cost]").click()');
  await until('editor.open && document.querySelector("#dialog-title").textContent.includes("成本明细")');
  assert.equal(await evaluate('document.querySelector("#editor-body").textContent.includes("合成演示")'), true);
  assert.equal(await evaluate('editor.querySelectorAll("[data-cost-tab]").length'), 3);
  const detailTotals = await evaluate(`api('operation_read?name=project_cost_details&id=1').then(data=>({total:data.total_cost_minor,parts:Object.values(data.totals).reduce((sum,value)=>sum+BigInt(value),0n).toString()}))`);
  assert.equal(detailTotals.total, detailTotals.parts, 'Costs reconcile in integer cents');
  await evaluate('editor.querySelector("[data-cost-tab=labor]").click()');
  assert.equal(await evaluate('editor.querySelector("#project-cost-table th").textContent'), '出勤日期');
  await evaluate('editor.querySelector("[data-cost-tab=other]").click()');
  assert.equal(await evaluate('editor.querySelector("#project-cost-table th").textContent'), '发生日期');
  await evaluate('editor.querySelector("[data-cost-tab=purchase]").click()');
  await sleep(210); await screenshot('02_project_cost_details');
  // Reopening during a cancelled close must not close the new dialog.
  await evaluate('closeEditor();openEditor("新的明细", "<p>保留新内容</p>", null)');
  await sleep(220);
  assert.equal(await evaluate('editor.open && document.querySelector("#dialog-title").textContent==="新的明细"'), true);
  await evaluate('closeEditor()');
  assert.equal(await evaluate('editor.open'), false);
  results.checks.push('Cost drilldown, modal open/close and interrupted close preserve the current dialog');
  await screenshot('03_profit');
  assert.equal(await evaluate('getComputedStyle(main.querySelector("td.table-actions")).position'), 'sticky');

  for (let index = 0; index < 18; index++) {
    const name = ['home', 'profit', 'finance'][index % 3];
    const measurement = await evaluate(`(async()=>{
      const intervals=[]; let previous=null; let running=true;
      const frame=time=>{if(previous!==null)intervals.push(time-previous);previous=time;if(running)requestAnimationFrame(frame);};
      requestAnimationFrame(frame); const started=performance.now();
      location.hash=${JSON.stringify(name)}; await renderRoute();
      const load=performance.now()-started;
      const properties=main.getAnimations({subtree:true}).flatMap(animation=>animation.effect.getKeyframes().flatMap(frame=>Object.keys(frame))).filter(key=>!['offset','computedOffset','easing','composite'].includes(key));
      await new Promise(resolve=>setTimeout(resolve,240));running=false;
      return {load,intervals,properties,active:WorkbenchMotion.activeCount};
    })()`);
    assert(measurement.properties.every(key => ['opacity', 'transform'].includes(key)), 'Only compositor-friendly properties');
    assert.equal(measurement.active, 0, 'No animation accumulates after a switch');
    results.routeSamples.push({route: name, loadMs: measurement.load});
    results.frameIntervals.push(...measurement.intervals);
  }
  await evaluate(`(async()=>{for(const name of ['home','profit','assistant','finance','purchases','labor','home']){location.hash=name;await new Promise(resolve=>setTimeout(resolve,12));}})()`);
  await until('state.route==="home" && main.getAttribute("aria-busy")==="false"');
  await sleep(250);
  assert.equal(await evaluate('WorkbenchMotion.activeCount'), 0);
  assert.equal(await evaluate('getComputedStyle(main).transform'), 'none');
  assert.equal(await evaluate('main.querySelector("h1").textContent'), '经营驾驶舱');
  results.checks.push('18 measured switches plus rapid interrupted navigation leave no stale page/animation');

  await call('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Tab', code: 'Tab'});
  await call('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Tab', code: 'Tab'});
  await evaluate('WorkbenchMotion.finishRoute(main)');
  assert.equal(await evaluate('WorkbenchMotion.activeCount'), 0, 'Keyboard navigation is immediate');
  await call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
  await until('WorkbenchMotion.reduced');
  assert.equal(await evaluate('document.querySelector("#reduced-motion").disabled'), true);
  await route('profit');
  assert.equal(await evaluate('WorkbenchMotion.activeCount'), 0);
  await call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'no-preference'}]});
  await until('!WorkbenchMotion.reduced');
  await evaluate('document.querySelector("#reduced-motion").click();document.querySelector("#solid-glass").click()');
  await call('Page.reload', {ignoreCache: true});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert.equal(await evaluate('WorkbenchMotion.reduced && document.body.classList.contains("solid-glass")'), true);
  assert.equal(await evaluate('getComputedStyle(document.querySelector("#sidebar")).backdropFilter'), 'none');
  results.checks.push('System reduced motion cannot be overridden; user reduced/solid preferences survive reload');
  await evaluate('document.querySelector("#reduced-motion").click();document.querySelector("#solid-glass").click()');

  await route('assistant');
  const longContent = '## 已核对结论\n\n这是中文长内容的布局测试，不是业务数据。\n\n| 日期 | 项目与说明 | 金额 |\n| --- | --- | --- |\n| 2026-10-01 | ' + '超长中文项目说明'.repeat(8) + ' | ¥1882.44 |\n\n' + '完整中文不裁切，金额与日期保持独立。'.repeat(70);
  const messages = [{role:'user',content:'请核对这个项目的全部成本和回款，不要合并不同项目。',project_id:null},
    {role:'assistant',content:longContent,project_id:null,sources:[]}];
  await evaluate(`drawAssistant({...assistantView.snapshot,messages:${JSON.stringify(messages)},busy:false})`);
  await screenshot('04_assistant_long');
  for (const [width, height] of [[960, 720], [390, 844]]) {
    await call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 760});
    await sleep(100);
    assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'), true, 'No document horizontal overflow');
    assert.equal(await evaluate('document.querySelector(".chat-answer .table-wrap").scrollWidth>document.querySelector(".chat-answer .table-wrap").clientWidth'), true);
    assert.equal(await evaluate('document.querySelector("#chat-send").getBoundingClientRect().width>40'), true);
    await screenshot('05_assistant_' + width);
  }
  results.checks.push('Long Chinese and wide answer tables scroll internally at 960px and 390px');
  assert.equal(errors.length, 0, JSON.stringify(errors));
  const frames = results.frameIntervals.slice().sort((a, b) => a - b);
  const loads = results.routeSamples.map(row => row.loadMs).sort((a, b) => a - b);
  results.performance = {
    frameSamples: frames.length,
    medianFrameMs: frames[Math.floor(frames.length * .5)],
    p95FrameMs: frames[Math.floor(frames.length * .95)],
    longestFrameMs: frames.at(-1),
    over34Ms: frames.filter(value => value > 34).length,
    medianRouteLoadMs: loads[Math.floor(loads.length * .5)],
    maxRouteLoadMs: loads.at(-1),
    note: 'Headless Edge on this machine; not a promise of refresh rate on every device.',
  };
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({checks: results.checks, performance: results.performance}, null, 2));
} finally {
  if (ws?.readyState === WebSocket.OPEN) {
    try { await call('Browser.close'); } catch { /* Browser may close before replying. */ }
    ws.close();
  }
  browser.kill();
}
