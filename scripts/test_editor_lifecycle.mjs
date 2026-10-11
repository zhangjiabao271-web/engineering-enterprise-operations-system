// Real dialog-opening code, deterministic delayed replies, no business database.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    open:false, classList:{remove(){}}, setAttribute(){}, addEventListener(){},
    querySelectorAll(){return [];}, querySelector(child){return element(selector+' '+child);},
    showModal(){this.open=true;},
  });
  return elements.get(selector);
}
const context=vm.createContext({
  console, setTimeout, clearTimeout, URLSearchParams,
  document:{querySelector:element,addEventListener(){}}, window:{addEventListener(){}},
  enhanceForms(){}, WorkbenchMotion:{openDialog(){},closeDialog(dialog){dialog.open=false;}},
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/app.js',import.meta.url),'utf8'),context);
const pending=[], discarded=[];
context.api=(path,data)=>{
  if (path==='write_discard' || path==='cashflow/discard') {discarded.push(data.token);return Promise.resolve(true);}
  return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
};
const begin=vm.runInContext('beginEditorRequest',context);
const superseded=error=>error.constructor.name==='SupersededView';

// A later click wins, regardless of reply order.
let request=begin(),reply=request.read('record?module=costs&id=1');
let rejected=assert.rejects(reply,superseded);
const latest=begin();
pending.shift().resolve({id:1});await rejected;
reply=latest.read('record?module=costs&id=2');pending.shift().resolve({id:2});
assert.equal((await reply).id,2);

// Opening a synchronous dialog invalidates any older asynchronous opener.
request=begin();reply=request.read('record?module=costs&id=1');rejected=assert.rejects(reply,superseded);
vm.runInContext("openEditor('新对话框','新内容',null)",context);
pending.shift().resolve({id:1});await rejected;
assert.equal(element('#dialog-title').textContent,'新对话框');

// Closing and navigating cannot resurrect a pending form.
request=begin();reply=request.read('options');rejected=assert.rejects(reply,superseded);
await vm.runInContext('closeEditor()',context);
pending.shift().resolve({projects:[]});await rejected;
request=begin();reply=request.read('options');rejected=assert.rejects(reply,superseded);
vm.runInContext('loadingVersion++',context);pending.shift().resolve({projects:[]});await rejected;

// A late form ticket is explicitly discarded, never used by the new form.
request=begin();reply=request.read('write_ticket?module=costs');rejected=assert.rejects(reply,superseded);
begin();pending.shift().resolve({token:'synthetic-old-ticket'});await rejected;
assert.deepEqual(discarded,['synthetic-old-ticket']);

// Stale failures stay silent; a failure of the current request is preserved.
request=begin();reply=request.read('options');rejected=assert.rejects(reply,superseded);
begin();pending.shift().reject(new Error('旧请求失败'));await rejected;
request=begin();reply=request.read('options');rejected=assert.rejects(reply,/当前请求失败/);
pending.shift().reject(new Error('当前请求失败'));await rejected;
assert.equal(pending.length,0);
console.log('PASS: 8 dialog lifecycle checks; no ledger or network access.');

// Project ledger requests use the same late-response guard as edit forms.
vm.runInContext(readFileSync(new URL('../web_workbench/static/project-ledger.js',import.meta.url),'utf8'),context);
const showLedger=[];
context.showProjectLedger=(data,kind)=>showLedger.push({data,kind});
const openLedger=vm.runInContext('openProjectLedger',context);
reply=openLedger(1,'receipts');rejected=assert.rejects(reply,superseded);
await vm.runInContext('closeEditor()',context);
pending.shift().resolve({project:{id:1}});await rejected;
reply=openLedger(1,'settlements');rejected=assert.rejects(reply,superseded);
const latestLedger=openLedger(2,'invoices');
pending.shift().resolve({project:{id:1}});await rejected;
pending.shift().resolve({project:{id:2}});await latestLedger;
assert.deepEqual(showLedger,[{data:{project:{id:2}},kind:'invoices'}]);
vm.runInContext('showProjectLedger = () => {}',context);
console.log('PASS: project ledger close and latest-click protection.');

// Source interpretation follows the same dialog and navigation generation.
vm.runInContext("state.boot={read_only:true};openEditor('资料读取','',()=>{})",context);
const previewSource=vm.runInContext('requestSourcePreview',context);
let sourceReply=previewSource('cashflow/preview',{}, {contract_id:1,project_id:1},'合同正文','cashflow/save_document');
rejected=assert.rejects(sourceReply,superseded);
await Promise.resolve();
await vm.runInContext('closeEditor()',context);
pending.shift().resolve({token:'synthetic-stale-source'});
await rejected;
assert(discarded.includes('synthetic-stale-source'));
assert.equal(element('#editor').open,false);

vm.runInContext("openEditor('资料读取','',()=>{})",context);
sourceReply=previewSource('cashflow/preview',{}, {contract_id:1,project_id:1},'合同正文','cashflow/save_document');
await Promise.resolve();
const sourceRequest=pending.shift();
const understood={summary:'先垫资，验收后收款。',kind:'合同正文',model:'合成视觉模型',nodes:[{
  title:'<script>恶意内容</script>',direction:'in',amount_minor:4000000,due_date:null,timing:'验收后30天',
  condition:'验收通过',counterparty:'合成客户',calculation:'合同额 × 70% − 前序金额',issues:['触发日待补'],
  evidence:[{locator:'PDF第1页',excerpt:'验收后30天付至70%'}],
}],risks:[]};
sourceRequest.resolve({token:'synthetic-current-source',name:'合成资料.pdf',sha256:'synthetic-file-fingerprint',
  fragments:[{locator:'PDF第1页',text:'合成原文'}],recognition:{ocr_pages:[],warnings:[],page_count:1},understanding:understood});
await sourceReply;
const previewHTML=element('#editor-body').innerHTML;
assert(previewHTML.includes('这份资料对资金安排意味着什么'));
assert(previewHTML.includes('验收后30天'));
assert(previewHTML.includes('¥40,000.00'));
assert(!previewHTML.includes('<script>恶意内容'));
assert(previewHTML.includes('&lt;script&gt;'));
assert(!previewHTML.includes('重新识别'));
assert.equal(element('#dialog-title').textContent,'核对资料理解 · 合成资料.pdf');
console.log('PASS: source preview late-result protection, semantic formatting and HTML escaping.');

// A stale running backend must not silently substitute local OCR for model understanding.
vm.runInContext("openEditor('资料读取','',()=>{})",context);
sourceReply=previewSource('cashflow/preview',{}, {contract_id:1,project_id:1},'合同正文','cashflow/save_document');
rejected=assert.rejects(sourceReply,/重启工作台/);
await Promise.resolve();
pending.shift().resolve({token:'synthetic-old-backend',understanding:null});
await rejected;
assert(discarded.includes('synthetic-old-backend'));
console.log('PASS: old backend cannot masquerade as completed model interpretation.');
