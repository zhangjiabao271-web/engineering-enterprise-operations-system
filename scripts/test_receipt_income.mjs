// Receipt-income review with deterministic replies, no database or network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

function field(value='') {
  const wrapper={hidden:false},listeners={};
  return {value,disabled:false,required:false,listeners,wrapper,
    closest:()=>wrapper,addEventListener:(name,callback)=>{listeners[name]=callback;}};
}
const amount=field('100'),action=field(),reason=field(),confirmed={checked:true};
const host={innerHTML:''},pending=[],tasks=[];
let current=true;
const context=vm.createContext({
  labels:{},form:{elements:{namedItem:name=>({amount,receipt_income_action:action,receipt_income_reason:reason})[name],confirmed}},
  editor:{open:true},document:{querySelector:()=>host},
  beginEditorRequest:()=>({read:path=>new Promise(resolve=>pending.push({path,resolve})),
    check:()=>{if(!current)throw new Error('superseded');}}),
  query:values=>new URLSearchParams(values).toString(),
  safe:callback=>{const task=callback();tasks.push(task);return task;},
  SupersededView:class extends Error{},
  notice:text=>text,money:minor=>String(minor),table:()=>'<table>synthetic</table>',
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/receipt-income.js',import.meta.url),'utf8'),context);
const fields=vm.runInContext('receiptIncomeFields()',context);
assert.equal(fields[0].default,'');
assert.deepEqual(Object.keys(fields[0].choices),['retain','reverse']);

vm.runInContext('bindReceiptIncomePreview({id:12})',context);
assert(pending[0].path.includes('id=12&amount=100'));
pending.shift().resolve({requires_decision:false});await tasks.shift();
assert.equal(action.disabled,true);assert.equal(action.wrapper.hidden,true);

action.value='reverse';amount.value='50';amount.listeners.input();
assert.equal(action.value,'');assert.equal(confirmed.checked,false);
amount.listeners.change();
pending.shift().resolve({requires_decision:true,can_reverse:true,reduction_minor:5000,notice:'合成影响',rows:[]});
await tasks.shift();
assert.equal(action.disabled,false);assert.equal(action.required,true);assert.equal(reason.required,true);
assert.equal(action.wrapper.hidden,false);assert(host.innerHTML.includes('5000'));

const old=context.form._receiptIncomeRefresh();
const rejected=assert.rejects(old,error=>error.constructor.name==='SupersededView');
amount.value='40';amount.listeners.input();const latest=context.form._receiptIncomeRefresh();
const late=pending.shift(),fresh=pending.shift();
fresh.resolve({requires_decision:false});await latest;
const displayed=host.innerHTML;
late.resolve({requires_decision:true,can_reverse:true,reduction_minor:9999,rows:[]});await rejected;
assert.equal(host.innerHTML,displayed);

const closing=context.form._receiptIncomeRefresh();
const closed=assert.rejects(closing,/superseded/);current=false;
pending.shift().resolve({requires_decision:false});await closed;
assert.equal(host.innerHTML,displayed);assert.equal(pending.length,0);
console.log('PASS: receipt income choices, progressive fields, reset and stale-response guards; no ledger writes.');
