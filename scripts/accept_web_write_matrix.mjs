import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const origin=process.argv[2],output=path.resolve(process.argv[3]);
const testPython=process.env.SUPPLY_CHAIN_TEST_PYTHON || fileURLToPath(new URL('../.venv/Scripts/python.exe',import.meta.url));
assert(new URL(origin).hostname==='127.0.0.1');fs.mkdirSync(output,{recursive:true});
const profileArgument=process.argv[4] && !process.argv[4].startsWith('--')?process.argv[4]:null;
const profile=profileArgument?path.resolve(profileArgument):path.join(output,'edge-profile');
if(!profileArgument){
  assert(!fs.existsSync(profile),'Use a fresh isolated browser directory');
  spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',`--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','--disable-component-update','about:blank'],{windowsHide:true,stdio:'ignore'});
}
const activePort=path.join(profile,'DevToolsActivePort');
for(let index=0;!fs.existsSync(activePort)&&index<100;index++)await new Promise(resolve=>setTimeout(resolve,60));
const port=fs.readFileSync(activePort,'utf8').split('\n')[0].trim();
const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const socket=new WebSocket(pages.find(page=>page.type==='page').webSocketDebuggerUrl);
await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
let counter=0,lastWrite;const pending=new Map(),errors=[],requests=[],matrix=[];
socket.addEventListener('message',event=>{
  const message=JSON.parse(event.data);
  if(message.id){const waiter=pending.get(message.id);pending.delete(message.id);if(!waiter)return;clearTimeout(waiter.timeout);message.error?waiter.reject(message.error):waiter.resolve(message.result);}
  if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
  if(message.method==='Network.requestWillBeSent'){
    const request=message.params.request;requests.push(request.url);
    if(request.postData){const body=JSON.parse(request.postData);if(body.confirmed===true && !request.url.includes('qa_'))lastWrite={path:new URL(request.url).pathname.slice(5),body};}
  }
});
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++counter;const timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Browser command timed out: '+method));},20000);pending.set(id,{resolve,reject,timeout});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value;}
async function until(expression){for(let index=0;index<160;index++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,60));}throw new Error('Timed out '+expression+' / '+await evaluate('document.querySelector("#form-error").textContent+" / "+document.querySelector("#toast").textContent'));}
async function screenshot(name){const image=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name+'.png'),Buffer.from(image.data,'base64'));}
async function selectFile(selector,file){const document=await call('DOM.getDocument');const input=await call('DOM.querySelector',{nodeId:document.root.nodeId,selector});assert(input.nodeId,selector);await call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[file]});}
const routes={contracts:'contracts?view=all',allocations:'contracts',settlements:'contracts',invoices:'finance',receipts:'finance',collection:'finance',input_invoices:'finance',accounts:'funds',plans:'funds',transactions:'funds',historical:'finance',workers:'labor',worklogs:'labor',costs:'cost',attachments:'contracts?view=all',entities:'entities',governance:'governance',funds:'funds'};

async function openRoute(fixture){
  await call('Page.reload',{ignoreCache:true});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');
  const route=routes[fixture.module] || fixture.module;
  await evaluate(`state.project='';state.collection='';state.page=0;state.keyword='';document.querySelector('#project-scope').value='';location.hash=${JSON.stringify(route)};`);
  await until(`state.route===${JSON.stringify(route.split('?')[0])} && main.getAttribute('aria-busy')==='false' && !main.querySelector('.loading')`);
  const tab=await evaluate(`!!main.querySelector('[data-tab="${fixture.module}"]')`);
  if(tab){await evaluate(`main.querySelector('[data-tab="${fixture.module}"]').click()`);await until(`!!main.querySelector('[data-tab="${fixture.module}"].active') && !main.querySelector('.loading')`);}
  if(fixture.module==='suppliers' && fixture.id){
    await evaluate(`(()=>{const row=state.boot.options.suppliers.find(row=>String(row.value)===String(${JSON.stringify(fixture.id)}));const kind=row?.supplier_kind;if(kind)main.querySelector('[data-partner-kind="'+kind+'"]')?.click();})()`);
    await until(`!!main.querySelector('[data-partner-edit="${fixture.id}"]')`);
  }
}

