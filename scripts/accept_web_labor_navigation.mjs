// Real pointer interactions on a new synthetic ledger, never the production DB.
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
let sequence=0;
const pending=new Map(),errors=[],requests=[],checks=[];
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
async function until(expression){for(let attempt=0;attempt<160;attempt++){if(await evaluate(expression))return;await sleep(60);}throw new Error('Timed out '+expression+' / '+await evaluate('document.querySelector("#toast").textContent'));}
async function screenshot(name){const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));}
async function click(selector){
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'nearest'})`);
  await sleep(200);
  const target=await evaluate(`(()=>{const element=document.querySelector(${JSON.stringify(selector)});const box=element.getBoundingClientRect();const x=box.x+box.width/2,y=box.y+box.height/2;const hit=document.elementFromPoint(x,y);return {x,y,hit:element.contains(hit),disabled:element.disabled,hitElement:hit?.outerHTML.slice(0,400),scroll:scrollY,route:state.route,animations:main.getAnimations().length};})()`);
  if(!target.hit){
    const diagnostic=await evaluate(`(()=>{const element=document.querySelector(${JSON.stringify(selector)});const parents=[];for(let parent=element;parent;parent=parent.parentElement){const css=getComputedStyle(parent);parents.push({tag:parent.tagName,id:parent.id,inert:parent.inert,events:css.pointerEvents,visibility:css.visibility,transform:css.transform,opacity:css.opacity});}return {parents,modal:editor.matches(':modal'),dialogOpen:editor.open,dialog:editor.outerHTML.slice(0,200),elements:document.elementsFromPoint(${target.x},${target.y}).map(element=>element.tagName+'#'+element.id),rect:JSON.stringify(element.getBoundingClientRect())};})()`);
    console.log(JSON.stringify({pointerDiagnostic:diagnostic}));
  }
  assert(target.hit,'Pointer blocked: '+selector+' '+JSON.stringify(target));assert(!target.disabled,'Disabled: '+selector);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:target.x,y:target.y});
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:target.x,y:target.y});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:target.x,y:target.y});
}
async function route(name){await click(`.nav-link[data-route="${name}"]`);await until(`state.route===${JSON.stringify(name)} && main.getAttribute('aria-busy')==='false'`);}
async function openBatch(){await click('#batch-worklogs');await until('editor.open && !!document.querySelector("#batch-worker-search")');}
async function close(){await click('#editor-cancel');await until('!editor.open');}
try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Synthetic writable mode required');
  await evaluate("api('qa_fixture',{name:'create_workers'})");
  const workers=await evaluate(`(async()=>{
    const save=async(module,record)=>{
      const ticket=await api('write_ticket?'+query({kind:'save',module}));
      return api('save',{module,record,form_ticket:ticket.token,confirmed:true});
    };
    const options=await api('options');const ids=options.active_workers.map(worker=>worker.value);
    for(const name of ['合成示例工人丙','合成王正尚','合成章国忠','合成罗运喜','合成袁新亮','示例工人乙']){
      ids.push(await save('workers',{name,daily_rate:'200'}));
    }
    const retired=await save('workers',{name:'合成离职人员',daily_rate:'180',status:'离职'});
    for(let index=0;index<45;index++){
      const date=new Date(Date.UTC(2026,5,index+1)).toISOString().slice(0,10);
      await save('worklogs',{worker_id:ids[0],project_id:1,construction_site:'合成分页工天',work_date:date,work_days:'0.5'});
    }
    await refreshOptions();return {ids,last:ids.at(-1),retired};
  })()`);
  const baseline=await evaluate("api('qa_state').then(value=>value.digest)");
  await route('labor');await openBatch();
  assert.equal(await evaluate('editor.querySelectorAll(".batch-worker").length'),7);
  assert(await evaluate(`!!editor.querySelector('.batch-worker[data-worker-id="${workers.last}"]')`));
  assert.equal(await evaluate(`!!editor.querySelector('.batch-worker[data-worker-id="${workers.retired}"]')`),false);
  assert.match(await evaluate('document.querySelector("#batch-worker-count").textContent'),/在职 7 人.*当前显示 7 人/);
  await screenshot('01_initial_batch');
  const layout=await evaluate(`(()=>{const list=editor.querySelector('.batch-worker-list'),body=document.querySelector('#editor-body'),row=editor.querySelector('.batch-worker[data-worker-id="${workers.last}"]');return {listHeight:list.clientHeight,listContent:list.scrollHeight,bodyHeight:body.clientHeight,bodyContent:body.scrollHeight,lastName:row.textContent,viewport:innerHeight};})()`);
  console.log(JSON.stringify({initialLayout:layout}));
  assert(layout.listContent<=layout.listHeight+1,'Worker list must not have a second scrollbar');
  await click(`.batch-worker[data-worker-id="${workers.last}"] [name="batch-worker"]`);
  assert.match(await evaluate('document.querySelector("#batch-workday-summary").textContent'),/已选 1 人/);
  await screenshot('02_last_worker_without_search');
  await click('#batch-clear');
  await click('#batch-worker-search');await call('Input.insertText',{text:'示例工人乙'});
  assert.equal(await evaluate('[...editor.querySelectorAll(".batch-worker")].filter(row=>!row.hidden).length'),1);
  assert.match(await evaluate('document.querySelector("#batch-worker-count").textContent'),/在职 7 人.*当前显示 1 人/);
  await click(`.batch-worker[data-worker-id="${workers.last}"] [name="batch-worker"]`);
  assert.match(await evaluate('document.querySelector("#batch-workday-summary").textContent'),/已选 1 人/);
  await screenshot('03_search_last_worker');await close();
  checks.push('7名在职候选含最后的示例工人乙，单层滚动、人数提示、真实搜索和勾选可用，离职人员不显示');
  for(const name of ['projects','finance','cost','purchases','assistant','home']){
    console.log('round-trip '+name);
    await route(name);await route('labor');await openBatch();await close();
  }
  checks.push('6个页面往返后真实鼠标可打开和取消批量弹窗');
  await click('#new-record');await until('editor.open && !!form.elements.worker_id');await close();
  await click('#next-page');await until('state.page===1 && main.getAttribute("aria-busy")==="false"');await openBatch();await close();
  await click('#prev-page');await until('state.page===0 && main.getAttribute("aria-busy")==="false"');
  await click('#record-search');await call('Input.insertText',{text:'合成分页工天'});await click('#search-records');
  await until('state.keyword==="合成分页工天" && main.getAttribute("aria-busy")==="false"');await openBatch();await close();
  assert.equal(await evaluate('main.querySelectorAll(".route-content").length'),1);
  assert.equal(await evaluate('main.getAnimations().length'),0,'Persistent main must never be animated');
  checks.push('长台账翻页、搜索刷新后按钮可真实点击，单人登记保持可用，动效内容层不嵌套');
  for(const tab of ['workers','monthly','worklogs']){
    await click(`[data-tab="${tab}"]`);await until(`state.collection===${JSON.stringify(tab)} && main.getAttribute('aria-busy')==='false'`);
  }
  await openBatch();await close();checks.push('工人档案与月度矩阵往返后批量按钮可用');
  for(let index=0;index<5;index++){
    await evaluate("location.hash='projects'");await sleep(5);await evaluate("location.hash='labor'");
    await until('state.route==="labor" && main.getAttribute("aria-busy")==="false" && !!document.querySelector("#batch-worklogs")');
    await openBatch();await close();
  }
  checks.push('5轮快速打断切页后真实按钮仍可用');
  for(const [width,height] of [[1366,768],[960,700],[390,844]]){
    await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
    await openBatch();
    assert.equal(await evaluate('document.querySelector("#editor-body").scrollWidth<=document.querySelector("#editor-body").clientWidth+1'),true);
    assert.equal(await evaluate('document.querySelector(".batch-worker-list").scrollHeight<=document.querySelector(".batch-worker-list").clientHeight+1'),true);
    await click(`.batch-worker[data-worker-id="${workers.last}"] [name="batch-worker"]`);
    assert.match(await evaluate('document.querySelector("#batch-workday-summary").textContent'),/已选 1 人/);
    assert.equal(await evaluate('document.querySelector(".dialog-foot").getBoundingClientRect().bottom<=innerHeight+1'),true);
    await screenshot('04_last_worker_'+width);await close();
  }
  checks.push('1366px、960px、390px窗口只有一层表单滚动，末位工人可真实勾选且操作栏可见');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await until('WorkbenchMotion.reduced');await route('finance');await route('labor');await openBatch();await close();
  assert.equal(await evaluate('main.getAnimations({subtree:true}).length'),0);
  await call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await until('!WorkbenchMotion.reduced');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab'});await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab'});
  await evaluate('WorkbenchMotion.finishRoute(main)');
  assert.equal(await evaluate('main.getAnimations({subtree:true}).length'),0);
  assert.equal(await evaluate('main.querySelectorAll(".route-content").length'),1);
  checks.push('减少动效与键盘操作仍即时可用，同一内容层不重复包裹');
  assert.equal(await evaluate("api('qa_state').then(value=>value.digest)"),baseline);
  assert.deepEqual(errors,[]);
  const external=requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin));assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed:checks.length,checks,layout,errors,external_requests:external.length},null,2));
  console.log(JSON.stringify({passed:checks.length,checks}));
}catch(error){await screenshot('FAILED');throw error;}
finally{try{await call('Browser.close');}catch{}socket.close();}
