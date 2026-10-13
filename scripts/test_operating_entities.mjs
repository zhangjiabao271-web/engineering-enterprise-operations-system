// Production markup/matching checks, without browser, network or ledger writes.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const context=vm.createContext({console,setTimeout,clearTimeout,URLSearchParams,
  document:{querySelector(){return {addEventListener(){}};},addEventListener(){}},
  window:{addEventListener(){}}});
for(const file of ['app.js','entities.js'])vm.runInContext(readFileSync(new URL('../web_workbench/static/'+file,import.meta.url),'utf8'),context);
vm.runInContext('state.boot={read_only:false};',context);
const rowHTML=vm.runInContext('entityRegistryRowHTML',context);
const metric=vm.runInContext('entityMetricButton',context);
const comparison=vm.runInContext('entityIncomeComparison',context);
const row={id:1,label:'<script>合成工程</script>',projects:'合成项目',date:'2026-10-13',
  ownership_status:'inherited',ownership_label:'已自动归属',entity_id:1,entity_name:'合成主体',
  ownership_source:'沿用合同',ownership_reason:'项目自动沿用合同',assignable:false,amount_minor:null};
let html=rowHTML(row);
assert(!html.includes('<script>'));
assert(html.includes('&lt;script&gt;合成工程&lt;/script&gt;'));
assert(html.includes('查看关联合同')&&!html.includes('data-entity-assign'));
assert(html.includes('disabled')&&html.includes('已自动归属'));
html=rowHTML({...row,assignable:true,ownership_status:'confirmed',ownership_label:'已确认'});
assert(html.includes('更正归属')&&!html.includes('查看关联合同'));
vm.runInContext('state.boot.read_only=true;',context);
assert(rowHTML({...row,assignable:true}).includes('data-entity-assign="1" disabled'));
assert(metric('income','9007199254740993',1,{label:'收入',definition:'合成口径'}).includes('¥90,071,992,547,409.93'));
html=comparison({total_metrics:{income_minor:'10000'},pending_metrics:{income_minor:'5000'},
  entities:[{id:1,name:'示例钢构',metrics:{income_minor:'0'}},{id:2,name:'示例安装',metrics:{income_minor:'5000'}}]});
assert(!html.includes('entity-series-0'));
assert(html.includes('entity-series-1')&&html.includes('entity-series-2'));
assert(html.includes('50.0%'));
vm.runInContext(`entityRegistryView.status='pending';entityRegistryView.search='合成';
  entityRecords=[{label:'合成甲',ownership_status:'confirmed'},
                 {label:'合成乙',ownership_status:'inherited'},
                 {label:'合成丙',ownership_status:'pending'},
                 {label:'合成丁',ownership_status:'conflict'}];`,context);
assert.equal(vm.runInContext('entityMatchingRecords().length',context),2);
vm.runInContext("entityRegistryView.status='inherited'",context);
assert.equal(vm.runInContext('entityMatchingRecords()[0].label',context),'合成乙');
vm.runInContext("entityRegistryView.status='all';entityRegistryView.search='不存在'",context);
assert.equal(vm.runInContext('entityMatchingRecords().length',context),0);
console.log('PASS: explicit/inherited actions, readonly protection, text escaping, exact large amounts, stable chart colors and status/search filtering.');
