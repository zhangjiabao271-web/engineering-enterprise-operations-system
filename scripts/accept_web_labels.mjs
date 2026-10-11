// Render-only checks; fixture mutations are limited to a new synthetic ledger.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin=process.argv[2], output=path.resolve(process.argv[3]);
assert.equal(new URL(origin).hostname,'127.0.0.1');
assert(!fs.existsSync(output),'Use a new acceptance directory');
fs.mkdirSync(output,{recursive:true});
const profile=path.join(output,'edge-profile');
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
  '--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','--disable-component-update','about:blank',
],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const portFile=path.join(profile,'DevToolsActivePort');
for(let attempt=0;!fs.existsSync(portFile)&&attempt<100;attempt++)await sleep(60);
const port=fs.readFileSync(portFile,'utf8').split('\n')[0].trim();
const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const socket=new WebSocket(pages.find(page=>page.type==='page').webSocketDebuggerUrl);
await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
let sequence=0;
const pending=new Map(), errors=[], requests=[], checks=[], missing=new Set(), audited=new Set(), untranslated=new Set();
socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id){const waiter=pending.get(message.id);pending.delete(message.id);if(waiter){clearTimeout(waiter.timeout);message.error?waiter.reject(message.error):waiter.resolve(message.result);}}
  if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
  if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);
});
function call(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence,timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Browser timeout: '+method));},20000);
  pending.set(id,{resolve,reject,timeout});socket.send(JSON.stringify({id,method,params}));
});}
async function evaluate(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value;}
async function until(expression){for(let attempt=0;attempt<160;attempt++){if(await evaluate(expression))return;await sleep(60);}throw new Error('Timed out: '+expression);}
async function screenshot(name){const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));}
async function audit(expression,module=''){
  const result=await evaluate(`(async()=>{
    const facts=await (${expression}),keys=new Set(),codes=new Set();
    function scan(value){
      if(Array.isArray(value)){value.forEach(scan);return;}
      if(value && typeof value==='object')for(const [key,item] of Object.entries(value)){
        if(!visibleEvidenceKey(key))continue;
        keys.add(key);scan(item);
        if(typeof item==='string' && /^[a-z][a-z_]+$/.test(item) &&
          /(^status$|^roles$|^kind$|^method$|^category$|_status$|_mode$|_kind$|_type$|_policy$|_code$|^tax_identity$)/.test(key) &&
          display(item,key)===item)codes.add(key+':'+item);
        if(key.endsWith('_json') && typeof item==='string'){try{scan(JSON.parse(item));}catch{}}
      }
    }
    scan(facts);
    return {keys:[...keys],codes:[...codes],missing:[...keys].filter(key=>!knownFieldLabel(key,${JSON.stringify(module)}))};
  })()`);
  result.keys.forEach(key=>audited.add(key));result.missing.forEach(key=>missing.add(key));
  result.codes.forEach(code=>untranslated.add(code));
}
try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Only a synthetic writable test ledger is allowed');
  const cases=await evaluate("api('qa_cases')");
  for(const test of cases.filter(test=>test.kind==='edit' || test.kind==='operation')){
    const fixture=await evaluate(`api('qa_fixture',{name:${JSON.stringify(test.name)}})`);
    if(test.kind==='edit')await audit(`api('record?'+query({module:${JSON.stringify(test.module)},id:${fixture.id}}))`,test.module);
    else{
      await audit(`api('operation_preview',{name:${JSON.stringify(test.name)},record:${JSON.stringify(fixture.record)}}).then(result=>result.review)`);
    }
  }
  await evaluate("api('qa_fixture',{name:'edit_receipts'})");
  for(const view of ['home','profit','governance','cost','finance','collection'])await audit(`api('dashboard?'+query({view:${JSON.stringify(view)}}))`);
  const reads=await evaluate('Object.values(state.boot.operations).filter(definition=>definition.read).map(definition=>definition.read)');
  for(const name of new Set([...reads,'sites','fund_sources','reconciliations','entities','entity_records','input_candidates','input_links','historical_receipts','collection_case','unassigned_labor','unassigned_purchases','fulfillment_gaps','labor_month','worker_month'])){
    await audit(`api('operation_read?'+query({name:${JSON.stringify(name)},id:1,entity_id:1,month:'2026-10',start:'2026-10-01',end:'2026-10-31'}))`);
  }
  const historyKeys=['previous_receipt_no','previous_receipt_date','previous_payer_name_snapshot','previous_amount_minor',
    'previous_payment_method','previous_notes','previous_status','previous_project_id','previous_contract_id',
    'previous_invoice_id','previous_settlement_id','previous_allocated_amount_minor','cost_entry_id','purchase_order_id',
    'account_id','account_name','balance_date','current_book_minor','needs_review','previous_values_json','updated_values_json',
    'old_daily_rate_minor','new_daily_rate_minor','adjustment_id','work_log_id','after'];
  for(const key of historyKeys){
    audited.add(key);
    if(!await evaluate(`knownFieldLabel(${JSON.stringify(key)})`))missing.add(key);
  }
  fs.writeFileSync(path.join(output,'label-coverage.json'),JSON.stringify({fields:audited.size,missing:[...missing].sort(),untranslated:[...untranslated].sort()},null,2));
  assert.deepEqual([...missing].sort(),[],'All exposed facts must have a deliberate Chinese label');
  assert.deepEqual([...untranslated].sort(),[],'Internal status and type codes must also be Chinese');
  checks.push('实际详情、汇总、只读查询和41个高级预览字段都有中文名称');

  const before=await evaluate("api('qa_state').then(result=>result.digest)");
  const routes=await evaluate('state.boot.navigation.flatMap(([,items])=>items.map(([route])=>route))');
  for(const route of routes){
    await evaluate(`location.hash=${JSON.stringify(route)}`);
    await until(`state.route===${JSON.stringify(route)} && !main.querySelector('.loading')`);
    const english=await evaluate(`[...main.querySelectorAll('th,.metric label,.field-label')].map(node=>node.textContent.trim()).filter(text=>/^[a-z][a-z0-9_]*$/i.test(text))`);
    assert.deepEqual(english,[],route+' has no English field labels');
  }
  checks.push('19个导航页面没有裸露英文字段标签');
  await evaluate("location.hash='customers'");await until("state.route==='customers' && !main.querySelector('.loading')");
  await evaluate("openOperation('customer_business',{id:1})");
  assert(await evaluate("editor.textContent.includes('业务年度') && editor.textContent.includes('年度业务额（已确认收入）') && editor.textContent.includes('年度实际回款') && editor.textContent.includes('当前未回款') && editor.textContent.includes('项目状态')"));
  assert.equal(await evaluate('/yearly_|project_status|current_receivable_minor/.test(editor.textContent)'),false);
  await sleep(250);await screenshot('01_customer_business');
  await evaluate("[...editor.querySelectorAll('h3')].find(node=>node.textContent==='汇总').scrollIntoView({block:'start'})");
  await screenshot('01_customer_yearly_summary');
  checks.push('截图对应客户年度业务弹窗和明细列全部中文');
  await evaluate('closeEditor()');
  for(const module of await evaluate('Object.keys(state.boot.schema)')){
    const id=await evaluate(`api('rows?'+query({module:${JSON.stringify(module)}})).then(result=>result.rows[0]?.id)`);
    if(!id)continue;
    await evaluate(`detailRecord(${JSON.stringify(module)},${JSON.stringify(id)})`);
    const english=await evaluate(`[...editor.querySelectorAll('.detail-cell small')].map(node=>node.textContent).filter(text=>/^[a-z][a-z0-9_]*$/i.test(text) || text.includes('名称待完善'))`);
    assert.deepEqual(english,[],module+' detail labels');
    await evaluate('closeEditor()');
  }
  checks.push('实际原始记录详情中文化，查看不改账本');
  assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),before);
  const displayChecks=await evaluate(`({
    income:display('manual','income_mode'), allocation:display('manual','method'),
    contractStatus:display('active','status','contracts'), customerStatus:display('active','status','customers'),
    roles:display(['customer','supplier'],'roles'), tax:display(1300,'tax_rate_bps'),
    roleEvidence:evidenceHTML({roles:['customer','supplier']}),
    integerMoney:display('900719925474099301','amount_minor'),
    history:display(JSON.stringify({project_status:'进行中',yearly_business_minor:'12345'}),'before_json'),
    preciseHistory:display('{"amount_minor":900719925474099301}','before_json'),
    unknown:fieldLabel('future_internal_field'),
    prototype:fieldLabel('constructor'),
    unchangedText:display('constructor','notes'),
    safe:evidenceHTML({year:'<img src=x onerror=alert(1)>'}),
  })`);
  assert.equal(displayChecks.income,'人工确认收入');assert.equal(displayChecks.allocation,'手工金额分摊');
  assert.equal(displayChecks.contractStatus,'履约中');assert.equal(displayChecks.customerStatus,'有效');
  assert.equal(displayChecks.roles,'客户、供应商');assert.equal(displayChecks.tax,'13.0%');
  assert(!/customer|supplier/.test(displayChecks.roleEvidence));assert(displayChecks.roleEvidence.includes('客户、供应商'));
  assert.equal(displayChecks.integerMoney,'¥9,007,199,254,740,993.01');
  assert(!/yearly_|project_status/.test(displayChecks.history));assert(displayChecks.history.includes('¥123.45'));
  assert(displayChecks.preciseHistory.includes('¥9,007,199,254,740,993.01'));
  assert(!displayChecks.prototype.includes('constructor'));assert.equal(displayChecks.unchangedText,'constructor');
  assert(!displayChecks.unknown.includes('future_internal_field'));assert(!displayChecks.safe.includes('<img'));
  checks.push('收入依据与分摊不混译、角色/税率/修改历史中文化、整数分及安全转义保持');
  const source=await evaluate(`assistantSourceHTML({label:'合成查询来源',summary:{yearly_business_minor:'12345',record_count:2},
    columns:[{key:'project_status',label:'project_status'}],details:[{project_status:'进行中'}]},0)`);
  assert(!/yearly_business_minor|record_count|project_status/.test(source));
  assert(source.includes('年度业务额（已确认收入）') && source.includes('项目状态') && source.includes('¥123.45'));
  checks.push('经营助手的来源汇总和裸英文字段表头也显示中文');
  await evaluate("openOperation('customer_business',{id:1})");
  await evaluate("[...editor.querySelectorAll('h3')].find(node=>node.textContent==='汇总').scrollIntoView({block:'start'})");
  await call('Emulation.setDeviceMetricsOverride',{width:960,height:760,deviceScaleFactor:1,mobile:false});
  await sleep(250);await screenshot('02_customer_960');
  assert(await evaluate('editor.scrollWidth<=editor.clientWidth && document.querySelector(".dialog-foot").getBoundingClientRect().bottom<=innerHeight'));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(250);await screenshot('03_customer_390');
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth && editor.scrollWidth<=editor.clientWidth'));
  checks.push('960px与390px中文长标签不溢出，底部操作可见');
  assert.deepEqual(errors,[]);
  const external=requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin));assert.deepEqual(external,[]);
  const result={passed:checks.length,fields:audited.size,checks,errors,external_requests:external.length};
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}catch(error){await screenshot('failure');throw error;}
finally{try{await call('Browser.close');}catch{}socket.close();browser.unref();}