async function openOperationForm(fixture){
  if(fixture.name==='attachment_void'){
    await evaluate("main.querySelector('[data-contract-files=\"1\"]').click()");await until('editor.open && !!form.elements.attachment');
    await evaluate(`editor.querySelector('[data-evidence-id="${fixture.record.ids[0]}"]').click()`);await until('editor.open && !!form.elements.reason');return;
  }
  if(fixture.name==='entity_assign'){
    await evaluate(`(()=>{const kind=main.querySelector('#entity-kind');kind.value=${JSON.stringify(fixture.record.kind)};kind.dispatchEvent(new Event('change'));const status=main.querySelector('#entity-status');status.value='all';status.dispatchEvent(new Event('change'));})()`);
    await until(`!!main.querySelector('[data-entity-assign="${fixture.record.ids[0]}"]') && !main.querySelector('.loading')`);
    await evaluate(`main.querySelector('[data-entity-assign="${fixture.record.ids[0]}"]').click()`);
  }else if(fixture.name==='entity_tax'){
    await evaluate(`main.querySelector('[data-entity-tax="${fixture.record.entity_id}"]').click()`);
  }else if(fixture.name==='entity_setup'){
    await evaluate("main.querySelector('#entity-initial-setup').click()");
  }else{
    if(fixture.name==='contract_void'){
      await evaluate(`main.querySelector('[data-contract-detail="${fixture.record.id}"][data-kind="overview"]').click()`);
      await until('editor.open && !!form.querySelector(".contract-dialog-actions")');
    }
    await evaluate(`(()=>{const host=${fixture.name==='contract_void'?'form':'main'};const buttons=[...host.querySelectorAll('[data-operation="${fixture.name}"]')];const button=buttons.find(button=>button.dataset.id===String(${JSON.stringify(fixture.record.id)})) || buttons[0];if(!button)throw new Error('Missing visible operation ${fixture.name}');const menu=button.closest('details');if(menu)menu.open=true;button.click();})()`);
  }
  await until(fixture.name==='labor_batch'?'editor.open && !!document.querySelector("#batch-worker-search")':'editor.open && !!form.elements.reason');
}

async function fillOperation(fixture){
  if(fixture.name==='attachment_void'){await evaluate(`form.elements.reason.value=${JSON.stringify(fixture.record.reason)}`);return;}
  if(fixture.name==='entity_assign'){await evaluate(`form.elements.entity_id.value=${JSON.stringify(String(fixture.record.entity_id))};form.elements.reason.value=${JSON.stringify(fixture.record.reason)}`);return;}
  if(fixture.name==='labor_batch'){
    await evaluate(`(()=>{
      const values=${JSON.stringify(fixture.record)},common=values.entries[0];
      for(const name of ['project_id','construction_site','work_date'])form.elements.namedItem(name).value=common[name];
      form.elements.notes.value=values.reason;
      for(const row of editor.querySelectorAll('.batch-worker'))row.querySelector('[name=batch-worker]').checked=false;
      for(const entry of values.entries){
        const row=editor.querySelector('.batch-worker[data-worker-id="'+entry.worker_id+'"]');
        row.querySelector('[name=batch-worker]').checked=true;
        row.querySelector('[name=work_days]').value=entry.work_days;
        row.querySelector('[name=is_overtime]').checked=!!entry.is_overtime;
        row.dispatchEvent(new Event('change',{bubbles:true}));
      }
      form.elements.project_id.dispatchEvent(new Event('choice-sync'));
    })()`);return;
  }
  await evaluate(`(async()=>{
    const values=${JSON.stringify(fixture.record)},definition=state.boot.operations[${JSON.stringify(fixture.name)}];
    for(const field of definition.fields){
      if(['allocation_rows','settlement_rows','invoice_links','worklog_rows'].includes(field.kind))continue;
      const element=form.elements.namedItem(field.name),value=values[field.name];if(value===undefined)continue;
      if(field.kind==='multi'){for(const option of element.options)option.selected=(value || []).map(String).includes(option.value);}
      else if(field.kind==='checkbox')element.checked=value;
      else element.value=value??'';
    }
    if(values.project_id && definition.fields.some(field=>field.source==='sites')){
      form.elements.project_id.dispatchEvent(new Event('change'));
    }
  })()`);
  if(fixture.record.project_site_id){await until(`!!form.elements.project_site_id.querySelector('option[value="${fixture.record.project_site_id}"]')`);await evaluate(`form.elements.project_site_id.value='${fixture.record.project_site_id}'`);}
  await evaluate(`(()=>{
    const values=${JSON.stringify(fixture.record)},definition=state.boot.operations[${JSON.stringify(fixture.name)}];
    for(const field of definition.fields.filter(field=>['allocation_rows','settlement_rows','invoice_links','worklog_rows'].includes(field.kind))){
      const host=editor.querySelector('[data-row-field="'+field.name+'"]');host.querySelectorAll('.operation-row').forEach(row=>row.remove());
      for(const record of values[field.name] || []){
        host.querySelector('.add-operation-row').click();const row=[...host.querySelectorAll('.operation-row')].at(-1);
        for(const [key,value] of Object.entries(record)){const input=row.querySelector('[name="'+key+'"]');if(input){input.value=value;input.dispatchEvent(new Event('change'));}}
      }
    }
    for(const select of form.querySelectorAll('select[data-search]'))select.dispatchEvent(new Event('choice-sync'));
    const invalid=[...form.elements].filter(element=>element.willValidate && !element.checkValidity());if(invalid.length)throw new Error('Invalid operation controls: '+invalid.map(element=>element.name || element.getAttribute('aria-label')).join(','));
  })()`);
}

