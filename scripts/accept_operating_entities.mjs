import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [origin,outputArg]=process.argv.slice(2);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin),'Only loopback synthetic fixtures are allowed');
const output=path.resolve(outputArg);
assert(output.startsWith(path.resolve('qa')+path.sep),'Output must be inside qa');
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({...(process.env.WORKBENCH_BROWSER ? {executablePath:process.env.WORKBENCH_BROWSER} : {}),
  headless:true,args:['--disable-background-networking','--no-first-run']});
const context=await browser.newContext({viewport:{width:1366,height:768},reducedMotion:'reduce'});
const page=await context.newPage();
const errors=[],external=[],checks=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(!request.url().startsWith(origin)&&!request.url().startsWith('data:'))external.push(request.url());});
const check=(label)=>checks.push({label,status:'passed'});
const idle=()=>page.waitForFunction(()=>main.getAttribute('aria-busy')==='false'&&!editorSubmitting);
const read=async name=>page.evaluate(name=>api(name),name);
const close=async()=>{await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);};
async function registry(kind,status='all',search=''){
  await page.locator('#entity-kind').selectOption(kind);
  await page.waitForFunction(kind=>entityRegistryView.kind===kind&&entityRecords.every(row=>row.kind===kind)&&!document.querySelector('#entity-registry-records .loading'),kind);
  await page.locator('#entity-status').selectOption(status);
  await page.locator('#entity-search').fill(search);
}
async function assignment(identity,reason='合成验收确认主体'){
  await page.locator(`[data-entity-assign="${identity}"]`).click();
  await page.locator('select[name="entity_id"]').selectOption('1');
  await page.locator('textarea[name="reason"]').fill(reason);
  await page.locator('#editor-save').click();
  await page.waitForFunction(()=>document.querySelector('#dialog-title').textContent==='确认经营主体 · 预览'&&!editorSubmitting);
}
async function unclipped(label){
  const issues=await page.evaluate(()=>{
    const found=[];
    const selectors=['.entity-company h2','.entity-value>strong','.entity-value>span','.entity-income-legend>button>span'];
    for(const item of document.querySelectorAll(selectors.join(','))){
      if(!item.getClientRects().length)continue;
      if(item.scrollWidth>item.clientWidth+2||item.scrollHeight>item.clientHeight+2)found.push(item.textContent);
    }
    if(document.documentElement.scrollWidth>innerWidth+2)found.push('page horizontal overflow');
    return found;
  });
  assert.deepEqual(issues,[],label+': clipped content');
  check(label);
}

