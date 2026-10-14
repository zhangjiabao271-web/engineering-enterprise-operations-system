import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('playwright');
const [origin,folder]=process.argv.slice(2);
assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
const output=path.resolve(folder);assert(output.startsWith(path.resolve('qa')+path.sep));
await fs.mkdir(output);
const browser=await chromium.launch({headless:true,...(process.env.WORKBENCH_BROWSER?{executablePath:process.env.WORKBENCH_BROWSER}:{})});
const page=await browser.newPage({viewport:{width:1366,height:768},reducedMotion:'reduce'});
const errors=[],checks=[];page.on('pageerror',error=>errors.push(error.message));
const read=route=>page.evaluate(route=>api(route),route);
const idle=()=>page.waitForFunction(()=>main.getAttribute('aria-busy')==='false'&&!editorSubmitting);
const close=async()=>{await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);};
async function chooseProject(){
  await page.locator('[name="project_id"] + input').fill('厂房钢结构');
  await page.locator('[name="project_id"] ~ ul [data-choice-index]').first().click();
}
try{
  await page.goto(origin+'/#governance');await idle();
  assert(await page.evaluate(()=>state.boot.demo));
  const before=await read('qa_state');assert(before.synthetic);
  const data=await read('dashboard?view=governance');
  assert.equal(Number(data.summary.purchase_record_count),1);
  const source=page.locator('[data-governance-section="purchases"]');
  const text=await source.innerText();
  for(const value of ['合成五金店','合成螺杆','合成螺帽','M24','12 支','3 只','¥90.58'])assert(text.includes(value),value);
  assert(!text.includes('合成已均摊电焊机'));
  assert.match(await page.locator('#governance-projects').innerText(),/厂房钢结构/);
  await source.locator('[data-governance-detail]').click();
  await page.waitForFunction(()=>editor.open&&document.querySelector('#dialog-title').textContent.includes('原始记录'));
  assert.match(await page.locator('#editor-body').innerText(),/合成螺杆/);
  await page.locator('#record-detail-back').click();assert.equal(await page.evaluate(()=>editor.open),false);
  await source.locator('[data-governance-attachment]').click();await page.locator('#attachment-history').waitFor();assert.match(await page.locator('#editor-body').innerText(),/暂无业务附件/);await close();
  assert.deepEqual(await read('qa_state'),before);
  checks.push('采购显示所有材料、数量、单价和金额；项目名称正常；原单与附件可读，查看不改账');
  await page.evaluate(()=>{window.scrollTo(0,0);main.querySelectorAll('.table-wrap').forEach(table=>table.scrollLeft=0);});await page.screenshot({path:path.join(output,'governance-desktop.png'),fullPage:true});
  await page.setViewportSize({width:375,height:812});await page.screenshot({path:path.join(output,'governance-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.setViewportSize({width:1366,height:768});
  checks.push('375像素小屏只在表格内部横滚，页面不溢出');
  if(await page.evaluate(()=>state.boot.read_only)){
    assert.equal(await page.locator('[data-governance-assign]').count(),0);
    const rejected=await page.evaluate(async id=>{
      try{await api('operation_preview',{name:'governance_purchase',record:{ids:[id],project_id:1,reason:'合成只读测试'}});return false;}
      catch{return true;}
    },before.pending_id);
    assert(rejected);assert.deepEqual(await read('qa_state'),before);
    checks.push('只读页面保留明细与附件，隐藏归集按钮，服务器拒绝预览写入');
  }else{
    await source.locator('[data-governance-assign]').click();
    const choices=await page.locator('[name="ids"]').innerText();
    assert(choices.includes('合成螺杆')&&choices.includes('合成螺帽')&&choices.includes('¥90.58'));
    await chooseProject();await page.locator('[name="reason"]').fill('合成明确归集');await page.locator('#editor-save').click();
    await page.waitForFunction(()=>!editorSubmitting&&document.querySelector('[name="operation-confirmed"]'));
    const review=await page.locator('#editor-body').innerText();assert(review.includes('归集到：')&&review.includes('厂房钢结构')&&review.includes('12 支')&&review.includes('¥90.58'));
    await close();assert.deepEqual(await read('qa_state'),before);
    checks.push('批量候选与确认预览有日期、商户、全部材料、数量、金额及目标；取消不写账');
    await page.evaluate(id=>openOperation('input_links',{id}),before.invoice_id);
    await page.locator('[data-row-field="links"] .add-operation-row').click();
    const candidates=await page.evaluate(()=>[...editor.querySelectorAll('select option')].map(option=>option.textContent).join('\n'));
    assert(candidates.includes('合成螺杆')&&candidates.includes('合成螺帽'));await close();
    assert.deepEqual(await read('qa_state'),before);
    checks.push('进项票关联成本也显示材料说明，不再只有单号');
    await source.locator('[data-governance-assign]').click();await chooseProject();
    await page.locator('[name="reason"]').fill('合成明确归集');await page.locator('#editor-save').click();
    await page.waitForFunction(()=>!editorSubmitting&&document.querySelector('[name="operation-confirmed"]'));
    await page.locator('[name="operation-confirmed"]').check();await page.locator('#editor-save').click();
    await page.waitForFunction(()=>!editor.open&&!editorSubmitting);await idle();
    assert.equal((await read('qa_state')).assigned_project,1);
    const after=await read('dashboard?view=governance');assert.equal(Number(after.summary.purchase_record_count),0);assert.equal(after.unassigned_purchases.length,0);
    checks.push('明确确认后只归集选中原单，清单和统计一起刷新，已均摊采购仍不重复计入');
  }
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack);}
finally{await fs.writeFile(path.join(output,'results.json'),JSON.stringify({synthetic:true,checks,errors},null,2));await browser.close();}
console.log(JSON.stringify({synthetic:true,checks,errors},null,2));if(errors.length)process.exitCode=1;
