// Regression acceptance only on the disposable write-matrix fixture server.
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
const field=name=>'#editor [name="'+name+'"]';
const read=route=>page.evaluate(route=>api(route),route);
const digest=async()=> (await read('qa_state')).digest;
const idle=()=>page.waitForFunction(()=>!editorSubmitting&&main.getAttribute('aria-busy')==='false');
async function route(hash){
  await page.evaluate(hash=>location.hash=hash,hash);
  await page.waitForFunction(hash=>state.route===hash.split('?')[0]&&main.getAttribute('aria-busy')==='false',hash);
}
async function choose(name,keyword){
  await page.locator(field(name)+' + input').fill(keyword);
  await page.locator(field(name)+' ~ ul [data-choice-index]').first().click();
  await page.waitForFunction(()=>!editorLookups.size);
}
async function close(){await page.locator('#editor-cancel').click();await page.waitForFunction(()=>!editor.open);}
async function submit(){await page.locator(field('confirmed')).check();await page.locator('#editor-save').click();}
async function saved(){await submit();await page.waitForFunction(()=>!editor.open&&!editorSubmitting);await idle();}
async function write(module,record,id){
  return page.evaluate(async({module,record,id})=>{
    if(!state.boot.demo||state.boot.read_only)throw new Error('Disposable fixture required');
    const original=id?await api('record?'+query({module,id})):{};
    const ticket=await api('write_ticket?'+query({kind:'save',module,id,expected:original._version}));
    return api('save',{module,id,expected:original._version,record:{...original,...record},form_ticket:ticket.token,confirmed:true});
  },{module,record,id});
}
async function assign(kind,ids,entity_id){
  return page.evaluate(async record=>{
    const preview=await api('operation_preview',{name:'entity_assign',record});
    return api('operation_apply',{token:preview.token,confirmed:true});
  },{kind,ids,entity_id,reason:'合成验收确认'});
}
async function rejected(message,before){
  await submit();await page.locator('#form-error').waitFor({state:'visible'});
  assert.match(await page.locator('#form-error').innerText(),message);
  assert(await page.evaluate(()=>editor.open));
  await page.waitForFunction(()=>!editorSubmitting);
  assert.equal(await digest(),before);
}
try{
  await page.goto(origin);await page.waitForFunction(()=>state.boot&&main.getAttribute('aria-busy')==='false');
  assert(await page.evaluate(()=>state.boot.demo&&!state.boot.read_only));
  await page.evaluate(()=>api('qa_fixture',{name:'inspection'}));
  await page.reload();await idle();
  await assign('contracts',[1],1);
  const receipt=await write('receipts',{project_id:1,contract_id:1,amount:'500',receipt_date:'2026-10-03',payment_method:'银行转账'});
  const transaction=await write('transactions',{category:'receipt',source_id:receipt,to_account_id:1,
    transaction_date:'2026-10-03',amount:'500',request_key:'synthetic-boundary-funds'});
  await assign('fund_transactions',[transaction],1);
  const customer=await write('customers',{legal_name:'合成验收 · 另一客户'});
  await page.evaluate(()=>refreshOptions());
  await route('contracts?tab=contracts');
  await page.locator('[data-action="edit"][data-id="1"]').click();
  await page.locator(field('entity_id')).selectOption('2');
  await page.locator(field('notes')).fill('不能部分保存');
  await rejected(/资金流水.*经营主体/,await digest());
  assert.equal(await page.locator(field('notes')).inputValue(),'不能部分保存');
  await page.screenshot({path:path.join(output,'contract-owner-conflict.png')});
  await close();checks.push('合同原表单阻止继承主体与资金冲突，错误保留输入且整账不变');
  const beforePreview=await digest();
  const previewError=await page.evaluate(async()=>{
    try{await api('operation_preview',{name:'entity_assign',record:{kind:'contracts',ids:[1],entity_id:2,reason:'合成冲突预览'}});return '';}
    catch(error){return error.message;}
  });
  assert.match(previewError,/资金流水/);assert.equal(await digest(),beforePreview);
  checks.push('经营主体预览同样拒绝冲突且不写账');
  for(const module of ['projects','contracts']){
    await route(module==='projects'?'projects':'contracts?tab=contracts');
    await page.locator('[data-action="edit"][data-id="1"]').click();
    await choose('customer_partner_id','另一客户');
    await rejected(/同一客户/,await digest());
    await close();checks.push(module+'拒绝客户错配且整笔回滚');
  }
  for(const module of ['projects','contracts']){
    const values={name:'合成未使用'+module,customer_partner_id:1};
    if(module==='contracts')Object.assign(values,{amount:'100',sign_date:'2026-10-03',status:'draft'});
    const id=await write(module,values);
    await page.evaluate(()=>refreshOptions());
    await route(module==='projects'?'projects':'contracts?tab=contracts');
    await page.evaluate(()=>renderRoute(false));await idle();
    await page.locator(`[data-action="edit"][data-id="${id}"]`).click();
    await choose('customer_partner_id','另一客户');await saved();
    assert.equal((await read('record?'+new URLSearchParams({module,id}))).customer_partner_id,customer);
    checks.push(module+'未使用资料仍允许更正客户');
  }
  await route('finance?tab=receipts');
  await page.locator(`[data-action="edit"][data-id="${receipt}"]`).click();
  for(const name of ['project_id','contract_id']){
    assert(await page.locator(field(name)).isDisabled());
    assert(await page.locator(field(name)+' + input').isDisabled());
  }
  assert.match(await page.locator('#editor-body').innerText(),/原回款归属.*不可更换/);
  for(const viewport of [{width:390,height:844},{width:1366,height:768}]){
    await page.setViewportSize(viewport);
    await page.locator('#editor-body').evaluate(body=>body.scrollTop=0);
    const notes=await page.locator(field('project_id')).evaluate(select=>{
      const label=select.closest('label'),note=label.querySelector('small');
      return {label:label.getBoundingClientRect().toJSON(),note:note.getBoundingClientRect().toJSON()};
    });
    assert(notes.note.width>0&&notes.note.right<=notes.label.right+1);
    if(viewport.width===390)await page.screenshot({path:path.join(output,'receipt-locked-scope-mobile.png')});
  }
  await page.screenshot({path:path.join(output,'receipt-locked-scope.png')});
  await page.locator(field('notes')).fill('合成验收允许补备注');await saved();
  const actual=await read('record?module=receipts&id='+receipt);
  assert.equal(actual.notes,'合成验收允许补备注');assert.equal(actual.project_id,1);assert.equal(actual.contract_id,1);
  checks.push('回款项目与合同含搜索框均锁定，桌面和手机说明可读，备注正常保存');
  const beforeBypass=await digest();
  await assert.rejects(write('receipts',{contract_id:null,notes:'不能绕过前端'},receipt),/归属已锁定/);
  assert.equal(await digest(),beforeBypass);checks.push('绕过前端提交归属更改也被拒绝，无假成功');
  await page.locator('#new-record').click();
  await page.waitForFunction(()=>editor.open&&document.querySelector('#dialog-title').textContent.startsWith('新增'));
  assert(!(await page.locator(field('project_id')).isDisabled()));
  await choose('project_id','厂房钢结构');
  assert(!(await page.locator(field('contract_id')).isDisabled()));
  await close();checks.push('新增回款仍可选择项目和合同');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack);}
finally{
  await fs.writeFile(path.join(output,'results.json'),JSON.stringify({synthetic:true,checks,errors},null,2));
  await browser.close();
}
console.log(JSON.stringify({synthetic:true,checks,errors},null,2));
if(errors.length)process.exitCode=1;
