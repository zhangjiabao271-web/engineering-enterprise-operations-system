// Real pointer/keyboard acceptance; refuse formal ledgers and reuse no browser profile.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';

const origin=process.argv[2], output=path.resolve(process.argv[3]);
const root=path.resolve(import.meta.dirname,'..');
assert.equal(new URL(origin).hostname,'127.0.0.1');
assert(output.startsWith(path.join(root,'qa')+path.sep));
assert(!fs.existsSync(output),'Use a fresh acceptance directory');
fs.mkdirSync(output,{recursive:true});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const pending=new Map(), checks=[], errors=[], requests=[];
let browser, socket, sequence=0;
function receive(event) {
  const message=JSON.parse(event.data);
  if(message.id) {
    const waiter=pending.get(message.id);pending.delete(message.id);
    if(waiter) {clearTimeout(waiter.timer);message.error?waiter.reject(message.error):waiter.resolve(message.result);}
  }
  if(message.method==='Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  if(message.method==='Network.requestWillBeSent') requests.push(message.params.request.url);
}
function call(method,params={}) {
  return new Promise((resolve,reject)=>{
    const id=++sequence, timer=setTimeout(()=>{pending.delete(id);reject(new Error(method+' timeout'));},20000);
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression) {
  const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression) {
  for(let index=0;index<240;index++) {if(await evaluate(expression)) return;await pause(60);}
  throw new Error('Timed out: '+expression+' / '+await evaluate('document.querySelector("#form-error")?.textContent'));
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'nearest'})`);
  await pause(190);
  const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),b=e.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;return {x,y,hit:e.contains(document.elementFromPoint(x,y))};})()`);
  assert(point.hit,'Pointer cannot reach '+selector);
  for(const type of ['mouseMoved','mousePressed','mouseReleased'])
    await call('Input.dispatchMouseEvent',{type,x:point.x,y:point.y,button:type==='mouseMoved'?'none':'left',clickCount:type==='mouseMoved'?0:1});
}
async function search(selector,keyword) {
  await click(selector);
  await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await call('Input.insertText',{text:keyword});
}
async function choose(selector,keyword) {
  await search(selector+' + input',keyword);
  await click(selector+' ~ ul [data-choice-index="0"]');
}
async function screenshot(name) {
  await evaluate('Promise.all(editor.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))');
  const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));
}
async function route(hash) {
  await evaluate(`location.hash=${JSON.stringify(hash)}`);
  await until(`state.route===${JSON.stringify(hash.split('?')[0])} && main.getAttribute('aria-busy')==='false'`);
}
async function close() {await click('#editor-cancel');await until('!editor.open');}

