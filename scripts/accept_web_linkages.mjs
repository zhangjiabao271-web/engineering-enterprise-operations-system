// Real form interactions; accepts only a fresh, writable synthetic fixture.
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
await fs.mkdir(output,{recursive:false});
const browser=await chromium.launch({...(process.env.WORKBENCH_BROWSER ? {executablePath:process.env.WORKBENCH_BROWSER} : {}),headless:true});
const page=await browser.newPage({viewport:{width:1366,height:768},reducedMotion:'reduce'});
const checks=[],errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(!request.url().startsWith(origin))errors.push('Unexpected external request');});
const read=route=>page.evaluate(route=>api(route),route);
const idle=()=>page.waitForFunction(()=>!editorSubmitting&&main.getAttribute('aria-busy')==='false');
const linked=()=>page.waitForFunction(()=>!editorLookups.size);
const values=selector=>page.locator(selector).evaluate(select=>[...select.options].map(row=>row.value).filter(Boolean));
const field=name=>'#editor [name="'+name+'"]';
async function choose(name,keyword){
  await page.locator(field(name)+' + input').fill(keyword);
  await page.locator(field(name)+' ~ ul [data-choice-index]').first().click();
}
async function close(){await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);}
async function route(hash){
  await page.evaluate(hash=>location.hash=hash,hash);
  await page.waitForFunction(hash=>state.route===hash.split('?')[0]&&main.getAttribute('aria-busy')==='false',hash);
}
async function openNew(){await page.locator('#new-record').click();await page.waitForFunction(()=>editor.open&&!editorSubmitting);}
async function write(module,record){
  return page.evaluate(async({module,record})=>{
    if(!state.boot.demo||state.boot.read_only)throw new Error('Synthetic fixture required');
    const ticket=await api('write_ticket?'+query({kind:'save',module}));
    return api('save',{module,record,form_ticket:ticket.token,confirmed:true});
  },{module,record});
}
try{
  await page.goto(origin);await page.waitForFunction(()=>state.boot&&main.getAttribute('aria-busy')==='false');
  assert(await page.evaluate(()=>state.boot.demo&&!state.boot.read_only));
  await page.evaluate(()=>api('qa_fixture',{name:'inspection'}));
  await page.reload();await idle();
  const contract=await write('contracts',{name:'合成联动 · 第二合同',customer_partner_id:1,pricing_mode:'actual',amount:'0',sign_date:'2026-10-13',status:'active'});
  await write('allocations',{project_id:1,contract_id:contract,amount:'0'});
  const settlement=await write('settlements',{project_id:1,contract_id:contract,amount:'10000',settlement_date:'2026-10-13',basis:'合成第二合同'});
  const invoice=await write('invoices',{project_id:1,contract_id:contract,settlement_id:settlement,amount:'1000',invoice_date:'2026-10-13',invoice_no:'SYNTHETIC-LINKAGE-INVOICE'});
  const project=await write('projects',{name:'合成联动 · 第二工地',status:'进行中',customer_partner_id:1});
  const otherContract=await write('contracts',{name:'合成联动 · 另一工地合同',customer_partner_id:1,pricing_mode:'actual',amount:'0',sign_date:'2026-10-13',status:'active'});
  await write('allocations',{project_id:project,contract_id:otherContract,amount:'0'});
  const otherSettlement=await write('settlements',{project_id:project,contract_id:otherContract,amount:'2000',settlement_date:'2026-10-13',basis:'合成另一工地'});
  const cashProject=await write('projects',{name:'合成联动 · 现金项目',status:'进行中',customer_partner_id:1,business_mode:'cash',invoice_policy:'not_required'});
  const cashSettlement=await write('settlements',{project_id:cashProject,amount:'1000',settlement_date:'2026-10-13',basis:'合成现金确认'});
  const receipt=await write('receipts',{project_id:1,contract_id:1,amount:'1000',receipt_date:'2026-10-13',payment_method:'银行转账'});
  await page.evaluate(async()=>{
    const preview=await api('operation_preview',{name:'entity_assign',record:{kind:'contracts',ids:[1],entity_id:1,reason:'合成联动主体确认'}});
    await api('operation_apply',{token:preview.token,confirmed:true});
    await refreshOptions();
  });
  assert.equal((await read('record?module=invoices&id=1')).unreceived_minor,'9900000');
  checks.push('合同主体继承后发票余额立即抵扣，无须重复确认单据');
  const beforeInteractions=(await read('qa_state')).digest;

  await route('finance?tab=receipts');await openNew();
  assert(await page.locator(field('contract_id')).isDisabled());
  assert(await page.locator(field('settlement_id')).isDisabled());
  await choose('project_id','厂房钢结构');await linked();
  assert.deepEqual((await values(field('contract_id'))).sort(),['1',String(contract)].sort());
  await choose('contract_id','第二合同');await linked();
  assert.deepEqual(await values(field('settlement_id')),[String(settlement)]);
  assert.deepEqual(await values(field('invoice_id')),[String(invoice)]);
  await page.locator(field('invoice_id')).selectOption(String(invoice));
  assert.equal(await page.locator(field('settlement_id')).inputValue(),String(settlement));
  assert(await page.locator(field('settlement_id')).isDisabled());
  await page.locator(field('invoice_id')).selectOption('');
  assert(!(await page.locator(field('settlement_id')).isDisabled()));
  await page.locator(field('amount')).fill('12.34');
  await page.locator(field('contract_id')+' + input').fill('钢结构');
  assert.equal(await page.locator(field('contract_id')).inputValue(),'');
  assert(await page.locator(field('settlement_id')).isDisabled());
  assert.deepEqual(await values(field('invoice_id')),[]);
  assert.equal(await page.locator(field('amount')).inputValue(),'12.34');
  await choose('contract_id','钢结构施工合同');await linked();
  assert.deepEqual(await values(field('settlement_id')),['1']);
  assert.deepEqual(await values(field('invoice_id')),['1']);
  await choose('project_id','第二工地');await linked();
  assert.equal(await page.locator(field('contract_id')).inputValue(),'');
  assert.deepEqual(await values(field('contract_id')),[String(otherContract)]);
  await choose('contract_id','另一工地合同');await linked();
  assert.deepEqual(await values(field('settlement_id')),[String(otherSettlement)]);
  await choose('project_id','现金项目');await linked();
  assert(await page.locator(field('contract_id')).isDisabled());
  assert(await page.locator(field('invoice_id')).isDisabled());
  assert.deepEqual(await values(field('settlement_id')),[String(cashSettlement)]);
  await close();checks.push('项目／合同／收入／发票逐级筛选，搜索中清旧关联，现金项目不选发票');

  await page.evaluate(id=>openOperation('receipt_allocation',{id}),receipt);
  await page.locator('[data-row-field="allocations"] [name="settlement_id"]').first().waitFor();
  assert.deepEqual(await values('[data-row-field="allocations"] [name="settlement_id"]'),['1']);
  await close();checks.push('回款重新分配只提供本项目本合同收入确认');

  await route('finance?tab=invoices');await openNew();
  await choose('project_id','厂房钢结构');await linked();
  await choose('contract_id','第二合同');await linked();
  assert.deepEqual(await values(field('settlement_id')),[String(settlement)]);
  await close();checks.push('普通发票录入和回款复用一致的合同筛选');

  let release,started;
  const gate=new Promise(resolve=>release=resolve),requested=new Promise(resolve=>started=resolve);
  let delayed=false;
  await page.route('**/api/options?*',async request=>{
    if(new URL(request.request().url()).searchParams.get('project_id')==='1'&&!delayed){
      delayed=true;started();await gate;
    }
    await request.continue();
  });
  await openNew();await choose('project_id','厂房钢结构');await requested;
  await choose('project_id','第二工地');await linked();
  const lateResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/options'&&new URL(response.url()).searchParams.get('project_id')==='1');
  release();await lateResponse;await page.waitForTimeout(50);
  assert.deepEqual(await values(field('contract_id')),[String(otherContract)]);
  await close();await page.unroute('**/api/options?*');checks.push('迟到的原项目选项不能覆盖新项目候选');

  await page.route('**/api/pdf_preview',request=>request.fulfill({json:{result:{token:'synthetic-preview-only',match:null,
    recognition:{invoice_no:'SYNTHETIC-PDF',invoice_date:'2026-10-13',amount:'100',net_amount:'100',tax_amount:'0',tax_rate:'0',warnings:[]}}}}));
  await page.route('**/api/pdf_discard',request=>request.fulfill({json:{result:true}}));
  await page.locator('#invoice-pdf-import').click();
  await page.locator(field('pdf')).setInputFiles({name:'synthetic.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\nsynthetic form linkage only')});
  await page.locator('#editor-save').click();await page.locator(field('contract_id')).waitFor({state:'attached'});
  await page.waitForFunction(()=>!editorSubmitting);
  await choose('project_id','厂房钢结构');await linked();
  await choose('contract_id','第二合同');await linked();
  assert.deepEqual(await values(field('settlement_id')),[String(settlement)]);
  await close();await page.unroute('**/api/pdf_preview');await page.unroute('**/api/pdf_discard');
  checks.push('PDF导入业务表单复用联动；合成识别结果，不外发真实资料');

  await route('labor?tab=worklogs');await openNew();
  await choose('project_id','厂房钢结构');await linked();
  const sites=(await read('options?project_id=1')).sites;
  await page.locator(field('project_site_id')).selectOption(String(sites[0].value));
  assert.equal(await page.locator(field('construction_site')).inputValue(),sites[0].label);
  await choose('project_id','第二工地');await linked();
  assert.equal(await page.locator(field('construction_site')).inputValue(),'');
  await choose('project_id','厂房钢结构');await linked();
  await page.locator(field('project_site_id')).selectOption(String(sites[0].value));
  await page.locator(field('construction_site')).fill('人工填写的具体位置');
  await choose('project_id','第二工地');await linked();
  assert.equal(await page.locator(field('construction_site')).inputValue(),'人工填写的具体位置');
  await close();checks.push('单人工天选地点自动填字；换项目清自动地点，但保留人工文字');

  await page.locator('#batch-worklogs').click();await page.locator(field('project_site_id')).waitFor();
  await choose('project_id','厂房钢结构');await linked();
  await page.locator(field('project_site_id')).selectOption(String(sites[0].value));
  await choose('project_id','第二工地');await linked();
  assert.equal(await page.locator(field('construction_site')).inputValue(),'');
  assert.equal(await page.locator(field('project_site_id')).inputValue(),'');
  await close();checks.push('批量工天与单人入口共用项目地点规则');

  await route('contracts?tab=contracts');await openNew();
  await page.locator(field('contract_type')).selectOption('annual');
  assert.equal(await page.locator(field('pricing_mode')).inputValue(),'actual');
  assert(!(await page.locator(field('amount')).isVisible()));
  assert(await page.locator(field('amount')).isDisabled());
  assert(await page.locator(field('control_limit')).isVisible());
  await page.screenshot({path:path.join(output,'annual-actual.png')});
  await page.locator(field('pricing_mode')).selectOption('provisional');
  assert(await page.locator(field('amount')).isVisible());
  assert(await page.locator(field('amount')).getAttribute('required')!==null);
  assert.match(await page.locator(field('amount')).locator('..').innerText(),/暂定金额/);
  await page.locator(field('contract_type')).selectOption('project');
  await page.locator(field('contract_type')).selectOption('annual');
  assert.equal(await page.locator(field('pricing_mode')).inputValue(),'provisional');
  await page.locator(field('pricing_mode')).selectOption('fixed');
  assert(!(await page.locator(field('control_limit')).isVisible()));
  await close();checks.push('年度合同默认据实；金额字段按模式展示；尊重人工计价选择');

  await page.evaluate(()=>editRecord('contracts',1));
  await page.locator(field('pricing_mode')).waitFor();
  assert.equal(await page.locator(field('pricing_mode')).inputValue(),'fixed');
  await page.locator(field('contract_type')).selectOption('annual');
  assert.equal(await page.locator(field('pricing_mode')).inputValue(),'fixed');
  await close();checks.push('编辑历史合同不擅自改变计价模式');
  assert.equal((await read('qa_state')).digest,beforeInteractions);
  checks.push('本轮表单预览和取消未改变合成账本');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack);}
finally{
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({synthetic:true,checks,errors},null,2));
  await browser.close();
}
console.log(JSON.stringify({synthetic:true,checks,errors},null,2));
if(errors.length)process.exitCode=1;
