// Live interaction acceptance, only on the explicitly generated synthetic ledger.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [origin,folder]=process.argv.slice(2);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
const root=path.resolve(folder);
assert(root.startsWith(path.resolve('qa')+path.sep));
const fixture=JSON.parse(await fs.readFile(path.join(root,'fixture.json'),'utf8'));
assert(fixture.synthetic && fixture.url===origin);
const output=path.join(root,'browser');
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({...(process.env.WORKBENCH_BROWSER ? {executablePath:process.env.WORKBENCH_BROWSER} : {}),headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const checks=[],errors=[];
page.on('pageerror',error=>errors.push(error.message));
const idle=()=>page.waitForFunction(()=>typeof state!=='undefined' && main.getAttribute('aria-busy')==='false');
const check=label=>checks.push({label,status:'passed'});
const close=async()=>{await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);};
const selectScope=async scope=>{await page.locator('#contract-business-scope').selectOption(scope);await idle();};
try {
  await page.goto(origin+'/#contracts');await idle();
  assert.equal(await page.evaluate(()=>state.boot.demo),true);
  const before=await page.evaluate(()=>api('qa_state'));
  const data=await page.evaluate(()=>api('contract_ledger'));
  assert.equal(data.business_scope,'all');
  assert.equal(BigInt(data.summary.settlement_minor),27510000n);
  assert.equal(BigInt(data.summary.receipt_minor),21510000n);
  assert.equal(BigInt(data.summary.receivable_minor),10000000n);
  assert.equal(BigInt(data.project_business_summary.settlement_minor),17510000n);
  assert.equal(data.contract_count,4);
  assert.equal(data.project_business_count,3);
  assert.equal(await page.locator('#contract-year').count(),0);
  assert.match(await page.locator('.contract-summary').innerText(),/275,100\.00/);
  check('顶部与客户总览含现金工程，约定金额不虚增收入，预收不抵其他项目欠款');
  await page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:false});
  for(const item of fixture.cash_projects){
    const row=page.locator('#contract-customers tbody tr').filter({has:page.locator(`[data-customer="customer:${item.customer}"]`)});
    assert.match(await row.innerText(),new RegExp(Number(item.expected_receipt).toLocaleString('en-US')+'\\.00'));
    assert.match(await row.innerText(),/零星现金工程/);
  }
  for(const kind of ['settlements','receipts','invoices','outstanding']){
    await page.locator(`[data-business-detail="${kind}"]`).click();
    await page.locator('#business-facts').waitFor();
    const sum=await page.evaluate(kind=>api('contract_business_detail').then(data=>({
      reconciled:data.details[kind].reconciled,
      total:data.details[kind].total_minor,
      rows:data.details[kind].rows.reduce((sum,row)=>sum+BigInt(row[data.details[kind].amount_key]),0n).toString(),
    })),kind);
    assert(sum.reconciled);assert.equal(sum.total,sum.rows);
    await close();
  }
  check('顶部四项都可点击，逐笔合计与当前范围一致');
  const prepaid=fixture.cash_projects[2];
  await page.locator(`.contract-name-link[data-customer="customer:${prepaid.customer}"]`).click();
  await page.waitForURL(new RegExp('customer=customer%3A'+prepaid.customer));await idle();
  await page.locator('#project-business-records tbody tr').waitFor();
  assert.equal(await page.locator('#project-business-records tbody tr').count(),1);
  assert.match(await page.locator('#project-business-records').innerText(),/40,000\.00/);
  await page.locator(`[data-project-business-id="${prepaid.project}"][data-kind="receipts"]`).first().click();
  await page.locator('#business-facts').waitFor();
  assert.match(await page.locator('#business-facts').innerText(),/40,000\.00/);
  await page.locator('[data-business-fact]').first().click();
  await page.locator('#record-detail-back').click();await page.locator('#business-facts').waitFor();
  await close();
  await page.locator('#contract-customers-back').click();await page.waitForURL('**/#contracts');await idle();
  await page.locator('#contract-customers').waitFor();
  check('预收单独显示，点入未关联合同部分后可返回原回款和客户列表');
  await selectScope('contracts');
  assert.match(await page.locator('.contract-summary').innerText(),/100,000\.00/);
  assert.equal(await page.locator('#contract-year').count(),1);
  await page.locator('#contract-year').selectOption('2026');await idle();
  await selectScope('uncontracted');
  assert.equal(await page.locator('#contract-year').count(),0);
  assert.match(await page.locator('.contract-summary').innerText(),/175,100\.00/);
  assert.match(await page.locator('.contract-summary').innerText(),/215,100\.00/);
  check('合同签年独立筛选，现金/未签范围不受旧签约年暗中影响');
  await page.locator('#record-search').fill('现金项目乙');
  await page.locator('#search-records').click();await idle();
  assert.equal(await page.locator('#contract-customers tbody tr').count(),1);
  assert.match(await page.locator('.contract-summary').innerText(),/97,100\.00/);
  await page.locator('[data-business-detail="receipts"]').click();await page.locator('#business-facts').waitFor();
  assert.equal(await page.locator('#business-facts tbody tr').count(),2);
  await close();
  check('按项目名称搜索后顶部与明细同步收窄，不串入同客户其他合同');
  await page.locator('#contract-clear').click();await idle();
  await page.locator('#contract-entity').selectOption('2');await idle();
  assert.match(await page.locator('.contract-summary').innerText(),/175,100\.00/);
  await page.locator(`.contract-name-link[data-customer="customer:${prepaid.customer}"]`).click();
  await page.locator('#project-business-records tbody tr').waitFor();
  const scopedRequest=page.waitForResponse(response=>response.url().includes('/api/contract_project_business?'));
  await page.locator(`[data-project-business-id="${prepaid.project}"][data-kind="receipts"]`).first().click();
  assert.equal(new URL((await scopedRequest).url()).searchParams.get('entity_id'),'2');
  await page.locator('#business-facts').waitFor();
  assert.match(await page.locator('#business-facts').innerText(),/归属当前范围/);
  await page.locator('[data-business-fact]').first().click();
  const returnRequest=page.waitForResponse(response=>response.url().includes('/api/contract_project_business?'));
  await page.locator('#record-detail-back').click();
  assert.equal(new URL((await returnRequest).url()).searchParams.get('entity_id'),'2');
  await page.locator('#business-facts').waitFor();await close();
  await page.locator('#contract-customers-back').click();await page.locator('#contract-customers').waitFor();
  check('经营主体筛选同时作用于现金业务与合同');
  await page.setViewportSize({width:390,height:844});
  const bounds=await page.evaluate(()=>({width:innerWidth,body:document.documentElement.scrollWidth}));
  assert(bounds.body<=bounds.width+1,JSON.stringify(bounds));
  await page.screenshot({path:path.join(output,'overview-mobile.png'),fullPage:true});
  await page.locator('[data-business-detail="receipts"]').click();await page.locator('#business-facts').waitFor();
  assert(await page.locator('#editor-cancel').isVisible());
  await page.screenshot({path:path.join(output,'receipts-mobile.png'),fullPage:false});
  await close();
  check('窄屏金额与返回可触达，宽明细只在表内横滚');
  assert.equal((await page.evaluate(()=>api('qa_state'))).digest,before.digest);
  assert.deepEqual(errors,[]);
  check('查看和返回不改变账本，无浏览器异常');
} catch(error) {
  errors.push(error.message);
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:false});
  throw error;
} finally {
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({checks,errors},null,2));
  await browser.close();
}
console.log(JSON.stringify({checks,errors},null,2));
