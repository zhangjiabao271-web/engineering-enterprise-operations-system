// Urgent interaction audit. Synthetic local ledger only; delayed responses never leave this machine.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

const origin=process.argv[2], output=path.resolve(process.argv[3]);
const reproduce=process.argv.includes('--reproduce');
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
const pending=new Map(), errors=[], requests=[], checks=[];
socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id){
    const waiter=pending.get(message.id);pending.delete(message.id);
    if(waiter){clearTimeout(waiter.timeout);message.error?waiter.reject(message.error):waiter.resolve(message.result);}
  }
  if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
  if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);
});
function call(method,params={}){return new Promise((resolve,reject)=>{
  const id=++sequence,timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Browser timeout: '+method));},20000);
  pending.set(id,{resolve,reject,timeout});socket.send(JSON.stringify({id,method,params}));
});}
async function evaluate(expression){
  const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function until(expression){
  for(let attempt=0;attempt<160;attempt++){if(await evaluate(expression))return;await sleep(60);}
  throw new Error('Timed out: '+expression);
}
async function screenshot(name){
  const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(capture.data,'base64'));
}
async function click(selector){
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'nearest'})`);
  await sleep(200);
  const target=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),b=e.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;return {x,y,hit:e.contains(document.elementFromPoint(x,y))};})()`);
  assert(target.hit,'Pointer cannot reach '+selector);
  for(const type of ['mouseMoved','mousePressed','mouseReleased'])await call('Input.dispatchMouseEvent',{type,button:type==='mouseMoved'?'none':'left',clickCount:type==='mouseMoved'?0:1,x:target.x,y:target.y});
}
async function route(name){
  await click(`.nav-link[data-route="${name}"]`);
  await until(`state.route===${JSON.stringify(name)} && main.getAttribute('aria-busy')==='false'`);
}
async function choose(name,value){
  const selector=`#editor-body [name="${name}"]`;
  const label=await evaluate(`document.querySelector(${JSON.stringify(selector)}).querySelector('option[value="${value}"]').textContent`);
  await click(selector+' + input');
  await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector+' + input')});input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await call('Input.insertText',{text:label});
  await click(selector+' ~ ul [data-choice-index="0"]');
}
async function openNew(module){
  await evaluate(`safe(()=>editRecord(${JSON.stringify(module)}))`);
  await until('editor.open');
}
async function close(){await click('#editor-cancel');await until('!editor.open');}
async function holdResponse(match){
  await evaluate(`(()=>{
    window.auditHeld=[];window.auditMatches=0;
    window.auditOriginalFetch ||= window.fetch.bind(window);
    window.fetch=async (url,config={})=>{
      const matching=(${match})(String(url),config);
      if(matching)window.auditMatches++;
      const response=await window.auditOriginalFetch(url,config);
      if(matching)return await new Promise(resolve=>window.auditHeld.push(()=>resolve(response)));
      return response;
    };
  })()`);
}
async function release(){await evaluate('auditHeld.splice(0).forEach(resolve=>resolve());window.fetch=window.auditOriginalFetch');await sleep(450);}
function check(name,actual,expected){
  const passed=JSON.stringify(actual)===JSON.stringify(expected);
  checks.push({name,status:passed?'passed':'failed',actual,expected});
  console.log(JSON.stringify(checks.at(-1)));
  if(!reproduce)assert.deepEqual(actual,expected,name);
}