async function operationCase(fixture){
  const before=await evaluate("api('qa_state').then(result=>result.digest)");
  await openOperationForm(fixture);await fillOperation(fixture);await evaluate('form.requestSubmit()');
  const checkbox={attachment_void:'evidence-confirmed',labor_batch:'batch-confirmed',entity_assign:'entity-confirmed'}[fixture.name] || 'operation-confirmed';
  await until(`!!form.elements.namedItem('${checkbox}')`);assert.equal(await evaluate(`form.elements.namedItem('${checkbox}').checked`),false);
  await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');
  assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),before,'cancel '+fixture.name);
  await openOperationForm(fixture);await fillOperation(fixture);await evaluate('form.requestSubmit()');await until(`!!form.elements.namedItem('${checkbox}')`);
  if(['purchase_allocate','fund_verify','receipt_allocation','input_links','input_deduction','governance_purchase','governance_labor','historical_receipt'].includes(fixture.name))await screenshot(fixture.name+'_review');
  lastWrite=null;await evaluate(`form.elements.namedItem('${checkbox}').checked=true;form.requestSubmit()`);
  if(fixture.name==='attachment_void'){await until('!!form.elements.attachment && !document.querySelector("#editor-save").disabled');await evaluate("document.querySelector('#editor-cancel').click()");}
  await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
  return verifyRetry(fixture);
}

