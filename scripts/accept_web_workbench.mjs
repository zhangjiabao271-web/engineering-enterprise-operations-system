import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
const origin=process.argv[2];
assert(origin,'请提供合成工作台的本机网址');
const address=new URL(origin);
assert(address.protocol==='http:' && address.hostname==='127.0.0.1','仅验收本机回环网址');
const output=path.resolve(process.argv[3] || 'qa_web_01');
fs.mkdirSync(output,{recursive:true});
const profile=path.resolve(process.argv[4] || path.join(output,'edge-profile'));
const ownBrowser=!process.argv[4];
if(ownBrowser){
 const edge=path.join(process.env['ProgramFiles(x86)'] || 'C:/Program Files (x86)','Microsoft/Edge/Application/msedge.exe');
 spawn(edge,['--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',`--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','--disable-component-update','about:blank'],{windowsHide:true,stdio:'ignore'});
}
const activePort=path.join(profile,'DevToolsActivePort');
for(let i=0;!fs.existsSync(activePort)&&i<100;i++)await new Promise(resolve=>setTimeout(resolve,60));
const port=fs.readFileSync(activePort,'utf8').split('\n')[0].trim();
const pages=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
let counter=0;const pending=new Map();const errors=[];const requests=[];
ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}
 if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);
 if(m.method==='Network.requestWillBeSent')requests.push(m.params.request.url);
});
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++counter;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){
 // These legacy cases assign IDs directly; sync the visible search controls too.
 expression=expression.replaceAll('form.requestSubmit()',"(form.querySelectorAll('select[data-search]').forEach(select=>select.dispatchEvent(new Event('choice-sync'))),form.requestSubmit())");
 const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;
}
async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,60));}throw new Error('Timed out: '+expression+' / '+await evaluate('main.textContent+" / "+document.querySelector("#toast").textContent+" / "+document.querySelector("#form-error").textContent'));}
async function screenshot(name){const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(r.data,'base64'));}
async function selectFile(selector,file){const doc=await call('DOM.getDocument');const selected=await call('DOM.querySelector',{nodeId:doc.root.nodeId,selector});assert(selected.nodeId,selector);await call('DOM.setFileInputFiles',{nodeId:selected.nodeId,files:[file]});}
async function confirmOperation(){await until("!!form.elements.namedItem('operation-confirmed')");assert.equal(await evaluate("form.elements.namedItem('operation-confirmed').checked"),false);await evaluate("form.elements.namedItem('operation-confirmed').checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');}
async function route(name){await evaluate(`location.hash=${JSON.stringify(name)}`);await until(`state.route===${JSON.stringify(name)} && !main.querySelector('.loading')`);assert.equal(await evaluate(`main.textContent.includes('读取未完成')`),false,name);}
try {
await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');
await screenshot('01_home');
const navigation=await evaluate('state.boot.navigation.flatMap(group=>group[1].map(item=>item[0]))');
for(const name of navigation){await route(name);if(['contracts','cashflow','purchases','funds'].includes(name))await screenshot('route_'+name);}
const tabs=[];
for(const parent of ['contracts','finance','funds','cost','labor']){
 await route(parent);
 const modules=await evaluate(`[...main.querySelectorAll('[data-tab]')].map(b=>b.dataset.tab)`);
 for(const module of modules){
  await evaluate(`main.querySelector('[data-tab="${module}"]').click()`);
  await until(`!!main.querySelector('[data-tab="${module}"].active')`);
  assert.equal(await evaluate(`main.textContent.includes('读取未完成')`),false,module);
  tabs.push(module);
 }
}
const readOnly=await evaluate('state.boot.read_only');
assert(readOnly || await evaluate('state.boot.demo'),'可写验收只允许合成演示模式');
if (!readOnly) {
await route('projects');await evaluate('document.querySelector("#new-record").click()');await until('editor.open');await screenshot('02_project_form');await evaluate('document.querySelector("#editor-cancel").click()');await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
await evaluate(`document.querySelector('#new-record').click()`);await until('editor.open');
const projectName='合成浏览器录入-'+Date.now();
await evaluate(`form.elements.name.value=${JSON.stringify(projectName)};form.elements.confirmed.checked=true;form.requestSubmit()`);
await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');await until('!main.querySelector(".loading")');
assert.equal(await evaluate(`main.textContent.includes(${JSON.stringify(projectName)})`),true,'project saved through visible form');
await route('cashflow');
await evaluate(`document.querySelector('#flow-contract').value='1';document.querySelector('#flow-project').value='1';document.querySelector('#load-flow').click()`);
await until('document.querySelector("#upload-source")');await screenshot('03_cashflow_scope');
async function manual(name,text,reason=''){
 await evaluate(`document.querySelector('#manual-source').click()`);await until('editor.open');
 await evaluate(`form.elements.name.value=${JSON.stringify(name)};form.elements.locator.value='合成第8条 / 第4页';form.elements.text.value=${JSON.stringify(text)};form.elements.change_reason.value=${JSON.stringify(reason)};form.requestSubmit()`);
 await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');await until('document.querySelector("#manual-source")');
}
const sourceName='合成浏览器来源-'+Date.now();
await manual(sourceName,'付款 浏览器验收节点；金额：123.45元；日期：2026-12-20；条件：验收完成');
assert.equal(await evaluate('!!state.analysis.stale_reason'),true,'prior draft invalidated');
await manual(sourceName,'付款 浏览器验收节点；金额：123.45元；日期：2026-12-20；条件：验收完成');
await evaluate(`document.querySelector('#analyze-flow').click()`);
await until('state.analysis && !state.analysis.stale_reason');
await evaluate(`document.querySelector('#confirm-flow').click()`);await until('editor.open');
assert.equal(await evaluate(`form.querySelectorAll('[name^="row-"]:checked').length`),0,'no preselected confirmation');
await screenshot('06_confirmation');await evaluate(`document.querySelector('#editor-cancel').click()`);await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
assert.equal(await evaluate('state.analysis.status'), 'draft');
await evaluate(`document.querySelector('#confirm-flow').click()`);await until('editor.open');
await evaluate(`const key=state.analysis.payload.rows.find(r=>r.title==='浏览器验收节点').key;form.elements.namedItem('row-'+key).checked=true;form.elements.reason.value='合成浏览器验收核对来源';form.elements.acknowledged.checked=true;form.requestSubmit()`);
await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');await until('state.analysis.status==="confirmed"');await screenshot('07_confirmed');
assert.equal(await evaluate('state.analysis.plan_ids.length'),1);
await manual(sourceName,'付款 浏览器验收节点；金额：135元；日期：2026-12-20；条件：验收完成','合成版本修订');
assert.equal(await evaluate('!!state.analysis.stale_reason'),true);
await evaluate(`document.querySelector('#analyze-flow').click()`);await until('state.analysis.status==="draft" && !state.analysis.stale_reason');
await evaluate(`document.querySelector('#cancel-flow').click()`);await until('editor.open');await evaluate('form.requestSubmit()');await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');await until('state.analysis.status==="cancelled"');
await manual(sourceName+'冲突摘录','付款 浏览器验收节点；金额：140元；日期：2026-12-20；条件：验收完成');
await evaluate(`document.querySelector('#analyze-flow').click()`);await until('state.analysis.status==="draft" && !state.analysis.stale_reason');
assert.equal(await evaluate(`state.analysis.payload.rows.find(r=>r.title==='浏览器验收节点').conflict`),true,'both conflicting variants preserved');
await screenshot('08_conflict');
await evaluate(`document.querySelector('#confirm-flow').click()`);await until('editor.open');
assert.equal(await evaluate(`form.querySelector('[name="row-'+state.analysis.payload.rows.find(r=>r.title==='浏览器验收节点').key+'"]')===null`),true,'conflicting node excluded from confirmation');
await evaluate(`document.querySelector('#editor-cancel').click()`);await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');

await route('cost');await evaluate("main.querySelector('[data-tab=costs]').click()");await until("main.querySelector('[data-tab=costs].active')");
const costVersion=await evaluate("api('record?module=costs&id=1').then(row=>row._version)");
await evaluate("main.querySelector('[data-operation=cost_allocate]').closest('details').open=true;main.querySelector('[data-operation=cost_allocate]').click()");await until('editor.open && !!form.elements.method');
await evaluate("form.elements.method.value='manual';editor.querySelector('.add-operation-row').click();const row=editor.querySelector('.operation-row');row.querySelector('[name=project_id]').value='1';row.querySelector('[name=amount]').value='2000.00';form.elements.reason.value='合成浏览器分摊预览';form.requestSubmit()");
await until("!!form.elements.namedItem('operation-confirmed')");assert.equal(await evaluate("form.elements.namedItem('operation-confirmed').checked"),false);await screenshot('09_cost_allocation_preview');
await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');assert.equal(await evaluate("api('record?module=costs&id=1').then(row=>row._version)"),costVersion,'cancel allocation preserves facts');
const otherProject=await evaluate(`api('rows?module=projects').then(result=>result.rows.find(row=>row.name===${JSON.stringify(projectName)}).id)`);
await evaluate("main.querySelector('[data-operation=cost_allocate]').click()");await until('editor.open && !!form.elements.method');
await evaluate(`form.elements.method.value='manual';editor.querySelector('.add-operation-row').click();editor.querySelector('.add-operation-row').click();const allocationRows=[...editor.querySelectorAll('.operation-row')];allocationRows[0].querySelector('[name=project_id]').value='1';allocationRows[0].querySelector('[name=amount]').value='1000.01';allocationRows[1].querySelector('[name=project_id]').value='${otherProject}';allocationRows[1].querySelector('[name=amount]').value='999.99';form.elements.reason.value='合成浏览器手工分摊确认';form.requestSubmit()`);await confirmOperation();
assert.deepEqual(await evaluate("api('operation_read?name=cost_allocations&id=1').then(result=>result.lines.map(row=>row.amount_minor).sort())"),['100001','99999']);
await evaluate("main.querySelector('[data-operation=cost_allocate]').click()");await until('editor.open && !!form.elements.method');
await evaluate(`form.elements.method.value='equal';for(const option of form.elements.project_ids.options)option.selected=['1','${otherProject}'].includes(option.value);editor.querySelectorAll('.operation-row').forEach(row=>row.remove());form.elements.reason.value='合成浏览器均摊确认';form.requestSubmit()`);await confirmOperation();
assert.deepEqual(await evaluate("api('operation_read?name=cost_allocations&id=1').then(result=>result.lines.map(row=>row.amount_minor))"),['100000','100000']);await screenshot('15_cost_allocation_confirmed');
await route('funds');await evaluate("main.querySelector('[data-operation=fund_reconcile]').click()");await until('editor.open && !!form.elements.actual_amount');
await evaluate("form.elements.account_id.value='1';form.elements.balance_date.value='2026-10-03';form.elements.actual_amount.value='249999.99';form.elements.reason.value='合成浏览器核对差异';form.requestSubmit()");await until("!!form.elements.namedItem('operation-confirmed')");
assert.equal(await evaluate("form.elements.namedItem('operation-confirmed').checked"),false);await screenshot('10_balance_review');await evaluate("form.elements.namedItem('operation-confirmed').checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
assert.equal(await evaluate("api('operation_read?name=reconciliations').then(rows=>rows.length)"),1);
assert.equal(await evaluate("api('record?module=accounts&id=1').then(row=>row.balance_minor)"),'25000000','reconcile never adjusts actual cash');
await route('labor');await evaluate("main.querySelector('[data-tab=workers]').click()");await until("main.querySelector('[data-tab=workers].active')");
await evaluate("main.querySelector('[data-operation=labor_adjust]').closest('details').open=true;main.querySelector('[data-operation=labor_adjust]').click()");await until('editor.open && !!form.elements.new_daily_rate');
await evaluate("form.elements.worker_id.value='1';form.elements.new_daily_rate.value='410.01';form.elements.effective_from.value=state.boot.schema.worklogs.fields.find(field=>field.name==='work_date').default;form.elements.scope_mode.value='through_today';form.elements.reason.value='合成浏览器调薪预览';form.requestSubmit()");await until("!!form.elements.namedItem('operation-confirmed')");await screenshot('11_wage_review');await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
await evaluate("main.querySelector('[data-tab=worklogs]').click()");await until("main.querySelector('[data-tab=worklogs].active')");await evaluate("document.querySelector('#new-record').click()");await until('editor.open && !!form.elements.work_days');
await evaluate("form.elements.worker_id.value='1';form.elements.project_id.value='1';form.elements.construction_site.value='合成浏览器调薪核对地点';form.elements.work_days.value='0.5';form.elements.confirmed.checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
const wageLog=await evaluate("api('rows?module=worklogs').then(result=>result.rows.find(row=>row.construction_site==='合成浏览器调薪核对地点').id)");
await evaluate("main.querySelector('[data-operation=labor_lock][data-id=\"1\"]').closest('details').open=true;main.querySelector('[data-operation=labor_lock][data-id=\"1\"]').click()");await until('editor.open && !!form.elements.locked');await evaluate(`for(const option of form.elements.ids.options)option.selected=option.value==='${wageLog}';form.elements.locked.checked=true;form.elements.reason.value='合成浏览器工资锁定';form.requestSubmit()`);await confirmOperation();
await evaluate("document.querySelector('#new-record').click()");await until('editor.open && !!form.elements.work_days');await evaluate("form.elements.worker_id.value='1';form.elements.project_id.value='1';form.elements.construction_site.value='合成浏览器调薪未锁记录';form.elements.work_days.value='0.5';form.elements.confirmed.checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
const unlockedWageLog=await evaluate("api('rows?module=worklogs').then(result=>result.rows.find(row=>row.construction_site==='合成浏览器调薪未锁记录').id)");
await evaluate("main.querySelector('[data-tab=workers]').click()");await until("main.querySelector('[data-tab=workers].active')");await evaluate("main.querySelector('[data-operation=labor_adjust]').closest('details').open=true;main.querySelector('[data-operation=labor_adjust]').click()");await until('editor.open && !!form.elements.new_daily_rate');
await evaluate("form.elements.worker_id.value='1';form.elements.new_daily_rate.value='410.01';form.elements.effective_from.value=state.boot.schema.worklogs.fields.find(field=>field.name==='work_date').default;form.elements.scope_mode.value='through_today';form.elements.reason.value='合成浏览器调薪实际回算';form.requestSubmit()");await until("!!form.elements.namedItem('operation-confirmed')");await screenshot('16_wage_confirm_review');await confirmOperation();
assert.equal(await evaluate(`api('record?module=worklogs&id=${unlockedWageLog}').then(row=>row.amount_minor)`),'20501');assert.equal(await evaluate("api('record?module=worklogs&id=1').then(row=>row.amount_minor)"),'40000');assert.equal(await evaluate(`api('record?module=worklogs&id=${wageLog}').then(row=>row.rate_locked)`),1);assert.equal(await evaluate(`api('record?module=worklogs&id=${wageLog}').then(row=>row.amount_minor)`),'20000');
await evaluate("main.querySelector('[data-tab=monthly]').click()");await until("!!main.querySelector('#monthly-matrix')");await screenshot('17_monthly_matrix');await evaluate("main.querySelector('[data-matrix-worker]').click()");await until('editor.open && document.querySelector("#dialog-title").textContent.includes("逐日出勤")');assert.equal(await evaluate("document.querySelector('#editor-body').textContent.includes('工资')"),false);await screenshot('18_monthly_drilldown');await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open');
await route('assistant');await evaluate("document.querySelector('#chat-question').value='哪些发票还有余额';document.querySelector('#chat-form').requestSubmit()");await until("document.querySelector('.chat-assistant .chat-answer') && !assistantView.snapshot.busy");await screenshot('12_local_query');
await route('tools');
const exportType=await evaluate("fetch('/api/excel_export?module=operating').then(r=>({status:r.status,type:r.headers.get('content-type')}))");assert.equal(exportType.status,200);assert(exportType.type.includes('spreadsheetml'));
const excelFile=path.join(output,'synthetic-browser-import.xlsx');
const {spawnSync}=await import('node:child_process');
const generated=spawnSync('D:/work/supply chain/.venv/Scripts/python.exe',['-B','-c',"from openpyxl import Workbook;import sys;w=Workbook();s=w.active;s.append(['ID','工厂名称','产品范围','联系人','价格水平','交期','质量情况','是否做出口','备注','默认税率（%）']);s.append([None,'合成浏览器批量供应商','工具','合成联系人','中','一般','良','否','合成测试',13]);w.save(sys.argv[1])",excelFile],{windowsHide:true,encoding:'utf8'});assert.equal(generated.status,0,generated.stderr);
async function selectExcel(){const doc=await call('DOM.getDocument');const selected=await call('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#excel-file'});await call('DOM.setFileInputFiles',{nodeId:selected.nodeId,files:[excelFile]});}
await selectExcel();await evaluate("document.querySelector('#excel-preview').click()");await until("editor.open && !!form.elements.namedItem('excel-confirmed')");assert.equal(await evaluate("form.elements.namedItem('excel-confirmed').checked"),false);await screenshot('13_excel_review');
await evaluate("form.elements.namedItem('excel-confirmed').checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');await until("!!document.querySelector('#excel-file')");
assert.equal(await evaluate("api('rows?module=suppliers').then(result=>result.rows.filter(row=>row.name==='合成浏览器批量供应商').length)"),1);
await selectExcel();await evaluate("document.querySelector('#excel-preview').click()");await until("document.querySelector('#toast').textContent.includes('已存在')");assert.equal(await evaluate('editor.open'),false);
await route('entities');await screenshot('14_entities');
await route('compare');await until("!!main.querySelector('.quote-chart svg')");assert.equal(await evaluate("main.textContent.includes('组内最低含税价') && main.textContent.includes('质量')"),true);await screenshot('19_quote_chart');
await route('tools');const csvFile=path.join(output,'synthetic-browser-full.csv');fs.writeFileSync(csvFile,'\ufefflegal_name,credit_limit\r\n合成浏览器CSV甲,99999999999.93\r\n合成浏览器CSV乙,1.01\r\n','utf8');
await evaluate("document.querySelector('#import-module').value='customers'");await selectFile('#import-file',csvFile);await evaluate("document.querySelector('#tools-import').click()");await until("editor.open && !!form.elements.namedItem('csv-confirmed')");assert.equal(await evaluate("form.elements.namedItem('csv-confirmed').checked"),false);await screenshot('20_csv_review');await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open');assert.equal(await evaluate("api('rows?module=customers').then(result=>result.rows.filter(row=>row.legal_name==='合成浏览器CSV甲').length)"),0);
await selectFile('#import-file',csvFile);await evaluate("document.querySelector('#tools-import').click()");await until("editor.open && !!form.elements.namedItem('csv-confirmed')");await evaluate("form.elements.namedItem('csv-confirmed').checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');assert.equal(await evaluate("api('rows?module=customers').then(result=>result.rows.filter(row=>row.legal_name==='合成浏览器CSV甲').length)"),1);
await evaluate("document.querySelector('#import-module').value='customers'");await selectFile('#import-file',csvFile);await evaluate("document.querySelector('#tools-import').click()");await until("document.querySelector('#toast').textContent.includes('已存在')");assert.equal(await evaluate('editor.open'),false);
const photoFile=path.join(output,'synthetic-site.png');const pdfFile=path.join(output,'synthetic-browser-invoice.pdf');
const artifacts=spawnSync('D:/work/supply chain/.venv/Scripts/python.exe',['-B','-c',"from PIL import Image;from tests.test_web_workbench_extended import synthetic_invoice_pdf;import sys;Image.new('RGB',(320,200),'#0066cc').save(sys.argv[1]);synthetic_invoice_pdf(sys.argv[2],number='10203040506070809010',buyer='合成建设单位')",photoFile,pdfFile],{cwd:'D:/work/supply chain',windowsHide:true,encoding:'utf8'});assert.equal(artifacts.status,0,artifacts.stderr);
await route('construction');await evaluate("main.querySelector('[data-action=photos]').click()");await until('editor.open && !!form.elements.photos');await selectFile('[name=photos]',photoFile);await evaluate("form.elements.notes.value='合成照片分类验收';form.elements.namedItem('photo-confirmed').checked=true;form.requestSubmit()");await until("editor.open && !!editor.querySelector('[data-photo-category]') && !document.querySelector('#editor-save').disabled");await screenshot('21_photo_manager');await evaluate("editor.querySelector('[data-photo-category]').click()");await until('!!form.elements.category && !form.elements.photos');await evaluate("form.elements.category.value='验收照片';form.requestSubmit()");await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成现场照片改验收分类';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!editor.querySelector('[data-photo-evidence]') && !document.querySelector('#editor-save').disabled");assert.equal(await evaluate("api('photos?id=1').then(rows=>rows[0].photo_type)"),'验收照片');
await evaluate("editor.querySelector('[data-photo-evidence]').click()");await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成照片保留移出';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!editor.querySelector('[data-photo-state=attachment_restore]') && !document.querySelector('#editor-save').disabled");assert.equal(await evaluate("api('photos?id=1').then(rows=>rows.length)"),1);await screenshot('22_photo_retained_history');await evaluate("editor.querySelector('[data-photo-state=attachment_restore]').click()");await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成照片恢复核对';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!editor.querySelector('[data-photo-state=attachment_void]') && !document.querySelector('#editor-save').disabled");await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open');
await evaluate("main.querySelector('[data-action=attachments]').click()");await until('editor.open && !!form.elements.attachment');await selectFile('[name=attachment]',csvFile);await evaluate("form.elements.category.value='合成材料清单';form.elements.description.value='合成普通附件恢复验收';form.requestSubmit()");await until("!!editor.querySelector('[data-evidence-action=attachment_void]') && !document.querySelector('#editor-save').disabled");
const ordinaryAttachment=await evaluate("api('attachments?entity=construction&id=1').then(rows=>rows.find(row=>row.category==='合成材料清单').id)");
await evaluate(`editor.querySelector('[data-evidence-id="${ordinaryAttachment}"]').click()`);await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成普通附件移出';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!editor.querySelector('[data-evidence-action=attachment_restore]') && !document.querySelector('#editor-save').disabled");
await evaluate(`editor.querySelector('[data-evidence-id="${ordinaryAttachment}"]').click()`);await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成普通附件恢复';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!form.elements.attachment && !document.querySelector('#editor-save').disabled");assert.equal(await evaluate(`api('attachments?entity=construction&id=1').then(rows=>rows.find(row=>row.id===${ordinaryAttachment}).file_exists)`),true);await screenshot('23_attachment_restored');await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open');
const pdfFixture=await evaluate(`(async()=>{const saveFixture=async(module,record)=>{const ticket=await api('write_ticket?kind=save&module='+module);return api('save',{module,record,form_ticket:ticket.token,confirmed:true});};const project=await saveFixture('projects',{name:'合成浏览器PDF项目',project_code:'SYNTH-PDF-BROWSER',customer_name:'合成建设单位'});const contract=await saveFixture('contracts',{name:'合成浏览器PDF合同',contract_no:'SYNTH-PDF-HT',amount:'113',customer_name:'合成建设单位',sign_date:'2026-10-03'});await saveFixture('allocations',{project_id:project,contract_id:contract,amount:'113'});const settlement=await saveFixture('settlements',{project_id:project,contract_id:contract,amount:'113',basis:'合成验收',settlement_date:'2026-10-03'});await refreshOptions();return {project,contract,settlement};})()`);
await route('finance');await evaluate("main.querySelector('[data-tab=invoices]').click()");await until("!!document.querySelector('#invoice-pdf-import')");await evaluate("document.querySelector('#invoice-pdf-import').click()");await until('editor.open && !!form.elements.pdf');await selectFile('[name=pdf]',pdfFile);await evaluate('form.requestSubmit()');await until("!!form.elements.namedItem('pdf-confirmed')");assert.equal(await evaluate('form.elements.amount.value'),'113.00');assert.equal(await evaluate("form.elements.namedItem('pdf-confirmed').checked"),false);await screenshot('24_local_pdf_review');await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open');assert.equal(await evaluate("api('rows?module=invoices').then(result=>result.rows.filter(row=>row.invoice_no==='10203040506070809010').length)"),0);
await evaluate("document.querySelector('#invoice-pdf-import').click()");await until('!!form.elements.pdf');await selectFile('[name=pdf]',pdfFile);await evaluate('form.requestSubmit()');await until("!!form.elements.namedItem('pdf-confirmed')");await evaluate(`form.elements.project_id.value='${pdfFixture.project}';form.elements.project_id.dispatchEvent(new Event('change'));`);await until(`!!form.elements.contract_id.querySelector('option[value="${pdfFixture.contract}"]')`);await evaluate(`form.elements.contract_id.value='${pdfFixture.contract}';form.elements.settlement_id.value='${pdfFixture.settlement}';form.elements.namedItem('pdf-confirmed').checked=true;form.requestSubmit()`);await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');assert.equal(await evaluate("api('rows?module=invoices').then(result=>result.rows.find(row=>row.invoice_no==='10203040506070809010').amount_minor)"),'11300');await screenshot('25_local_pdf_saved');
} else {
 assert.equal(await evaluate('document.querySelector("#new-record")===null'),true);
 const status=await evaluate(`fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':state.boot.csrf},body:JSON.stringify({module:'projects',record:{name:'禁止写入合成验证'},confirmed:true})}).then(r=>r.status)`);
 assert.equal(status,403);
 await screenshot('readonly');
}
await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
await call('Page.reload',{ignoreCache:true});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');
assert.equal(await evaluate('document.querySelector("#reduced-motion").checked'),true);
await screenshot('04_reduced_motion');
await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await route('contracts');await screenshot('05_mobile');
assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'mobile page overflow');
assert.deepEqual(errors,[],'browser exceptions');
assert.deepEqual(requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin)),[],'external network');
fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({readOnly,navigation,tabs,errors,requests,checks:readOnly ? ['all navigation routes','collection tabs','no create button','HTTP write rejected','reduced motion','mobile overflow','no external requests'] : ['all navigation routes','all collection tabs','allocation preview cancellation','manual allocation confirmed with exact cents','equal allocation confirmed','payroll lock confirmed','wage adjustment confirmed with locked row skipped and half day 205.01','monthly matrix drilldown without wages','quote source fields and grouped chart','CSV full batch cancel-confirm-duplicate','photo classification and retained remove-restore','ordinary attachment retained remove-restore','real local text PDF cancel-confirm with source preserved','balance review confirmation without cash adjustment','wage review cancellation','local operating query','Excel export','Excel preview confirmation and duplicate rejection','operating entity page','project visible-form save','cancel form','duplicate source import','old analysis stale','explicit node confirmation','source version update','conflicting variants excluded','cancel draft','contract/project scope','reduced motion','mobile overflow','no external requests']},null,2));
console.log('Browser checks passed:',navigation.length,'routes; evidence:',output);
await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
} finally {if(ownBrowser){try{await call('Browser.close');}catch{}}ws.close();}