try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});
  await until('typeof state!=="undefined" && state.boot && main.getAttribute("aria-busy")==="false"');
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Synthetic writable mode required');
  await evaluate("api('qa_fixture',{name:'create_workers'})");
  const fixture=await evaluate(`(async()=>{
    const save=async(module,record)=>{const ticket=await api('write_ticket?'+query({kind:'save',module}));return api('save',{module,record,form_ticket:ticket.token,confirmed:true});};
    const p=await save('projects',{name:'合成审计第二项目',project_code:'AUDIT-P2',customer_partner_id:1});
    const c=await save('contracts',{name:'合成审计第二合同',contract_no:'AUDIT-C2',amount:'1000',customer_partner_id:1,status:'active'});
    await save('allocations',{project_id:p,contract_id:c,amount:'1000'});
    const s=await save('settlements',{project_id:p,contract_id:c,amount:'100',settlement_date:'2026-10-05',basis:'合成审计确认'});
    const i=await save('invoices',{project_id:p,contract_id:c,settlement_id:s,invoice_no:'AUDIT-INV2',amount:'100',invoice_date:'2026-10-05'});
    const offer=await save('offers',{supplier_id:2,name:'合成审计彩钢瓦',unit:'米',price:'16',tax_rate_percent:'9',price_basis:'inclusive'});
    await refreshOptions();await renderRoute(false);return {p,c,s,i,offer};
  })()`);
  await click('[data-project-cost="1"]');await until('editor.open');
  check('驾驶舱成本明细按钮实际可点击',await evaluate('document.querySelector("#dialog-title").textContent.includes("成本明细")'),true);
  await close();

  // Same-form stale project response must not replace the newer project's choices.
  await route('contracts');await openNew('settlements');
  await evaluate("form.elements.amount.value='10';form.elements.basis.value='合成延迟选项校验';form.elements.confirmed.checked=true");
  await holdResponse(`url=>url.startsWith('/api/options?') && new URL(url,location.origin).searchParams.get('project_id')==='1'`);
  await choose('project_id',1);await until('auditHeld.length===1');
  if(!reproduce){
    check('项目变更立即清除旧合同并等待刷新',await evaluate('form.elements.contract_id.disabled && !form.elements.contract_id.value'),true);
    await click('#editor-save');
    check('关联查询未完成不能提交',await evaluate('!editorSubmitting && !document.querySelector("#toast").hidden && document.querySelector("#toast").textContent.includes("正在读取")'),true);
  }
  await choose('project_id',fixture.p);
  await until(`form.elements.contract_id.options.length===2 && !!form.elements.contract_id.querySelector('option[value="${fixture.c}"]')`);
  await release();
  check('快速切换项目只保留当前合同候选',await evaluate('[...form.elements.contract_id.options].filter(o=>o.value).map(o=>Number(o.value))'),[fixture.c]);
  await screenshot('01_project_choices');await close();

  // An old lookup must not write into a newly opened dialog.
  await openNew('settlements');
  await holdResponse(`url=>url.startsWith('/api/options?') && new URL(url,location.origin).searchParams.get('project_id')==='1'`);
  await choose('project_id',1);await until('auditHeld.length===1');await close();
  await openNew('settlements');await choose('project_id',fixture.p);
  await until(`form.elements.contract_id.options.length===2 && !!form.elements.contract_id.querySelector('option[value="${fixture.c}"]')`);
  await release();
  check('已关闭表单的迟到查询不覆盖新表单',await evaluate('[...form.elements.contract_id.options].filter(o=>o.value).map(o=>Number(o.value))'),[fixture.c]);
  await close();

  // Slow quotation lookup can otherwise overwrite the chosen material and price.
  await route('purchases');await openNew('purchases');
  await holdResponse(`url=>url.startsWith('/api/record?') && new URL(url,location.origin).searchParams.get('module')==='offers' && new URL(url,location.origin).searchParams.get('id')==='1'`);
  await choose('item-product_id',1);await until('auditHeld.length===1');
  await choose('item-product_id',fixture.offer);
  await until('editor.querySelector("[name=item-unit_price]").value==="16.00"');
  await release();
  check('快速切换报价保留最后选择的价格',await evaluate('editor.querySelector("[name=item-unit_price]").value'),'16.00');
  await screenshot('02_offer_price');await close();

  if(!reproduce){
    await openNew('purchases');
    await holdResponse(`url=>url.startsWith('/api/record?') && new URL(url,location.origin).searchParams.get('module')==='offers'`);
    await choose('item-product_id',1);await until('auditHeld.length===1');
    await click('#add-item');await click('.purchase-item:first-child .remove-item');
    check('移除正在读取报价的材料不阻塞其余行',await evaluate('editorLookups.size'),0);
    await release();await close();

    await route('labor');await openNew('workers');
    await evaluate("form.elements.name.value='合成审计错误恢复工人';form.elements.daily_rate.value='-1';form.elements.confirmed.checked=true");
    await click('#editor-save');
    await until('editor.open && !document.querySelector("#form-error").hidden && !editorSubmitting');
    check('保存失败保留原输入并解除锁定',await evaluate('form.elements.name.value==="合成审计错误恢复工人" && form.elements.daily_rate.value==="-1" && !document.querySelector("#editor-body").inert && !document.querySelector("#editor-cancel").disabled'),true);
    await evaluate("form.elements.daily_rate.value='200'");await click('#editor-save');
    await until('!editor.open && !editorSubmitting');
    check('校正错误后可正常保存',await evaluate("api('rows?module=workers').then(r=>r.rows.filter(w=>w.name==='合成审计错误恢复工人').length)"),1);
  }

  // The save has committed, but its response is delayed. Cancellation must not hide it.
  await route('labor');await openNew('workers');
  await evaluate("form.elements.name.value='合成审计保存中工人';form.elements.daily_rate.value='310';form.elements.confirmed.checked=true");
  await holdResponse(`(url,config)=>url==='/api/save' && config.method==='POST'`);
  await click('#editor-save');await until('auditHeld.length===1');
  if(reproduce)await click('#editor-cancel');
  else {
    check('保存中取消和关闭按钮禁用',await evaluate('document.querySelector("#editor-cancel").disabled && document.querySelector("#editor-close").disabled'),true);
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
    await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape'});
    check('保存中Escape不隐藏提交',await evaluate('editor.open'),true);
    await evaluate("form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))");
    await sleep(100);
    check('保存期间再次提交不发第二次写入',await evaluate('auditMatches'),1);
  }
  await sleep(180);
  check('保存结果回来前表单保持打开',await evaluate('editor.open'),true);
  if(reproduce){await openNew('workers');await evaluate("form.elements.name.value='合成审计尚未保存的新输入'");}
  await release();await until('!editor.open && main.getAttribute("aria-busy")==="false"');
  const saved=await evaluate("api('rows?module=workers&keyword='+encodeURIComponent('合成审计保存中工人')).then(r=>r.rows.filter(w=>w.name==='合成审计保存中工人').length)");
  check('确认记录仅保存一次',saved,1);

  const external=requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin));
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={mode:reproduce?'reproduction':'acceptance',checks,failed:checks.filter(item=>item.status==='failed').length,errors,external_requests:external.length};
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({failed:result.failed,checks:checks.length}));
}catch(error){await screenshot('FAILED');throw error;}
finally{try{await call('Browser.close');}catch{}socket.close();}