try {
  assert((await fetch(origin,{signal:AbortSignal.timeout(5000)})).ok);
  const profile=path.join(output,'edge-profile');
  browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
    '--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
    '--user-data-dir='+profile,'--no-first-run','--disable-background-networking','--disable-component-update','about:blank',
  ],{windowsHide:true,stdio:'ignore'});
  const portFile=path.join(profile,'DevToolsActivePort');
  for(let index=0;!fs.existsSync(portFile)&&index<100;index++) await pause(60);
  assert(fs.existsSync(portFile),'Acceptance browser did not start');
  const port=fs.readFileSync(portFile,'utf8').split('\n')[0].trim();
  const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(tabs.find(tab=>tab.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  socket.addEventListener('message',receive);
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Synthetic writable ledgers only');
  await evaluate("api('qa_fixture',{name:'inspection'})");
  await call('Page.reload',{ignoreCache:true});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  await route('purchases');
  const fixture=await evaluate(`(async()=>{
    window.auditSave=async(module,record,id)=>{
      const old=id?await api('record?'+query({module,id})):{};
      const ticket=await api('write_ticket?'+query({kind:'save',module,id,expected:old._version}));
      return api('save',{module,id,expected:old._version,record,form_ticket:ticket.token,confirmed:true});
    };
    const first=await auditSave('offers',{supplier_id:2,name:'合成H型钢',specification:'248×124',unit:'支',price:'1050.00',price_basis:'inclusive',tax_rate_percent:'9'});
    const second=await auditSave('offers',{supplier_id:2,name:'合成H型钢',specification:'198×99',unit:'支',price:'760.00',price_basis:'exclusive',tax_rate_percent:'9'});
    return {first,second};
  })()`);
  assert(await evaluate(`!state.boot.options.offers.some(row=>String(row.value)===String(${fixture.first}))`));
  await click('#new-record');
  await until('editor.open && document.querySelectorAll(".purchase-item").length===1');
  await choose('[name="project_id"]','厂房钢结构');
  await choose('[name="supplier_id"]','钢材供应商');
  await search('.purchase-item [name="item-product_id"] + input','248*124 H型钢');
  assert.equal(await evaluate('editor.querySelectorAll(".purchase-item [data-choice-index]").length'),1);
  await screenshot('01_search_by_specification');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await until('!editorLookups.size && editor.querySelector("[name=item-unit_price]").value==="1050.00"');
  assert.equal(await evaluate('editor.querySelector("[name=item-product_id]").value'),String(fixture.first));
  assert.equal(await evaluate('editor.querySelector("[name=item-specification_snapshot]").value'),'248×124');
  await click('#add-item');
  await choose('.purchase-item:nth-child(2) [name="item-product_id"]','198 x 99 合成H型钢');
  await until('!editorLookups.size && document.querySelector(".purchase-item:nth-child(2) [name=item-unit_price]").value==="760.00"');
  assert.equal(await evaluate('document.querySelector(".purchase-item:nth-child(2) [name=item-product_id]").value'),String(fixture.second));
  await evaluate("form.elements.confirmed.checked=true;form.elements.notes.value='合成新报价采购验收'");
  await until('document.querySelector("#purchase-preview").textContent.includes("1,878.40")');
  await click('#editor-save');
  await until('!editor.open && !editorSubmitting && main.getAttribute("aria-busy")==="false"');
  const saved=await evaluate("(async()=>{const row=state.currentRows.find(row=>row.notes==='合成新报价采购验收');return api('record?'+query({module:'purchases',id:row.id}));})()");
  assert.equal(String(saved.total_amount_cents),'187840');
  assert.deepEqual(saved.items.map(row=>row.supplier_offer_id),[fixture.first,fixture.second]);
  checks.push('new quotes searchable without refreshing, distinct specifications, keyboard/mouse selection and all rows save correct prices/IDs');

  await evaluate(`(async()=>{const old=await api('record?module=purchases&id=1');
    const items=old.items.map(row=>({...row,item_id:row.item_id,product_id:row.supplier_offer_id,unit_price:minorInput(row.price_basis==='inclusive'?row.tax_inclusive_unit_price_cents:row.material_unit_price_cents),tax_percent:Number(row.tax_rate_bps)/100}));
    await auditSave('purchases',{...old,items,payment_method:'员工垫付',payment_status:'未确认',invoice_status:'收据'},1);
    await renderRoute(false);})()`);
  const before=await evaluate("api('record?module=purchases&id=1')");
  await click('[data-action="edit"][data-id="1"]');await until('editor.open && !!form.elements.invoice_status');
  assert.equal(await evaluate('form.elements.invoice_status.value'),'收据');
  assert.equal(await evaluate('form.elements.payment_status.value'),'未确认');
  assert.equal(await evaluate('form.elements.payment_method.value'),'员工垫付');
  await evaluate("form.elements.notes.value='合成只补说明';form.elements.confirmed.checked=true");
  await click('#editor-save');await until('!editor.open && !editorSubmitting');
  const after=await evaluate("api('record?module=purchases&id=1')");
  for(const key of ['invoice_status','payment_status','payment_method','total_amount_cents','supplier_id','project_id']) assert.deepEqual(after[key],before[key],key);
  checks.push('editing legacy purchase notes preserves invoice/payment statuses, amount and associations');

  const project=await evaluate(`(async()=>{const project=await auditSave('projects',{name:'合成分页项目',status:'进行中'});
    for(let index=0;index<41;index++)await auditSave('costs',{project_id:project,category:'工程施工 / 材料辅料',amount:'1.00',cost_date:businessToday()});
    return project;})()`);
  await route('cost?tab=costs');
  await click('#next-page');await until('state.page===1 && main.getAttribute("aria-busy")==="false"');
  await choose('#project-scope','厂房钢结构');
  await until('state.project==="1" && main.getAttribute("aria-busy")==="false"');
  assert.equal(await evaluate('state.page'),0);
  assert(await evaluate('!!main.querySelector("#records tbody tr")'));
  await screenshot('02_filtered_first_page');
  checks.push('project filtering clamps the page and shows existing records instead of a false empty state');
  await evaluate(`(async()=>{const old=await api('record?module=projects&id=${project}');await auditSave('projects',{...old,status:'已完工'},${project});state.project=String(${project});})()`);
  await click('#new-record');await until('editor.open && !!form.elements.category');
  assert(await evaluate(`![...form.elements.project_id.options].some(option=>option.value===String(${project}))`));
  assert.equal(await evaluate('form.elements.project_id.value'),'');
  const digest=await evaluate("api('qa_state').then(row=>row.digest)");
  await close();
  assert.equal(await evaluate("api('qa_state').then(row=>row.digest)"),digest);
  checks.push('completed project not reintroduced into new cost choices; cancellation keeps ledger unchanged');
  assert.deepEqual(errors,[]);
  assert.deepEqual(requests.filter(url=>url.startsWith('http') && !url.startsWith(origin)),[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({checks,errors,external_requests:0},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,errors}));
} catch(error) {
  if(socket?.readyState===WebSocket.OPEN) {try {await screenshot('FAILED');} catch {}}
  throw error;
} finally {
  if(socket?.readyState===WebSocket.OPEN) {try {await call('Browser.close');} catch {}}
  socket?.close();browser?.kill();
}
