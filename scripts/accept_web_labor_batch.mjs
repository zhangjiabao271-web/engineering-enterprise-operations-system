// Visible controls against an isolated synthetic ledger; refuses production mode.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin=process.argv[2],output=path.resolve(process.argv[3]);
assert.equal(new URL(origin).hostname,'127.0.0.1');
assert(!fs.existsSync(output),'Use a new acceptance directory');
fs.mkdirSync(output,{recursive:true});
const profile=path.join(output,'edge-profile');
spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
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
let sequence=0,lastWrite;
const pending=new Map(),errors=[],requests=[],checks=[];
socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id){const waiter=pending.get(message.id);pending.delete(message.id);if(waiter){clearTimeout(waiter.timeout);message.error?waiter.reject(message.error):waiter.resolve(message.result);}}
  if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
  if(message.method==='Network.requestWillBeSent'){
    const request=message.params.request;requests.push(request.url);
    if(request.url.endsWith('/api/operation_apply')&&request.postData){const body=JSON.parse(request.postData);if(body.confirmed===true)lastWrite=body;}
  }
});
function call(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence,timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Browser timeout: '+method));},20000);
  pending.set(id,{resolve,reject,timeout});socket.send(JSON.stringify({id,method,params}));
});}
async function evaluate(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value;}
async function until(expression){for(let attempt=0;attempt<160;attempt++){if(await evaluate(expression))return;await sleep(60);}throw new Error('Timed out '+expression+' / '+await evaluate('document.querySelector("#form-error").textContent'));}
async function screenshot(name){const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));}
const ledger=()=>evaluate("api('qa_state').then(value=>value.digest)");
const close=async()=>{await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');};
async function openBatch(){await evaluate("document.querySelector('#batch-worklogs').click()");await until('editor.open && !!document.querySelector("#batch-worker-search")');}
async function fillBatch(workerIds){await evaluate(`(()=>{
  form.elements.project_id.value='1';form.elements.project_id.dispatchEvent(new Event('choice-sync'));
  form.elements.work_date.value='2026-10-06';form.elements.construction_site.value='合成批量验收';form.elements.work_type.value='安装';
  for(const [index,id] of ${JSON.stringify(workerIds)}.entries()){
    const row=editor.querySelector('.batch-worker[data-worker-id="'+id+'"]');row.querySelector('[name=batch-worker]').click();
    row.querySelector('[data-workday="'+(index?'0.5':'1')+'"]').click();if(index)row.querySelector('[name=is_overtime]').click();
  }
})()`);}
try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Synthetic writable mode required');
  await evaluate("api('qa_fixture',{name:'inspection'}).then(()=>refreshOptions())");
  const ids=await evaluate(`(async()=>{
    const save=async(module,record,id)=>{
      const previous=id?await api('record?'+query({module,id})):{};
      const ticket=await api('write_ticket?'+query({kind:'save',module,id,expected:previous._version}));
      return api('save',{module,id,expected:previous._version,record,form_ticket:ticket.token,confirmed:true});
    };
    const a=await save('workers',{name:'合成批量 · 甲','daily_rate':'200'});
    const b=await save('workers',{name:'合成批量 · 乙','daily_rate':'300.01'});
    const retired=await save('workers',{name:'合成离职 · 历史','daily_rate':'180'});
    const log=await save('worklogs',{worker_id:retired,project_id:1,construction_site:'合成历史地点',work_date:'2026-09-15',work_days:'0.5'});
    await save('workers',{status:'离职'},retired);
    const otherRetired=await save('workers',{name:'合成离职 · 无记录','daily_rate':'180','status':'离职'});
    const completed=await save('projects',{name:'合成已完工工程','status':'已完工'});
    await refreshOptions();location.hash='labor';return {a,b,retired,otherRetired,log,completed};
  })()`);
  await until('state.route==="labor" && !!document.querySelector("#batch-worklogs")');
  const before=await ledger();
  assert.equal(await evaluate('main.querySelectorAll("[data-operation=labor_batch]").length'),0);
  await evaluate("document.querySelector('#new-record').click()");await until('editor.open && !!form.elements.worker_id');
  const single=await evaluate('[...form.elements.worker_id.options].map(item=>item.value)');
  assert(single.includes(String(ids.a))&&single.includes(String(ids.b)));
  assert(!single.includes(String(ids.retired))&&!single.includes(String(ids.otherRetired)));
  assert.equal(await evaluate(`!!form.elements.project_id.querySelector('option[value="${ids.completed}"]')`),false);
  await close();assert.equal(await ledger(),before);checks.push('单人新增仅在职；已完工项目不显示');

  await evaluate(`main.querySelector('[data-action=edit][data-id="${ids.log}"]').click()`);await until('editor.open && !!form.elements.worker_id');
  assert.equal(await evaluate(`form.elements.worker_id.value==='${ids.retired}'`),true);
  assert.equal(await evaluate(`!!form.elements.worker_id.querySelector('option[value="${ids.otherRetired}"]')`),false);
  await close();assert.equal(await ledger(),before);checks.push('历史离职工天可查看编辑，只补原工人');

  await openBatch();assert.equal(await evaluate('document.querySelectorAll("[name=batch-worker]:checked").length'),0);
  assert.equal(await evaluate(`!!editor.querySelector('.batch-worker[data-worker-id="${ids.retired}"]')`),false);
  await evaluate("document.querySelector('#batch-select-visible').click()");assert.equal(await evaluate('document.querySelectorAll("[name=batch-worker]:checked").length'),3);
  await evaluate("document.querySelector('#batch-clear').click()");assert.equal(await evaluate('document.querySelectorAll("[name=batch-worker]:checked").length'),0);
  for(const name of ['甲','乙'])await evaluate(`document.querySelector('#batch-worker-search').value=${JSON.stringify(name)};document.querySelector('#batch-worker-search').dispatchEvent(new Event('input'));document.querySelector('#batch-select-visible').click()`);
  assert.equal(await evaluate('document.querySelectorAll("[name=batch-worker]:checked").length'),2);
  await evaluate(`editor.querySelector('.batch-worker[data-worker-id="${ids.b}"] [data-workday="0.5"]').click()`);
  assert.match(await evaluate('document.querySelector("#batch-workday-summary").textContent'),/2 人.*1.5 工/);
  await close();assert.equal(await ledger(),before);checks.push('姓名搜索、全选当前列表、取消全部，隐藏已选不丢失');

  await openBatch();await fillBatch([ids.a,ids.b]);await evaluate("document.querySelector('#editor-body').scrollTop=document.querySelector('#editor-body').scrollHeight");await screenshot('01_batch_workers');
  await evaluate('form.requestSubmit()');await until('!!form.elements.namedItem("batch-confirmed")');
  assert.equal(await evaluate('form.elements.namedItem("batch-confirmed").checked'),false);
  assert.match(await evaluate('document.querySelector("#editor-body").textContent'),/1.5 工/);
  await screenshot('02_batch_review');assert.equal(await ledger(),before);await close();assert.equal(await ledger(),before);
  checks.push('逐人预览清楚，确认默认不勾选，取消不落库');

  await openBatch();await fillBatch([ids.a,ids.b]);await evaluate('form.requestSubmit()');await until('!!form.elements.namedItem("batch-confirmed")');
  await evaluate("document.querySelector('#batch-workday-back').click()");await until('!!document.querySelector("#batch-worker-search")');
  const restored=await evaluate(`({project:form.elements.project_id.value,date:form.elements.work_date.value,site:form.elements.construction_site.value,type:form.elements.work_type.value,
    workers:[...editor.querySelectorAll('.batch-worker')].filter(row=>row.querySelector('[name=batch-worker]').checked).map(row=>[Number(row.dataset.workerId),row.querySelector('[name=work_days]').value,row.querySelector('[name=is_overtime]').checked]).sort((a,b)=>a[0]-b[0])})`);
  assert.deepEqual(restored,{project:'1',date:'2026-10-06',site:'合成批量验收',type:'安装',workers:[[ids.a,'1',false],[ids.b,'0.5',true]]});
  assert.equal(await ledger(),before);checks.push('预览返回修改保留项目日期地点、逐人工天和加班，旧预览不写账');
  await evaluate('form.requestSubmit()');await until('!!form.elements.namedItem("batch-confirmed")');
  lastWrite=null;await evaluate('form.elements.namedItem("batch-confirmed").checked=true;form.requestSubmit()');await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
  const rows=await evaluate("api('rows?module=worklogs').then(data=>data.rows.filter(row=>row.construction_site==='合成批量验收').sort((a,b)=>a.worker_id-b.worker_id).map(row=>[row.worker_id,row.work_days,row.amount_minor,row.is_overtime]))");
  assert.deepEqual(rows,[[ids.a,1,'20000',0],[ids.b,0.5,'15001',1]]);
  const after=await ledger();assert(lastWrite);await evaluate(`api('operation_apply',${JSON.stringify(lastWrite)})`);assert.equal(await ledger(),after);
  checks.push('1工/0.5工和加班保存准确，人工成本350.01元，重复确认不重复记');

  await openBatch();await fillBatch([ids.a,ids.b]);await evaluate('form.requestSubmit()');await until('!document.querySelector("#form-error").hidden');
  assert.match(await evaluate('document.querySelector("#form-error").textContent'),/不能超过 1/);
  assert.equal(await ledger(),after);assert.equal(await evaluate('!!document.querySelector("#batch-worker-search")'),true);
  checks.push('同人同日超限预览拒绝，保留表单且整批不写');
  for(const width of [960,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
    await evaluate("document.querySelector('#editor-body').scrollTop=document.querySelector('#editor-body').scrollHeight");
    assert.equal(await evaluate('document.querySelector("#editor-body").scrollWidth<=document.querySelector("#editor-body").clientWidth+1'),true);
    assert.equal(await evaluate('document.querySelector(".dialog-foot").getBoundingClientRect().bottom<=innerHeight+1'),true);
    await screenshot('03_batch_'+width);
  }
  await close();checks.push('960px与390px不横向溢出，底部操作可见');
  assert.deepEqual(errors,[]);const external=requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin));assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed:checks.length,checks,rows,errors,external_requests:external.length},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,rows,external_requests:0}));
}catch(error){await screenshot('FAILED');throw error;}
finally{try{await call('Browser.close');}catch{}socket.close();}
