// Run production form builders with synthetic values, without a database or network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const elements = new Map();
let created = 0;
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    open:false, dataset:{}, value:'quantity', classList:{remove(){}}, setAttribute(){}, addEventListener(){}, dispatchEvent(){},
    closest(){return element('field');},
    querySelectorAll(){return [];}, querySelector(child){return element(selector+' '+child);},
    showModal(){this.open=true;}, append(child){(this.children ||= []).push(child);},
  });
  return elements.get(selector);
}
class SyntheticDate extends Date {
  constructor(...args) {super(...(args.length ? args : ['2026-10-12T16:01:00Z']));}
}
const context = vm.createContext({
  Date:SyntheticDate, Event, console, setTimeout, clearTimeout, URLSearchParams,
  crypto:{randomUUID:()=> 'synthetic-entry'},
  document:{querySelector:element,createElement:tag=>element('new-'+tag+'-'+(++created)),addEventListener(){}}, window:{addEventListener(){}},
  enhanceForms(){}, WorkbenchMotion:{openDialog(){},closeDialog(dialog){dialog.open=false;}},
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/app.js',import.meta.url),'utf8'),context);
vm.runInContext(readFileSync(new URL('../web_workbench/static/purchase-allocation.js',import.meta.url),'utf8'),context);
vm.runInContext(`state.boot={options:{offers:[]},schema:{purchases:{title:'采购',fields:[]}}};form.elements={namedItem(){return null;}}`,context);
const fieldHTML=vm.runInContext('fieldHTML',context);
const purchaseItem=vm.runInContext('purchaseItem',context);
assert.equal(purchaseItem({id:77,item_id:123},{}).dataset.itemId,123);
assert(fieldHTML({name:'payment_method',label:'支付方式',kind:'select',choices:['现金']},'合成旧方式')
  .includes('value="合成旧方式" selected>合成旧方式（原记录）'));
const project={name:'project_id',label:'项目',kind:'select',source:'active_projects'};
assert(!fieldHTML(project,23,{active_projects:[]}).includes('value="23"'));
assert(fieldHTML({...project,preserve_current:true},23,{active_projects:[]}).includes('value="23" selected'));
assert(fieldHTML({name:'date',label:'日期',kind:'date',default:'2026-10-01',default_today:true})
  .includes('value="2026-10-13"'));
assert(fieldHTML({name:'date',label:'日期',kind:'date',default_today:true},'2026-09-30')
  .includes('value="2026-09-30"'));

// Opening a purchase must pass the freshly fetched choices to every material row.
const current={offers:[{value:999,label:'合成新报价 248×124',supplier_id:2}]};
const rows=[];
context.api=async path=>path.startsWith('options?') ? current : {token:'synthetic-ticket'};
context.purchaseItem=(item,options)=>{rows.push({item,options});return {};};
context.initializePurchasePreview=()=>{};
vm.runInContext("form.elements={namedItem(name){return name==='supplier_id'?document.querySelector('synthetic-supplier'):null;}};",context);
await vm.runInContext("editRecord('purchases')",context);
element('#add-item').onclick();
assert.equal(rows.length,2);
for(const row of rows) assert.equal(row.options,current);
assert.equal(vm.runInContext('state.boot.options.offers.length',context),0);
const legacy={offers:[{value:998,label:'旧运行进程的报价候选'}]};
let catalogReads=0;
context.api=async path=>{
  if(path.startsWith('options?'))return legacy;
  if(path==='rows?module=offers'){catalogReads++;return {rows:[{id:998,supplier_id:2}]};}
  return {token:'synthetic-ticket'};
};
await vm.runInContext("editRecord('purchases')",context);
assert.equal(catalogReads,1);
assert.equal(legacy.offers[0].supplier_id,2);
console.log('PASS: latest purchase choices for initial/added rows, historical choices, inactive-project boundary and Shanghai cross-day dates.');