async function fillOrdinary(fixture){
  await evaluate(`(async()=>{
    const values=${JSON.stringify(fixture.record)},fields=state.boot.schema[${JSON.stringify(fixture.module)}].fields;
    if(form.elements.allocation_scope && (values.contract_id || values.settlement_id || values.invoice_id)){form.elements.allocation_scope.value='contract';form.elements.allocation_scope.dispatchEvent(new Event('change'));}
    for(const field of fields){const element=form.elements.namedItem(field.name);if(!element || values[field.name]===undefined)continue;if(field.kind==='checkbox')element.checked=values[field.name];else element.value=values[field.name]??'';}
    if(form.elements.pricing_mode)form.elements.pricing_mode.dispatchEvent(new Event('change'));
    if(values.project_id && form.elements.project_id){form.elements.project_id.dispatchEvent(new Event('change'));await new Promise(resolve=>setTimeout(resolve,150));}
  })()`);
  await until('!editorLookups.size');
  for(const name of ['contract_id','settlement_id','invoice_id','project_site_id']){
    const value=fixture.record[name];if(!value)continue;
    await until(`!!form.elements.${name}.querySelector('option[value="${value}"]')`);
    await evaluate(`form.elements.${name}.value='${value}';form.elements.${name}.dispatchEvent(new Event('change'))`);
    await until('!editorLookups.size');
  }
  if(fixture.module==='purchases'){
    await evaluate(`document.querySelector('#purchase-items').replaceChildren();for(const values of ${JSON.stringify(fixture.record.items)}){document.querySelector('#add-item').click();const item=document.querySelector('#purchase-items').lastElementChild;for(const [key,value] of Object.entries(values)){const input=item.querySelector('[name="item-'+key+'"]');if(input)input.value=value??'';}}`);
  }
  await evaluate("(()=>{for(const select of form.querySelectorAll('select[data-search]'))select.dispatchEvent(new Event('choice-sync'));const invalid=[...form.elements].filter(element=>element.name!=='confirmed' && element.willValidate && !element.checkValidity());if(invalid.length)throw new Error('Invalid ordinary controls: '+invalid.map(element=>element.name || element.getAttribute('aria-label')).join(','));})()");
}

async function ordinaryCase(fixture){
  const before=await evaluate("api('qa_state').then(result=>result.digest)");
  const partner=['suppliers','customers'].includes(fixture.module);
  let selector;
  if(fixture.kind==='create'){
    selector='#new-record';
    if(partner)selector='#partner-new';
    else if(fixture.module==='contracts')selector='#contract-new';
  }else selector=partner?`[data-partner-edit="${fixture.id}"]`:`[data-action=edit][data-id="${fixture.id}"]`;
  const open=async()=>{
    if(fixture.module==='contracts' && fixture.kind==='edit'){
      await evaluate(`main.querySelector('[data-contract-detail="${fixture.id}"][data-kind="overview"]').click()`);
      await until('editor.open && !!form.querySelector("#contract-dialog-edit")');
      await evaluate('form.querySelector("#contract-dialog-edit").click()');
    }else await evaluate(`main.querySelector(${JSON.stringify(selector)}).click()`);
    await until('editor.open && !!form.elements.confirmed');await fillOrdinary(fixture);
  };
  await open();await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),before);
  await open();lastWrite=null;assert.equal(await evaluate('form.elements.confirmed.checked'),false);await evaluate('form.elements.confirmed.checked=true;form.requestSubmit()');await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
  if(['transactions','receipts','purchases','input_invoices'].includes(fixture.module))await screenshot(fixture.name+'_saved');
  return verifyRetry(fixture);
}

async function actionCase(fixture){
  const before=await evaluate("api('qa_state').then(result=>result.digest)");
  async function open(){
    const action=fixture.name==='project_site'?'sites':fixture.name;
    await evaluate(`main.querySelector('[data-action=${action}][data-id="${fixture.id}"]').click()`);
    if(fixture.name==='project_site'){await until('!!document.querySelector("#add-site")');await evaluate("document.querySelector('#add-site').click()");}
    await until('editor.open && !!form.elements.confirmed');
    await evaluate(`for(const [key,value] of Object.entries(${JSON.stringify(fixture.record)}))form.elements.namedItem(key).value=value`);
  }
  await open();await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),before);
  await open();lastWrite=null;assert.equal(await evaluate('form.elements.confirmed.checked'),false);await evaluate('form.elements.confirmed.checked=true;form.requestSubmit()');await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');
  return verifyRetry(fixture);
}

async function verifyRetry(fixture){
  assert(lastWrite,'actual browser POST not observed: '+fixture.name);
  const facts=await evaluate("api('qa_facts')");assert.deepEqual(facts.actual,facts.expected,'persisted '+fixture.name);
  const after=await evaluate("api('qa_state').then(result=>result.digest)");
  const repeated=await evaluate(`api(${JSON.stringify(lastWrite.path)},${JSON.stringify(lastWrite.body)})`);
  assert.equal(await evaluate("api('qa_state').then(result=>result.digest)"),after,'retry '+fixture.name);
  const row={...fixture,record:undefined,cancel_preserved:true,explicit_confirmation:true,repeat_preserved:true,actual:facts.actual,expected:facts.expected,status:'passed'};
  matrix.push(row);fs.writeFileSync(path.join(output,'matrix.json'),JSON.stringify(matrix,null,2));console.log('PASS',fixture.name);
}