try{
  await page.goto(origin+'/#entities');
  await page.locator('#entity-kind').waitFor();
  await idle();
  assert(await page.evaluate(()=>state.boot.demo&&!state.boot.read_only));
  const before=await read('qa_state');
  assert.equal((await read('operation_read?name=entity_overview')).pending_metrics.income_minor,'10000000');
  await registry('contracts','pending','钢结构施工合同');
  await assignment(1,'合成取消确认，不应写入');
  assert(await page.locator('#entity-project-preview').getByText('合成演示 · 厂房钢结构工程',{exact:true}).isVisible());
  assert(!await page.locator('input[name="entity-confirmed"]').isChecked());
  await close();
  assert.equal((await read('qa_state')).digest,before.digest);
  check('Preview shows derived project impact; cancellation leaves the ledger unchanged');

  await assignment(1);
  await page.locator('input[name="entity-confirmed"]').check();
  await page.locator('#editor-save').click();
  await page.waitForFunction(()=>!editor.open&&!editorSubmitting&&main.getAttribute('aria-busy')==='false');
  await page.locator('#entity-registry-records .empty').waitFor();
  assert((await page.locator('#toast').innerText()).includes('项目已自动沿用'));
  const overview=await read('operation_read?name=entity_overview');
  assert.equal(overview.entities[0].metrics.income_minor,'10000000');
  assert.equal(overview.pending_metrics.income_minor,'0');
  await page.locator('#entity-status').selectOption('confirmed');
  assert((await page.locator('.entity-registry-table').innerText()).includes('已确认'));
  assert((await page.locator('.entity-registry-table').innerText()).includes('示例钢结构有限公司'));
  check('Applying refreshes dashboard and pending queue; confirmed records remain visible');

  await page.locator('[data-entity-history="1"]').click();
  assert((await page.locator('#editor-body').innerText()).includes('合成验收确认主体'));
  await close();
  await registry('projects','inherited');
  assert((await page.locator('.entity-registry-table').innerText()).includes('已自动归属'));
  assert.equal(await page.locator('[data-entity-assign="1"]').count(),0);
  await page.locator('[data-entity-contracts="1"]').click();
  assert((await page.locator('#entity-linked-contracts').innerText()).includes('示例钢结构有限公司'));
  await close();
  check('Linked projects inherit contracts and offer contract management instead of redundant confirmation');

  await registry('cost_entries','pending');
  assert((await page.locator('.entity-registry-table').innerText()).includes('多主体项目分摊'));
  assert(!(await page.locator('.entity-registry-table').innerText()).includes('归属冲突'));
  check('Shared costs are pending actual-buyer confirmation, not false ownership conflicts');

  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(output,'01_desktop_overview.png')});
  await unclipped('Desktop company names and metric amounts are not clipped');
  for(const metric of ['income','receipts','outstanding','costs','margin','invoices','output_tax','input_gross','input_tax','deducted_tax']){
    if(['output_tax','input_gross','input_tax','deducted_tax'].includes(metric)){
      const details=page.locator('[data-company="1"] .entity-tax-details');
      if(!await details.evaluate(item=>item.open))await details.locator('summary').click();
    }
    const value=overview.entities[0].metrics[metric+'_minor'];
    await page.locator(`[data-company="1"] [data-entity-metric="${metric}"]`).click();
    await page.locator('#entity-detail-rows').waitFor();
    const detail=await read(`operation_read?name=entity_detail&metric=${metric}&entity_id=1`);
    assert.equal(detail.total_minor,value,metric+' totals reconcile');
    assert.equal(detail.rows.reduce((sum,row)=>sum+BigInt(row.amount_minor),0n),BigInt(value));
    if(metric==='income'){
      await page.locator('[data-entity-detail-project="1"]').click();
      await page.locator('#ledger-close').click();
      await page.locator('#entity-detail-rows').waitFor();
      await page.locator('#entity-detail-search').fill('没有该项目');
      assert((await page.locator('#entity-detail-count').innerText()).includes('当前匹配 0 条'));
    }
    await close();
  }
  check('All ten dashboard metrics drill down to exact totals; project navigation and detail search return correctly');

  await registry('contracts','pending','分页验收合同');
  assert((await page.locator('#entity-registry-count').innerText()).includes('54 条'));
  await page.locator('#entity-next').click();
  assert.equal(await page.locator('[data-entity-select]').count(),4);
  await page.locator('#entity-previous').click();
  await page.locator('#entity-select-all').click();
  assert((await page.locator('#entity-selection-count').innerText()).includes('54 条'));
  await page.locator('#entity-clear-selection').click();
  await page.locator('[data-entity-select]').nth(0).check();
  await page.locator('[data-entity-select]').nth(1).check();
  await page.locator('#entity-confirm-selected').click();
  await page.locator('select[name="entity_id"]').selectOption('1');
  await page.locator('textarea[name="reason"]').fill('合成批量确认两份合同');
  await page.locator('#editor-save').click();
  await page.locator('#entity-assignment-preview').waitFor();
  await page.locator('input[name="entity-confirmed"]').check();
  await page.locator('#editor-save').click();
  await page.waitForFunction(()=>!editor.open&&!editorSubmitting&&main.getAttribute('aria-busy')==='false');
  await page.waitForFunction(()=>document.querySelector('#entity-registry-count')?.textContent.includes('52 条'));
  check('Pagination, select all, clear and batch confirmation update the selected records only');

  await page.route('**/api/operation_read?**',async route=>{
    const url=new URL(route.request().url());
    if(url.searchParams.get('name')==='entity_records'&&url.searchParams.get('kind')==='contracts'){
      await new Promise(resolve=>setTimeout(resolve,350));
    }
    await route.continue();
  });
  await page.locator('#entity-kind').selectOption('projects');
  await page.locator('#entity-kind').selectOption('contracts');
  await page.locator('#entity-kind').selectOption('projects');
  await page.locator('#entity-status').selectOption('all');
  await page.locator('#entity-search').fill('');
  await page.waitForFunction(()=>entityRegistryView.kind==='projects'&&entityRecords.length===4&&entityRecords.every(row=>row.kind==='projects'));
  await page.waitForTimeout(450);
  assert(await page.evaluate(()=>entityRecords.every(row=>row.kind==='projects')&&entitySelected.size===0));
  await page.unroute('**/api/operation_read?**');
  check('Delayed record-type responses cannot replace the latest type or leave stale selections');

  await page.goto(origin+'/#projects');
  await idle();
  await page.goto(origin+'/#entities');
  await page.locator('#entity-kind').waitFor();await idle();
  await registry('projects','all');
  assert.equal(await page.locator('[data-entity-select="1"]').isDisabled(),true);
  check('Navigating away and back preserves a functioning registry and inherited project protection');

  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.scrollTo(0,0));
  await unclipped('Mobile layout has no page overflow, clipped company names or metric amounts');
  await page.screenshot({path:path.join(output,'02_mobile_overview.png'),fullPage:true});
  await page.locator('[data-company="1"] [data-entity-metric="income"]').click();
  await page.locator('#entity-detail-rows').waitFor();
  await page.screenshot({path:path.join(output,'03_mobile_detail.png')});
  await close();
  check('Mobile cards open scrollable detail dialogs');
  assert.deepEqual(errors,[],'No browser JavaScript errors');
  assert.deepEqual(external,[],'No data sent to external hosts');
}catch(error){
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});
  errors.push(error.stack);
}finally{
  await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify({checks,errors,external},null,2));
  await browser.close();
}
console.log(JSON.stringify({checks:checks.length,errors,output},null,2));
assert.deepEqual(errors,[],'Entity UI acceptance failed');
