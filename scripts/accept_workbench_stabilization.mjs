// Regression through the real local UI. Refuses non-synthetic ledgers.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin=process.argv[2], output=path.resolve(process.argv[3]), pdfFile=path.resolve(process.argv[4]);
const root=path.resolve(import.meta.dirname,'..');
assert.equal(new URL(origin).hostname,'127.0.0.1');
assert(output.startsWith(path.join(root,'qa')+path.sep));
assert(pdfFile.startsWith(path.join(root,'qa')+path.sep));
assert(!fs.existsSync(output),'Use a fresh acceptance directory');
fs.mkdirSync(output,{recursive:true});
const profile=path.join(output,'edge-profile');
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
  '--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','--disable-component-update','about:blank',
],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const portFile=path.join(profile,'DevToolsActivePort');
for(let index=0;!fs.existsSync(portFile)&&index<100;index++)await sleep(60);
const port=fs.readFileSync(portFile,'utf8').split('\n')[0].trim();
const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
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
async function until(expression){for(let index=0;index<200;index++){if(await evaluate(expression))return;await sleep(60);}throw new Error('Timed out: '+expression);}
async function screenshot(name){const result=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));}
async function click(selector){
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'nearest'})`);
  await sleep(120);
  const point=await evaluate(`(()=>{const item=document.querySelector(${JSON.stringify(selector)}),box=item.getBoundingClientRect(),x=box.x+box.width/2,y=box.y+box.height/2;return {x,y,hit:item.contains(document.elementFromPoint(x,y))};})()`);
  assert(point.hit,'Pointer cannot reach '+selector);
  for(const type of ['mouseMoved','mousePressed','mouseReleased'])await call('Input.dispatchMouseEvent',{type,button:type==='mouseMoved'?'none':'left',clickCount:type==='mouseMoved'?0:1,x:point.x,y:point.y});
}
async function route(hash){await evaluate(`location.hash=${JSON.stringify(hash)}`);await until(`state.route===${JSON.stringify(hash.split('?')[0])} && main.getAttribute('aria-busy')==='false'`);}
async function close(){await click('#editor-cancel');await until('!editor.open');}
async function period(month){await evaluate(`document.querySelector('#cost-ledger-period').value=${JSON.stringify(month)};document.querySelector('#cost-ledger-period').dispatchEvent(new Event('change'))`);await until(`state.costMonth===${JSON.stringify(month)} && main.getAttribute('aria-busy')==='false'`);}

try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Only fresh synthetic ledgers are allowed');
  const fixture=await evaluate(`(async()=>{
    await api('qa_fixture',{name:'inspection'});
    window.stabilizationSave=async(module,record,id)=>{
      const previous=id?await api('record?'+query({module,id})):{};
      const ticket=await api('write_ticket?'+query({kind:'save',module,id,expected:previous._version}));
      return api('save',{module,id,expected:previous._version,record,form_ticket:ticket.token,confirmed:true});
    };
    const cost=await stabilizationSave('costs',{project_id:1,category:'工程施工 / 材料辅料',amount:'33.01',cost_date:'2026-09-20',notes:'合成跨月费用'});
    for(let index=0;index<43;index++)await stabilizationSave('costs',{project_id:1,category:'管理与财务 / 代理记账',amount:'1.01',cost_date:'2026-10-04',notes:'合成分页费用 '+index});
    await refreshOptions();return {cost};
  })()`);
  await route('cost');
  assert.equal(await evaluate("document.querySelector('#cost-ledger-period').value"),'all');
  assert.deepEqual(await evaluate("[...main.querySelectorAll('.metric label')].map(item=>item.textContent)"),['总成本','采购成本','人工成本','费用成本']);
  await click('#cost-ledger-next');await until('state.costPage===1 && main.getAttribute("aria-busy")==="false"');
  await route('home');await click('#navigation-back');await until('state.route==="cost" && main.getAttribute("aria-busy")==="false"');
  assert.equal(await evaluate('state.costPage'),1);checks.push('全周期默认、四类金额、分页与返回状态正确');
  await period('2026-09');
  assert.match(await evaluate('main.querySelector(".metric strong").textContent'),/33\.01/);
  assert.equal(await evaluate('document.querySelectorAll("#cost-ledger-records tbody tr").length'),1);
  assert.equal(await evaluate('state.costPage'),0);
  await click(`[data-cost-record="${fixture.cost}"][data-cost-action="detail"]`);await until('editor.open');
  assert.match(await evaluate('editor.textContent'),/合成跨月费用/);await close();
  await click(`[data-cost-record="${fixture.cost}"][data-cost-action="edit"]`);await until('editor.open && !!form.elements.amount');
  await evaluate("form.elements.amount.value='35.02';form.elements.notes.value='合成成本修改';form.elements.confirmed.checked=true;form.requestSubmit()");
  await until('!editor.open && !editorSubmitting && main.getAttribute("aria-busy")==="false"');
  assert.match(await evaluate('main.querySelector(".metric strong").textContent'),/35\.02/);checks.push('同月汇总等于明细；来源查看和金额修改入口正确联动');
  await click(`[data-cost-record="${fixture.cost}"][data-cost-action="attachments"]`);await until('editor.open && !!form.elements.attachment');
  assert.match(await evaluate('editor.textContent'),/添加本地附件/);await close();
  await evaluate(`(async()=>{const ticket=await api('write_ticket?'+query({kind:'upload_attachment',entity:'cost',id:${fixture.cost}}));await api('upload_attachment',{entity:'cost',id:${fixture.cost},form_ticket:ticket.token,name:'合成三页预览.pdf',content:${JSON.stringify(fs.readFileSync(pdfFile).toString('base64'))},category:'合成附件',description:'仅验收',confirmed:true});})()`);
  await click(`[data-cost-record="${fixture.cost}"][data-cost-action="attachments"]`);await until('editor.open && !!editor.querySelector("[data-attachment-preview]")');
  const digest=await evaluate("api('qa_state').then(result=>result.digest)");
  await click('[data-attachment-preview]');await until('document.querySelector("#attachment-preview").open && document.querySelectorAll(".attachment-pdf-page").length===3');
  await until('document.querySelector(".attachment-pdf-page img").complete && document.querySelector(".attachment-pdf-page img").naturalWidth>0');
  await screenshot('01_pdf_continuous');
  await evaluate("document.querySelector('#attachment-preview-body').scrollTop=document.querySelector('#attachment-preview-body').scrollHeight");
  await until('[...document.querySelectorAll(".attachment-pdf-page img")].at(-1).naturalWidth>0');
  assert(await evaluate("api('rows?module=projects').then(result=>result.rows.length>0)"));
  await click('#attachment-preview-done');await until('!document.querySelector("#attachment-preview").open');
  assert(await evaluate('editor.open && !!form.elements.attachment'));
  assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),digest);await close();
  checks.push('成本附件入口可上传；PDF连续滚动，返回原表单，台账读取正常且预览不写账');
  await evaluate(`(async()=>{
    const old=await api('record?module=invoices&id=1');await stabilizationSave('invoices',{...old,entity_id:2},1);
    window.stabilizationReceipt=await stabilizationSave('receipts',{project_id:1,contract_id:1,amount:'500.00',receipt_date:'2026-10-03',payment_method:'银行转账',entity_id:2});
  })()`);
  await route('finance?tab=receipts');
  await evaluate('editRecord("receipts",stabilizationReceipt)');await until('editor.open && !!form.elements.entity_id');
  assert.equal(await evaluate('form.elements.entity_id.value'),'2');
  await evaluate("form.elements.notes.value='合成主体补充说明';form.elements.confirmed.checked=true;form.requestSubmit()");await until('!editor.open && !editorSubmitting && main.getAttribute("aria-busy")==="false"');
  await route('finance?tab=invoices');
  assert(await evaluate('main.textContent.includes("发票余额") && main.textContent.includes("示例安装")'));
  assert.equal(await evaluate("api('record?module=invoices&id=1').then(row=>row.unreceived_minor)"),'9950000');
  checks.push('发票及回款原表单确认主体，列表可见且余额正确更新');
  await route('cost');await period('2026-09');
  for(const width of [1366,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:768,deviceScaleFactor:1,mobile:false});
    assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Document overflow at '+width);
    await screenshot('02_cost_'+width);
    await click(`[data-cost-record="${fixture.cost}"][data-cost-action="edit"]`);await until('editor.open');
    assert(await evaluate('document.querySelector("#editor-body").scrollWidth<=document.querySelector("#editor-body").clientWidth+1'));
    assert(await evaluate('document.querySelector(".dialog-foot").getBoundingClientRect().bottom<=innerHeight+1'));
    await screenshot('03_cost_editor_'+width);await close();
  }
  checks.push('1366×768与390px页面不溢出，来源操作和固定保存区可见');
  assert.deepEqual(errors,[]);assert.deepEqual(requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin)),[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed:checks.length,checks,errors,external_requests:0},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,errors}));
}catch(error){await screenshot('FAILED');throw error;}
finally{try{await call('Browser.close');}catch{}socket.close();browser.kill();}
