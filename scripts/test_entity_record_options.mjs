// Exercise the production option mapper and HTML; no ledger or network access.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({
  console, setTimeout, clearTimeout, URLSearchParams,
  document: {querySelector() { return {addEventListener() {}}; }, addEventListener() {}},
  window: {addEventListener() {}},
});
for (const file of ['app.js', 'governance.js', 'operations.js']) {
  vm.runInContext(readFileSync(new URL('../web_workbench/static/' + file, import.meta.url), 'utf8'), context);
}
vm.runInContext('state.boot = {options: {projects: [{value: 7, label: "原有项目选项"}]}}', context);
const operationOptions = vm.runInContext('operationOptions', context);
const operationField = vm.runInContext('operationField', context);
const entityRecordOption = vm.runInContext('entityRecordOption', context);
const field = {name:'ids', label:'业务记录', kind:'multi', source:'entity_records', required:true};
const definition = {module:'entities', fields:[field]};
const samples = [
  ['projects', {id:23, label:'合成零星项目', projects:'合成零星项目', date:'', amount_minor:null}, '#23 · 合成零星项目'],
  ['contracts', {id:9, label:'合成年度合同', projects:'合成零星项目', date:'2026-10-01', amount_minor:'10000'}, '#9 · 合成年度合同 · 合成零星项目 · 2026-10-01 · ¥100.00'],
  ['settlements', {id:10, label:'JS-SYNTH-001', projects:'合成零星项目', date:'2026-10-02', amount_minor:'7500000'}, '#10 · JS-SYNTH-001 · 合成零星项目 · 2026-10-02 · ¥75,000.00'],
  ['sales_invoices', {id:11, label:'SYNTH-INV-001', projects:'合成零星项目', date:'2026-10-03', amount_minor:'11301'}, '#11 · SYNTH-INV-001 · 合成零星项目 · 2026-10-03 · ¥113.01'],
  ['receipts', {id:12, label:'HK-SYNTH-001', projects:'合成零星项目', date:'2026-10-04', amount_minor:'100000'}, '#12 · HK-SYNTH-001 · 合成零星项目 · 2026-10-04 · ¥1,000.00'],
  ['purchase_orders', {id:13, label:'CG-SYNTH-001', projects:'合成零星项目', date:'2026-10-05', amount_minor:'14700'}, '#13 · CG-SYNTH-001 · 合成零星项目 · 2026-10-05 · ¥147.00'],
  ['cost_entries', {id:14, label:'CB-SYNTH-001', projects:'合成零星项目', date:'2026-10-06', amount_minor:'97860'}, '#14 · CB-SYNTH-001 · 合成零星项目 · 2026-10-06 · ¥978.60'],
  ['fund_accounts', {id:15, label:'合成现金账户', projects:'', date:'2026-10-01', amount_minor:'0'}, '#15 · 合成现金账户 · 2026-10-01 · ¥0.00'],
  ['fund_transactions', {id:16, label:'16', projects:'', date:'2026-10-07', amount_minor:'1000'}, '#16 · 16 · 2026-10-07 · ¥10.00'],
];
let reads = 0;
for (const [kind, row, expected] of samples) {
  context.api = async request => {
    reads++;
    assert.equal(request, 'operation_read?name=entity_records&kind=' + kind + '&assignable=1');
    return [row];
  };
  const options = await operationOptions(definition, {kind});
  assert.equal(options.entity_records[0].value, row.id);
  assert.equal(options.entity_records[0].label, expected);
  assert.equal(options.projects[0].label, '原有项目选项');
  const html = operationField(field, [row.id], options);
  assert(html.includes('value="' + row.id + '" selected'));
  assert(html.includes(expected));
}

context.api = async request => {
  assert.equal(request, 'operation_read?name=entity_records&kind=contracts&assignable=1');
  return [samples[0][1]];
};
assert.equal((await operationOptions(definition, {})).entity_records[0].label, '#23 · 合成零星项目');
assert.equal(entityRecordOption({id:2, label:'', projects:'', date:'', amount_minor:null}).label, '#2 · 名称待补充');
assert.equal(entityRecordOption({id:3, label:'大额', amount_minor:'9007199254740991'}).label, '#3 · 大额 · ¥90,071,992,547,409.91');
const hostile = entityRecordOption({id:4, label:'<script>合成测试</script>', projects:'项目 & 单据'});
const html = operationField(field, [], {entity_records:[hostile]});
assert(!html.includes('<script>'));
assert(html.includes('&lt;script&gt;合成测试&lt;/script&gt;') && html.includes('项目 &amp; 单据'));

context.api = async () => [];
assert.equal((await operationOptions(definition, {kind:'projects'})).entity_records.length, 0);
context.api = async () => [{id:8, order_no:'CG-UNASSIGNED-001'}];
const unassigned = await operationOptions({module:'governance', fields:[{source:'unassigned_purchases'}]}, {});
assert.match(unassigned.unassigned_purchases[0].label, /CG-UNASSIGNED-001/);
assert.match(unassigned.unassigned_purchases[0].label, /材料明细待补充/);
assert.match(unassigned.unassigned_purchases[0].label, /记录8/);
assert.equal(reads, samples.length);
console.log('PASS: all 9 entity record kinds, selected IDs, default kind, zero/unknown/large amounts, escaping, empty results and descriptive unassigned options.');
