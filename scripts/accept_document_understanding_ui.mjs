// Visible controls in a disposable Edge profile against the mock-model fixture only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';

const origin=process.argv[2];
assert.equal(new URL(origin).hostname,'127.0.0.1');
const output=path.resolve(process.argv[3]);
assert(output.startsWith(path.resolve('qa')+path.sep));
assert(!fs.existsSync(output),'Use a new QA directory');
fs.mkdirSync(output,{recursive:true});
const profile=path.join(output,'edge-profile');
const browser=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
  '--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${profile}`,'--no-first-run','--disable-extensions','--disable-background-networking',
  '--disable-component-update','about:blank',
],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const pending=new Map();
const errors=[];
let ws,sequence=0;
function call(method,params={}) {
  return new Promise((resolve,reject)=>{
    const id=++sequence;
    const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Browser timeout: '+method));},15000);
    pending.set(id,{resolve,reject,timeout});ws.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression) {
  const response=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
  return response.result.value;
}
async function until(expression) {
  for(let index=0;index<150;index++) {
    if(await evaluate(expression)) return;
    await sleep(80);
  }
  throw new Error('UI condition timed out: '+expression);
}
async function screenshot(name) {
  await until("!document.getAnimations().some(animation=>animation.playState==='running')");
  const result=await call('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(result.data,'base64'));
}

try {
  const portFile=path.join(profile,'DevToolsActivePort');
  for(let i=0;!fs.existsSync(portFile)&&i<150;i++) await sleep(80);
  const port=fs.readFileSync(portFile,'utf8').split('\n')[0].trim();
  const targets=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws=new WebSocket(targets.find(target=>target.type==='page').webSocketDebuggerUrl);
  await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  ws.addEventListener('message',event=>{
    const message=JSON.parse(event.data);
    if(message.id) {
      const item=pending.get(message.id);if(!item)return;
      clearTimeout(item.timeout);pending.delete(message.id);
      if(message.error)item.reject(message.error);else item.resolve(message.result);
    }
    if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
  });
  await call('Runtime.enable');await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:940,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin+'/#cashflow'});
  await until("typeof state!=='undefined' && state.boot && state.route==='cashflow' && document.querySelector('#load-flow')");
  assert(await evaluate('state.boot.demo && !state.boot.read_only'),'Synthetic writable fixture required');
  await evaluate("document.querySelector('#flow-contract').value='1';document.querySelector('#flow-project').value='1';document.querySelector('#load-flow').click()");
  await until("document.querySelector('[data-read-attachment=\"1\"]')");
  await evaluate("document.querySelector('[data-read-attachment=\"1\"]').click()");
  await until("editor.open && form.elements.attachment_id && !editorSubmitting");
  assert((await evaluate("document.querySelector('#editor-body').textContent")).includes('发送给你配置的模型'));
  await evaluate("document.querySelector('#editor-save').click()");
  await until("editor.open && document.querySelector('.understanding-summary') && !editorSubmitting");
  assert.equal(await evaluate("document.querySelectorAll('.understood-node').length"),3);
  const text=await evaluate("document.querySelector('#editor-body').textContent");
  assert(text.includes('¥40,000.00') && text.includes('验收后30天'));
  assert(!text.includes('本机OCR') && !text.includes('重新识别'));
  assert.equal(await evaluate('form.elements.acknowledged.checked'),false);
  assert.equal(await evaluate("(async()=> (await api('cashflow/documents?contract_id=1&project_id=1')).length)()"),0);
  await screenshot('desktop-preview');
  await evaluate("document.querySelector('#editor-save').click()");
  await sleep(150);
  assert.equal(await evaluate("(async()=> (await api('cashflow/documents?contract_id=1&project_id=1')).length)()"),0);
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate("document.activeElement?.blur();document.querySelector('#editor-body').scrollTop=0");
  await screenshot('mobile-preview');
  assert(await evaluate('editor.getBoundingClientRect().width<=innerWidth'),'Preview fits mobile width');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:940,deviceScaleFactor:1,mobile:false});
  await evaluate("form.elements.acknowledged.click();document.querySelector('#editor-save').click()");
  await until("!editor.open && !editorSubmitting && document.querySelector('[data-source]')");
  assert.equal(await evaluate("(async()=> (await api('cashflow/documents?contract_id=1&project_id=1')).length)()"),1);
  await evaluate("document.querySelector('#analyze-flow').click()");
  await until("document.querySelector('#analysis-result .understanding-summary')");
  assert.equal(await evaluate('state.analysis.payload.rows.length'),3);
  assert.equal(await evaluate('state.analysis.payload.base.excluded_keys.length'),2);
  assert.equal(await evaluate('state.analysis.plan_ids.length'),0);
  await evaluate("document.querySelector('#analysis-result .understanding-summary').scrollIntoView({block:'start'})");
  await screenshot('analysis-with-business-meaning');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,checks:[
    'selected-source disclosure','one first-read model interpretation','summary and stage cards',
    'cumulative amount difference','relative timing preserved','confirmation not preselected',
    'preview cannot save without acknowledgement','mobile dialog width','trusted source persistence',
    'analysis uses semantic nodes','no automatic formal plans','no browser runtime errors',
  ],errors},null,2));
  console.log('PASS: 12 original-page semantic preview and analysis UI checks.');
} finally {
  if(ws?.readyState===WebSocket.OPEN) {
    try{await call('Browser.close');}catch{}ws.close();
  }
  if(browser.exitCode===null)browser.kill();
}
