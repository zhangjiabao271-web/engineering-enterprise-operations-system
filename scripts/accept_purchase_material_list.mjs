// Visible list/search/export acceptance on a disposable synthetic ledger only.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [origin,folder]=process.argv.slice(2);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
const output=path.resolve(folder);
assert(output.startsWith(path.resolve('qa')+path.sep));
await fs.mkdir(output);
const browser=await chromium.launch({...(process.env.WORKBENCH_BROWSER ? {executablePath:process.env.WORKBENCH_BROWSER} : {}),headless:true});
const page=await browser.newPage({viewport:{width:1366,height:768},reducedMotion:'reduce'});
const checks=[],errors=[];
page.on('pageerror',error=>errors.push(error.message));
const idle=()=>page.waitForFunction(()=>main.getAttribute('aria-busy')==='false');
async function write(record){
  return page.evaluate(async record=>{
    if(!state.boot.demo||state.boot.read_only)throw new Error('Synthetic fixture required');
    const ticket=await api('write_ticket?'+query({kind:'save',module:'purchases'}));
    return api('save',{module:'purchases',record,form_ticket:ticket.token,confirmed:true});
  },record);
}
try{
  await page.goto(origin);await page.waitForFunction(()=>state.boot&&main.getAttribute('aria-busy')==='false');
  assert(await page.evaluate(()=>state.boot.demo&&!state.boot.read_only));
  await page.evaluate(()=>api('qa_fixture',{name:'inspection'}));
  await page.reload();await idle();
  const items=[
    {material_name_snapshot:'割枪',specification_snapshot:'G01-100',quantity:'1',unit_snapshot:'把',unit_price:'68',price_basis:'inclusive'},
    {material_name_snapshot:'气割接头',specification_snapshot:'M12',quantity:'5',unit_snapshot:'只',unit_price:'3',price_basis:'inclusive'},
    {material_name_snapshot:'调压阀',specification_snapshot:'氧气专用',quantity:'1',unit_snapshot:'只',unit_price:'64',price_basis:'inclusive'},
  ];
  const common={project_id:1,purchase_date:'2026-10-13',purchase_type:'零星采购',merchant_name_snapshot:'合成五金店',payment_status:'已付款',payment_method:'现金'};
  await write({...common,order_no:'SYNTHETIC-MATERIAL-BATCH',items});
  await write({...common,order_no:'SYNTHETIC-MATERIAL-SINGLE',items:[{material_name_snapshot:'410不锈钢螺丝',specification_snapshot:'5.5×25',quantity:'6800',unit_snapshot:'颗',unit_price:'0.08',price_basis:'inclusive'}]});
  await write({...common,order_no:'SYNTHETIC-MATERIAL-LONG',items:[{...items[0],material_name_snapshot:'长名称'+ '不截断钢结构维修材料'.repeat(12)}]});
  await write({...common,order_no:'SYNTHETIC-MATERIAL-ESCAPE',items:[{...items[0],material_name_snapshot:'<img src=x onerror=alert(1)>',specification_snapshot:'<script>禁止执行</script>'}]});
  await page.evaluate(()=>location.hash='purchases');await page.waitForFunction(()=>state.route==='purchases'&&main.getAttribute('aria-busy')==='false');
  const before=await page.evaluate(async()=>(await api('qa_state')).digest);
  const headings=await page.locator('#records th').allTextContents();
  assert.deepEqual(headings.slice(0,2),['采购日期','材料名称 / 规格']);
  assert.equal(headings.at(-2),'采购单号');
  const batch=page.locator('#records tbody tr').filter({hasText:'SYNTHETIC-MATERIAL-BATCH'});
  assert.equal(await batch.locator('[data-column="material_details"]').innerText(),'割枪 · G01-100\n气割接头 · M12\n调压阀 · 氧气专用');
  assert.equal(await batch.locator('[data-column="total_amount_cents"]').innerText(),'¥147.00');
  assert.match(await page.locator('#records tbody tr').filter({hasText:'SYNTHETIC-MATERIAL-SINGLE'}).innerText(),/410不锈钢螺丝 · 5.5×25/);
  assert.equal(await page.locator('#records tbody [data-column="material_details"] img, #records tbody [data-column="material_details"] script').count(),0);
  checks.push('单材料及多材料逐项显示成交名称和规格，含税总额不变，单号退居后列，特殊字符安全转义');
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  for(const viewport of [{width:960,height:720},{width:390,height:844}]){
    await page.setViewportSize(viewport);
    const geometry=await batch.locator('[data-column="material_details"]').evaluate(cell=>({
      height:cell.clientHeight,scrollHeight:cell.scrollHeight,whiteSpace:getComputedStyle(cell).whiteSpace,
    }));
    assert.equal(geometry.whiteSpace,'pre-line');assert(geometry.scrollHeight<=geometry.height+1);
    assert(await page.locator('.table-wrap').evaluate(wrap=>wrap.scrollWidth>wrap.clientWidth));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,viewport.width===390?'mobile.png':'compact.png'),fullPage:true});
  }
  checks.push('窄桌面及手机材料完整换行，宽表仅在表内横向滚动');
  await page.setViewportSize({width:1366,height:768});
  await page.locator('#record-search').fill('M12');await page.locator('#search-records').click();
  await page.waitForFunction(()=>state.keyword==='M12'&&main.getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('#records tbody tr').count(),1);
  assert.match(await batch.innerText(),/割枪.*G01-100/s);assert.match(await batch.innerText(),/调压阀.*氧气专用/s);
  checks.push('按任意材料规格搜索返回完整采购单，不丢失同单其他材料');
  const downloaded=page.waitForEvent('download');await page.locator('#export-csv').click();
  const download=await downloaded;const file=path.join(output,'synthetic-materials.csv');await download.saveAs(file);
  const csv=await fs.readFile(file,'utf8');
  assert.match(csv,/材料名称 \/ 规格/);assert.match(csv,/割枪 · G01-100\n气割接头 · M12\n调压阀 · 氧气专用/);
  checks.push('导出台账保留全部材料和规格，不需要逐单打开');
  assert.equal(await page.evaluate(async()=>(await api('qa_state')).digest),before);
  checks.push('列表、搜索及导出均不写账');assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack);}
finally{await fs.writeFile(path.join(output,'results.json'),JSON.stringify({synthetic:true,checks,errors},null,2));await browser.close();}
console.log(JSON.stringify({synthetic:true,checks,errors},null,2));
if(errors.length)process.exitCode=1;
