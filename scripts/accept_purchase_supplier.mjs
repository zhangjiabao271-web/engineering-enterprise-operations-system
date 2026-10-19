import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [origin,outputArg]=process.argv.slice(2);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
const output=path.resolve(outputArg);
assert(output.startsWith(path.resolve('qa')+path.sep));
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({...(process.env.WORKBENCH_BROWSER ? {executablePath:process.env.WORKBENCH_BROWSER} : {}),headless:true});
const page=await browser.newPage({viewport:{width:1366,height:768},reducedMotion:'reduce'});
const checks=[],errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(!request.url().startsWith(origin))errors.push('Unexpected external request');});
const read=name=>page.evaluate(name=>api(name),name);
const idle=()=>page.waitForFunction(()=>!editorSubmitting&&main.getAttribute('aria-busy')==='false');
const quoteSelect=index=>`.purchase-item:nth-child(${index}) [name="item-product_id"]`;
async function choose(selector,keyword){
  await page.locator(selector+' + input').fill(keyword);
  await page.locator(selector+' ~ ul [data-choice-index]').first().click();
}
async function openNew(){
  await page.locator('#new-record').click();
  await page.locator('.purchase-item').first().waitFor();
}
async function close(){await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);}
async function matches(index,supplier,extra=[]){
  const actual=await page.locator(quoteSelect(index)).evaluate(select=>[...select.options].map(row=>row.value).filter(Boolean));
  const options=await read('options');
  const expected=options.offers.filter(row=>String(row.supplier_id)===String(supplier)).map(row=>String(row.value));
  assert.deepEqual(actual.sort(),[...expected,...extra.map(String)].sort());
}
try{
  await page.goto(origin+'/#purchases');await page.locator('#new-record').waitFor();await idle();
  assert(await page.evaluate(()=>state.boot.demo&&!state.boot.read_only));
  const before=(await read('qa_state')).digest;
  await openNew();
  assert(await page.locator(quoteSelect(1)).isDisabled());
  assert.equal(await page.locator(quoteSelect(1)+' option').count(),1);
  checks.push('未选供应商不显示全库报价');
  await choose('[name="supplier_id"]','钢材供应商');
  await matches(1,2);
  await choose(quoteSelect(1),'合成螺杆');
  await page.waitForFunction(()=>!editorLookups.size&&editor.querySelector('[name="item-unit_price"]').value==='9.70');
  await page.locator('#add-item').click();await matches(2,2);
  await choose(quoteSelect(2),'合成螺杆');
  await page.waitForFunction(()=>!editorLookups.size);
  await choose('[name="supplier_id"]','另一家五金');
  for(const index of [1,2]){
    await matches(index,3);
    assert.equal(await page.locator(quoteSelect(index)).inputValue(),'');
    assert.equal(await page.locator(`.purchase-item:nth-child(${index}) [name="item-unit_price"]`).inputValue(),'9.70');
  }
  await page.locator('#add-item').click();await matches(3,3);
  checks.push('切换供应商更新全部材料行，清除错配关联但不清空成交输入；新增行沿用当前供应商');
  await choose('[name="supplier_id"]','暂无报价');
  for(const index of [1,2,3])assert(await page.locator(quoteSelect(index)).isDisabled());
  assert(!await page.locator('.purchase-item').first().locator('[name="item-material_name_snapshot"]').isDisabled());
  assert((await page.locator('#editor-body').innerText()).includes('暂无材料报价'));
  checks.push('无报价供应商可直接填写材料，不混入别家报价');
  await choose('[name="supplier_id"]','钢材供应商');
  let release,started=false;
  const held=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/record?module=offers&id=2',async route=>{started=true;await held;await route.continue();});
  await choose(quoteSelect(1),'合成螺杆');
  await page.waitForFunction(()=>editorLookups.size>0);
  assert(started);
  await choose('[name="supplier_id"]','另一家五金');
  const price=await page.locator('.purchase-item').first().locator('[name="item-unit_price"]').inputValue();
  release();await page.waitForTimeout(200);
  assert.equal(await page.locator('.purchase-item').first().locator('[name="item-unit_price"]').inputValue(),price);
  assert.equal(await page.locator(quoteSelect(1)).inputValue(),'');
  await page.unroute('**/api/record?module=offers&id=2');
  checks.push('迟到的旧供应商报价不能填回切换后的材料行');
  await close();assert.equal((await read('qa_state')).digest,before);

  await page.locator('[data-action="edit"][data-id="2"]').click();
  await page.locator('.purchase-item').first().waitFor();
  assert.equal(await page.locator(quoteSelect(1)).inputValue(),'4');
  assert.equal(await page.locator('.purchase-item').first().locator('[name="item-unit_price"]').inputValue(),'8.88');
  await matches(1,2,[4]);
  await page.locator('#add-item').click();await matches(2,2);
  await close();assert.equal((await read('qa_state')).digest,before);
  checks.push('编辑保留停用的原报价及历史成交价，新增材料不包含停用报价；取消不改账');

  let legacyReads=0;
  page.on('request',request=>{if(request.url().endsWith('/api/rows?module=offers'))legacyReads++;});
  await page.route('**/api/options?**',async route=>{
    const response=await route.fetch();const data=await response.json();
    for(const offer of data.offers)delete offer.supplier_id;
    await route.fulfill({response,json:data});
  });
  await openNew();await choose('[name="supplier_id"]','钢材供应商');await matches(1,2);
  assert(legacyReads>0);await close();await page.unroute('**/api/options?**');
  checks.push('已运行的旧后端也能按供应商编号筛选，无需强制重启');

  const count=(await read('rows?module=purchases')).rows.length;
  await openNew();
  await choose('[name="project_id"]','厂房钢结构');
  await choose('[name="supplier_id"]','另一家五金');
  await choose(quoteSelect(1),'合成螺杆');
  await page.waitForFunction(()=>!editorLookups.size&&editor.querySelector('[name="item-unit_price"]').value==='17.00');
  await page.locator('[name="item-quantity"]').fill('2');
  await page.locator('[name="notes"]').fill('合成供应商联动保存');
  await page.locator('[name="continue-entry"]').check();await page.locator('[name="confirmed"]').check();
  await page.waitForFunction(()=>editor.querySelector('#purchase-preview').textContent.includes('34.00'));
  await page.locator('#editor-save').click();
  await page.waitForFunction(()=>editor.open&&!editorSubmitting&&form.elements.supplier_id.value==='3'&&editor.querySelector('[name="item-material_name_snapshot"]').value==='');
  await matches(1,3);assert.equal(await page.locator(quoteSelect(1)).inputValue(),'');
  const orders=(await read('rows?module=purchases')).rows;
  assert.equal(orders.length,count+1);
  const saved=orders.find(row=>row.notes==='合成供应商联动保存');
  const record=await read('record?module=purchases&id='+saved.id);
  assert.equal(record.supplier_id,3);assert.equal(record.items[0].supplier_offer_id,3);
  assert.equal(record.total_amount_cents,'3400');
  await page.screenshot({path:path.join(output,'filtered_purchase.png')});
  await close();assert.equal((await read('rows?module=purchases')).rows.length,count+1);
  checks.push('实际保存对应供应商和材料；继续录入保持供应商过滤，不重复生成空单');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack);await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});}
finally{await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify({checks,errors},null,2));await browser.close();}
console.log(JSON.stringify({checks:checks.length,errors,output},null,2));
assert.deepEqual(errors,[]);
