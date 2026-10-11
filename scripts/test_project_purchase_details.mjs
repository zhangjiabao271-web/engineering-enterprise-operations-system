// Read-only procurement rendering: original price facts versus allocated costs.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    open:false, classList:{remove(){}}, setAttribute(){}, addEventListener(){},
    querySelectorAll(){return [];}, querySelector(child){return element(selector+' '+child);},
    showModal(){this.open=true;},
  });
  return elements.get(selector);
};
const context = vm.createContext({
  document:{querySelector:element,addEventListener(){}},window:{addEventListener(){}},
  enhanceForms(){},WorkbenchMotion:{openDialog(){}},
  fieldLabel:key=>key,valueLabel:value=>String(value),
});
for (const name of ['app','operations']) {
  vm.runInContext(readFileSync(new URL(`../web_workbench/static/${name}.js`,import.meta.url),'utf8'),context);
}
const render = vm.runInContext('projectPurchaseTable',context);
const price = vm.runInContext('projectPurchasePrice',context);
const common = {date:'2026-10-10',source_no:'LS-20261010-001',counterparty:'合成供应商',
  description:'彩钢瓦',specification:'840型',quantity:996,unit:'米',settlement_mode:'quantity',
  price_basis:'inclusive',unit_price_cents:'189',tax_rate_bps:900,line_material_minor:'172701',
  line_tax_minor:'15543',line_amount_minor:'188244',amount_minor:'94122',
  payment_status:'已付款',allocation_method:'equal'};
let html = render([common]);
assert.match(html, /¥1\.89\/米/);
assert.match(html, /996 米/);
assert.match(html, /含税 · 税率 9%/);
assert.match(html, /¥1,882\.44/);
assert.match(html, /¥941\.22/);
assert.match(html, /多项目分摊/);
assert.match(html, /<summary>查看明细<\/summary>/);
assert.match(html, /LS-20261010-001/);
assert.match(html, /<th scope="col">材料 \/ 规格<\/th><th scope="col">数量 \/ 重量<\/th><th scope="col">单价<\/th>/);

assert.equal(price({...common,settlement_mode:'weight',unit:'吨',unit_price_yuan:'3568.1234'}), '¥3,568.1234/吨');
assert.equal(price({...common,settlement_mode:'weight',unit:'公斤',unit_price_yuan:'0.048'}), '¥0.048/公斤');
assert.equal(price({...common,settlement_mode:'total',unit_price_cents:null}), '整批结算');
assert.equal(price({...common,settlement_mode:'freight'}), '—');
assert.equal(price({...common,unit_price_cents:0}), '¥0.00/米');
assert.match(render([{...common,price_basis:'exclusive',unit_price_cents:970}]), /未税 · 税率 9%/);
assert.match(render([{...common,amount_minor:'900719925474099301'}]), /¥9,007,199,254,740,993\.01/);
html = render([{...common,description:'<img src=x onerror=alert(1)>',specification:'<script>x</script>',
  counterparty:'<b>对方</b>',source_no:'<input>',purpose:'<iframe>',weigh_ticket_no:'<svg>'}]);
for (const tag of ['img','script','b','input','iframe','svg']) assert(!html.includes(`<${tag}`));
assert.match(html, /&lt;img/);
assert.match(render([]), /暂无/);

const data = {project:{name:'合成项目',project_code:'DEMO-001'},purchase:[common],labor:[],other:[],
  totals:{purchase:94122,labor:0,other:0},total_cost_minor:94122};
vm.runInContext('openProjectCostDetails',context)(data);
assert.match(element('#project-cost-rows').innerHTML, /¥1\.89\/米/);
assert.equal(element('#editor-cancel').textContent,'关闭');
assert.equal(element('#editor .dialog-head .eyebrow').textContent,'项目全周期成本 · 只读明细');
console.log('PASS: prices, units, tax basis, line/share amounts, exact weight prices, freight, totals, escaping and empty state.');
