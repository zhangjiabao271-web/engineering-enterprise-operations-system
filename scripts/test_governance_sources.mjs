import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const host={innerHTML:'',addEventListener(){},querySelectorAll(){return [];},
  insertAdjacentHTML(_position,html){this.innerHTML+=html;}};
const context=vm.createContext({console,setTimeout,clearTimeout,URLSearchParams,
  document:{querySelector(){return host;},addEventListener(){}},window:{addEventListener(){}}});
for(const file of ['labels.js','app.js','operations.js','governance.js']) {
  vm.runInContext(readFileSync(new URL('../web_workbench/static/'+file,import.meta.url),'utf8'),context);
}
const details=vm.runInContext('governanceMaterialDetails',context);
const option=vm.runInContext('governanceRecordOption',context);
const table=vm.runInContext('governanceSourceTable',context);
const row={id:3,order_no:'CG-SYNTHETIC-3',purchase_date:'2026-10-14',supplier_name:'合成五金店',
  amount_minor:'9058',freight_amount_cents:'1000',payment_status:'已付款',notes:'合成说明',
  material_details:'<script>合成螺杆</script> · M24\n合成螺帽 · M24',items:[
    {material_name_snapshot:'<script>合成螺杆</script>',specification_snapshot:'M24',quantity:12,
     unit_snapshot:'支',material_unit_price_cents:'550',price_basis:'exclusive',line_amount_cents:'7458'},
    {material_name_snapshot:'合成螺帽',specification_snapshot:'M24',quantity:3,unit_snapshot:'只',
     tax_inclusive_unit_price_cents:'200',price_basis:'inclusive',line_amount_cents:'600'},
  ]};
let text=details(row);
assert(text.includes('12 支')&&text.includes('3 只')&&text.includes('¥5.50/支'));
assert(text.includes('¥74.58')&&text.includes('整单另含运费：¥10.00'));
let html=table([row],'purchases');
assert(!html.includes('<script>'));
assert(html.includes('&lt;script&gt;')&&html.includes('¥90.58'));
assert(html.includes('data-column="amount_minor"')&&!html.includes('data-column="total_amount_cents"'));
text=details({...row,items:[{material_name_snapshot:'合成钢板',settlement_mode:'weight',net_weight:'628',
  weight_unit:'kg',weight_unit_price:'3.6805',price_basis:'inclusive',line_amount_cents:'231135'}]});
assert(text.includes('628 kg')&&text.includes('¥3.6805/kg'));
text=details({...row,items:[{material_name_snapshot:'合成油漆',settlement_mode:'total',quantity:1,
  price_basis:'inclusive',line_amount_cents:'20000'}]});
assert(text.includes('整批结算')&&!text.includes('1 批'));
const choice=option(row,'unassigned_purchases');
assert(choice.label.includes('2026-10-14')&&choice.label.includes('合成五金店')&&choice.label.includes('M24')&&choice.label.includes('¥90.58'));
assert(option({id:2,work_date:'2026-10-14',worker_name:'合成工人',construction_site:'合成厂区',work_days:0.5,amount_minor:15000},'unassigned_labor').label.includes('0.5 工'));
vm.runInContext("state.boot={read_only:true,schema:{purchases:{attachment:'purchase'},worklogs:{}}};",context);
vm.runInContext('renderGovernance',context)({summary:{purchase_record_count:1,purchase_amount_minor:'9058',labor_record_count:0,labor_amount_minor:0},
  unassigned_purchases:[row],unassigned_labor:[],projects:[{id:1,name:'合成真实项目名称',project_code:'DEMO-1'}]});
assert(host.innerHTML.includes('合成真实项目名称'));
assert(host.innerHTML.includes('查看原单')&&host.innerHTML.includes('附件')&&!host.innerHTML.includes('data-governance-assign'));
assert(!host.innerHTML.includes('id="records"'));
console.log('PASS: governance amounts, material/quantity/price details, exact weights, source labels, project names, readonly actions and escaping.');