async function evidenceCases(){
  const {spawnSync}=await import('node:child_process');
  const photo=path.join(output,'synthetic-photo.png'),attachment=path.join(output,'synthetic-source.txt');
  const generated=spawnSync(testPython,['-B','-c',"from PIL import Image;import sys;Image.new('RGB',(30,20),'blue').save(sys.argv[1])",photo],{windowsHide:true,encoding:'utf8'});
  assert.equal(generated.status,0,generated.stderr);fs.writeFileSync(attachment,'合成普通附件；原条款待人工核对。','utf8');
  const fixture=await evaluate("api('qa_fixture',{name:'create_construction'})");await openRoute(fixture);
  const record=1;
  const snapshot=()=>evaluate("api('qa_state').then(result=>result.digest)");
  const close=async()=>{await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');};
  const open=async(action)=>{await evaluate(`main.querySelector('[data-action=${action}][data-id="${record}"]').click()`);await until(`editor.open && !!form.elements.${action==='photos'?'photos':'attachment'}`);};
  async function saveEvidence(name,actual,expected){
    assert.deepEqual(actual,expected,name);assert(lastWrite,name);
    const after=await snapshot();await evaluate(`api(${JSON.stringify(lastWrite.path)},${JSON.stringify(lastWrite.body)})`);assert.equal(await snapshot(),after,'exact retry '+name);
    matrix.push({name,kind:'evidence',module:'construction',cancel_preserved:true,explicit_confirmation:true,repeat_preserved:true,actual,expected,status:'passed'});
    fs.writeFileSync(path.join(output,'matrix.json'),JSON.stringify(matrix,null,2));console.log('PASS',name);
  }
  for(const photos of [true,false]){
    const before=await snapshot(),action=photos?'photos':'attachments',selector=photos?'[name=photos]':'[name=attachment]',file=photos?photo:attachment;
    await open(action);await selectFile(selector,file);await close();assert.equal(await snapshot(),before);
    await open(action);await selectFile(selector,file);
    if(photos){assert.equal(await evaluate("form.elements.namedItem('photo-confirmed').checked"),false);await evaluate("form.elements.notes.value='合成照片来源';form.elements.namedItem('photo-confirmed').checked=true");}
    else await evaluate("form.elements.category.value='材料清单';form.elements.description.value='合成附件来源'");
    lastWrite=null;await evaluate('form.requestSubmit()');await until(`!!editor.querySelector('${photos?'[data-photo-category]':'[data-evidence-id]'}') && !document.querySelector('#editor-save').disabled`);
    const rows=await evaluate(`api('${photos?'photos?id=1':'attachments?entity=construction&id=1'}')`);
    const source=rows.find(row=>row.original_name===path.basename(file));
    if(!source){await screenshot('FAILED_evidence_upload');throw new Error('Upload did not complete: '+await evaluate("document.querySelector('#form-error').textContent"));}
    const id=photos?source.attachment_id:source.id;
    const downloaded=await evaluate(`fetch('/api/attachment?id=${id}').then(response=>response.arrayBuffer()).then(buffer=>Array.from(new Uint8Array(buffer)))`);
    assert.deepEqual(Buffer.from(downloaded),fs.readFileSync(file),'original uploaded source bytes');
    await saveEvidence(photos?'photo_upload':'attachment_upload',[source.original_name,photos?source.photo_type:source.category],[path.basename(file),photos?'施工现场':'材料清单']);
    await screenshot(photos?'photo_uploaded':'attachment_uploaded');
    if(photos){
      async function categoryPreview(){await evaluate("editor.querySelector('[data-photo-category]').click()");await until('!!form.elements.category && !form.elements.photos');await evaluate("form.elements.category.value='验收照片';form.requestSubmit()");await until('!!form.elements.reason');await evaluate("form.elements.reason.value='合成分类依据';form.requestSubmit()");await until("!!form.elements.namedItem('evidence-confirmed')");}
      const beforeCategory=await snapshot();await categoryPreview();await close();assert.equal(await snapshot(),beforeCategory);
      await open('photos');await categoryPreview();assert.equal(await evaluate("form.elements.namedItem('evidence-confirmed').checked"),false);lastWrite=null;
      await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until("!!editor.querySelector('[data-photo-evidence]') && !document.querySelector('#editor-save').disabled");
      await saveEvidence('photo_category',await evaluate("api('photos?id=1').then(rows=>rows[0].photo_type)"),'验收照片');
    }
    for(const restore of [false,true]){
      const beforeChange=await snapshot();
      async function preview(){await evaluate(`editor.querySelector('[${photos?'data-photo-evidence':'data-evidence-id'}="${id}"]').click()`);await until('!!form.elements.reason');await evaluate(`form.elements.reason.value='合成${restore?'恢复':'移出'}依据';form.requestSubmit()`);await until("!!form.elements.namedItem('evidence-confirmed')");}
      await preview();await close();assert.equal(await snapshot(),beforeChange);await open(action);
      if(restore){await evaluate(`document.querySelector('${photos?'#photo-history':'#attachment-history'}').click()`);await until(`!!editor.querySelector('[${photos?'data-photo-evidence':'data-evidence-id'}="${id}"]')`);}
      await preview();assert.equal(await evaluate("form.elements.namedItem('evidence-confirmed').checked"),false);lastWrite=null;
      await evaluate("form.elements.namedItem('evidence-confirmed').checked=true;form.requestSubmit()");await until(`!!editor.querySelector('[${photos?'data-photo-evidence':'data-evidence-id'}="${id}"]') && !document.querySelector('#editor-save').disabled`);
      const status=await evaluate(`api('attachments?entity=construction&id=1&history=1').then(rows=>{const row=rows.find(row=>row.id===${id});return [row.status,row.file_exists]})`);
      await saveEvidence(`${photos?'photo':'attachment'}_${restore?'restore':'remove'}`,status,[restore?'active':'void',true]);
      const raw=await evaluate(`fetch('/api/attachment?id=${id}&history=1').then(response=>response.arrayBuffer()).then(buffer=>Array.from(new Uint8Array(buffer)))`);assert.deepEqual(Buffer.from(raw),fs.readFileSync(file));
    }
    await close();
  }
}

async function inputPDFCases(){
  const {spawnSync}=await import('node:child_process');
  const fixture=await evaluate("api('qa_fixture',{name:'create_input_invoices'})");await openRoute(fixture);
  await ordinaryCase({...fixture,name:'manual_input_source_setup'});
  const manual=await evaluate("api('rows?module=input_invoices').then(result=>result.rows.find(row=>row.invoice_no==='88776655443322110099'))");
  const snapshot=()=>evaluate("api('qa_state').then(result=>result.digest)");
  function generate(file,number){
    const script="from tests.test_web_workbench_extended import synthetic_invoice_pdf;import sys;synthetic_invoice_pdf(sys.argv[1],number=sys.argv[2],buyer=sys.argv[3],seller=sys.argv[4],net='100.01',gross='113.01')";
    const result=spawnSync(testPython,['-B','-c',script,file,number,fixture.record.buyer_name,fixture.record.seller_name],{cwd:path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),windowsHide:true,encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
  }
  const original=path.join(output,'synthetic-manual-input.pdf');generate(original,fixture.record.invoice_no);
  async function preview(attach,file){
    const selector=attach?`[data-action=input_pdf][data-id="${manual.id}"]`:'#invoice-pdf-import';
    await evaluate(`main.querySelector(${JSON.stringify(selector)}).click()`);await until('editor.open && !!form.elements.pdf');await selectFile('[name=pdf]',file);await evaluate('form.requestSubmit()');await until("!!form.elements.namedItem('pdf-confirmed')");
  }
  const close=async()=>{await evaluate("document.querySelector('#editor-cancel').click()");await until('!editor.open && !document.querySelector("#editor-save").disabled');};
  async function confirmAndCheck(name,number,file){
    assert.equal(await evaluate("form.elements.namedItem('pdf-confirmed').checked"),false);
    lastWrite=null;await evaluate("form.elements.namedItem('pdf-confirmed').checked=true;form.requestSubmit()");await until('!editor.open && !document.querySelector("#editor-save").disabled && !main.querySelector(".loading")');assert(lastWrite,name);
    const row=await evaluate(`api('rows?module=input_invoices').then(result=>result.rows.find(row=>row.invoice_no===${JSON.stringify(number)}))`);
    const detail=await evaluate(`api('record?module=input_invoices&id=${row.id}')`);
    const expected=[number,'2026-10-03','11301','10001','1300','pending',createHash('sha256').update(fs.readFileSync(file)).digest('hex')];
    const actual=[detail.invoice_no,detail.invoice_date,detail.gross_minor,detail.net_minor,detail.tax_minor,detail.deduction_status,detail.file_sha256];assert.deepEqual(actual,expected,name);
    const after=await snapshot();await evaluate(`api(${JSON.stringify(lastWrite.path)},${JSON.stringify(lastWrite.body)})`);assert.equal(await snapshot(),after,'exact PDF retry');
    matrix.push({name,kind:'pdf',module:'input_invoices',cancel_preserved:true,explicit_confirmation:true,repeat_preserved:true,actual,expected,status:'passed'});fs.writeFileSync(path.join(output,'matrix.json'),JSON.stringify(matrix,null,2));console.log('PASS',name);await screenshot(name+'_saved');
  }
  const beforeAttach=await snapshot();await preview(true,original);await close();assert.equal(await snapshot(),beforeAttach);await preview(true,original);await confirmAndCheck('input_pdf_attach',fixture.record.invoice_no,original);
  const number='99887766554433221100',file=path.join(output,'synthetic-new-input.pdf');generate(file,number);
  const beforeCreate=await snapshot();await preview(false,file);await close();assert.equal(await snapshot(),beforeCreate);
  await preview(false,file);await evaluate(`form.elements.entity_id.value='${fixture.record.entity_id}';form.elements.supplier_id.value='${fixture.record.supplier_id}';form.elements.supplier_id.dispatchEvent(new Event('choice-sync'))`);await confirmAndCheck('input_pdf_create',number,file);
}

try{
  await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin});await until('typeof state!=="undefined" && state.boot && !main.querySelector(".loading")');assert(await evaluate('state.boot.demo && !state.boot.read_only'));
  const selectedArgument=process.argv.find(argument=>argument.startsWith('--only='));
  const selected=selectedArgument?new Set(selectedArgument.slice(7).split(',')):null;
  const cases=(await evaluate("api('qa_cases')")).filter(entry=>!selected || selected.has(entry.name));
  if(selected)assert.equal(cases.length,selected.size,'Unknown or duplicate selected test case');
  for(const entry of process.argv.includes('--evidence-only')?[]:cases){
    const fixture=await evaluate(`api('qa_fixture',{name:${JSON.stringify(entry.name)}})`);
    try{
      await openRoute(fixture);
      if(fixture.kind==='operation')await operationCase(fixture);
      else if(fixture.kind==='action')await actionCase(fixture);
      else await ordinaryCase(fixture);
    }
    catch(error){await screenshot('FAILED_'+fixture.name);matrix.push({...entry,status:'failed',error:String(error)});fs.writeFileSync(path.join(output,'matrix.json'),JSON.stringify(matrix,null,2));console.log('FAIL',fixture.name,String(error));}
  }
  if(!selected){await evidenceCases();await inputPDFCases();}
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({cases:matrix.length,matrix,errors,external_requests:0},null,2));console.log('Complete browser write matrix:',matrix.length);
  assert.deepEqual(matrix.filter(row=>row.status!=='passed'),[]);assert.deepEqual(errors,[]);assert.deepEqual(requests.filter(url=>url.startsWith('http')&&!url.startsWith(origin)),[]);
}finally{try{await call('Browser.close');}catch{}socket.close();}
