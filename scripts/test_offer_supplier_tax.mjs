// Real supplier-tax binding and dialog lifecycle; no database or network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const elements=new Map(),pending=[],errors=[];
function element(selector) {
  if(!elements.has(selector))elements.set(selector,{
    open:true,classList:{remove(){}},setAttribute(){},addEventListener(){},
    querySelectorAll(){return [];},querySelector:child=>element(selector+' '+child),showModal(){this.open=true;},
  });
  return elements.get(selector);
}
const context=vm.createContext({
  console,setTimeout,clearTimeout,URLSearchParams,Event,
  document:{querySelector:element,addEventListener(){},createElement:()=>({})},
  window:{addEventListener(){}},WorkbenchMotion:{openDialog(){},closeDialog(dialog){dialog.open=false;}},
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/app.js',import.meta.url),'utf8'),context);
vm.runInContext(readFileSync(new URL('../web_workbench/static/forms.js',import.meta.url),'utf8'),context);
context.api=path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
context.toast=message=>errors.push(message);
const bind=vm.runInContext('bindOfferSupplierTax',context);
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function field(value='') {
  const listeners=new Map(),input={addEventListener:(name,callback)=>listeners.set('search-'+name,callback)};
  return {value,listeners,closest:()=>({append(){},querySelector:()=>input}),
    addEventListener:(name,callback)=>listeners.set(name,callback),
    dispatchEvent:event=>listeners.get(event.type)?.(event)};
}
function fresh(record={}) {
  vm.runInContext('editorSession++;editorLookups=new Map();editor.open=true;',context);
  const supplier=field(String(record.supplier_id || '')),tax=field(String(record.tax_rate_percent ?? '0')),confirmed={checked:true};
  element('#editor-form').elements={namedItem:name=>({supplier_id:supplier,tax_rate_percent:tax,confirmed})[name]};
  bind(record);
  return {supplier,tax,confirmed};
}
async function choose(form,owner) {
  form.supplier.value=owner;
  form.supplier.dispatchEvent(new Event('change'));
  await settle();
}

let form=fresh();assert.equal(pending.length,0);
await choose(form,'1');assert.equal(pending[0].path,'record?module=suppliers&id=1');
pending.shift().resolve({default_tax_rate_percent:13});await settle();
assert.equal(form.tax.value,'13');assert.equal(form.confirmed.checked,false);
await choose(form,'2');pending.shift().resolve({default_tax_rate_percent:0});await settle();
assert.equal(form.tax.value,'0');
form.supplier.value='';form.supplier.listeners.get('search-input')();assert.equal(form.tax.value,'');

await choose(form,'1');const older=pending.shift();
await choose(form,'2');pending.shift().resolve({default_tax_rate_percent:9});await settle();
older.resolve({default_tax_rate_percent:13});await settle();assert.equal(form.tax.value,'9');
await choose(form,'1');const manual=pending.shift();
form.tax.value='1';form.tax.dispatchEvent(new Event('input'));
manual.resolve({default_tax_rate_percent:13});await settle();assert.equal(form.tax.value,'1');

form=fresh({id:8,supplier_id:1,tax_rate_percent:3});
assert.equal(pending.length,0);assert.equal(form.tax.value,'3');
form=fresh({supplier_id:1,tax_rate_percent:0});assert.equal(pending.length,0);assert.equal(form.tax.value,'0');
form=fresh({supplier_id:1});pending.shift().resolve({default_tax_rate_percent:10});await settle();assert.equal(form.tax.value,'10');

await choose(form,'2');const closed=pending.shift();
const next=fresh({id:9,supplier_id:3,tax_rate_percent:6});
closed.resolve({default_tax_rate_percent:13});await settle();assert.equal(next.tax.value,'6');
await choose(next,'1');pending.shift().reject(new Error('合成读取失败'));await settle();
assert.equal(vm.runInContext('editorLookups.size',context),1);
next.tax.value='1';next.tax.dispatchEvent(new Event('input'));
assert.equal(vm.runInContext('editorLookups.size',context),0);
assert.deepEqual(errors,['合成读取失败']);assert.equal(pending.length,0);
console.log('PASS: supplier defaults, zero rate, history, explicit/manual values, search clearing, stale dialogs and error recovery.');
