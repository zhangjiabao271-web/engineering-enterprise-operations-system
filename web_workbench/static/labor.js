'use strict';

function batchWorkerHTML(worker, selected, previous={}) {
  return `<div class="batch-worker" data-worker-id="${esc(worker.value)}">
    <label class="batch-worker-name"><input type="checkbox" name="batch-worker" ${selected?'checked':''}><span>${esc(worker.label)}</span></label>
    ${fieldHTML({name:'work_days',label:'工天',default:'1'},previous.work_days)}
    <label class="batch-overtime"><input type="checkbox" name="is_overtime" ${previous.is_overtime?'checked':''}>加班记录</label>
  </div>`;
}

function collectBatchWorkdays() {
  const common = formValues([
    {name:'project_id'}, {name:'construction_site'}, {name:'work_date'},
    {name:'project_site_id'}, {name:'work_type'}, {name:'notes'},
  ]);
  const rows = [...editor.querySelectorAll('.batch-worker')].filter(row=>row.querySelector('[name=batch-worker]').checked);
  if (!rows.length) throw new Error('请至少勾选一名在职工人');
  if (rows.length>200) throw new Error('一次最多登记200条工天，请分批保存');
  return rows.map(row=>({
    ...common, worker_id:row.dataset.workerId,
    work_days:row.querySelector('[name=work_days]').value,
    is_overtime:row.querySelector('[name=is_overtime]').checked,
  }));
}

function batchWorkdayReview(review) {
  const days = review.rows.reduce((sum,row)=>sum+Number(row.work_days),0);
  const columns = [
    ['worker_name','工人'], ['work_date','日期'], ['project_name','项目'],
    ['construction_site','作业地点'], ['work_days','工天'], ['is_overtime','加班'], ['amount_minor','人工成本'],
  ];
  return notice(`本批 ${review.rows.length} 名工人 · 共 ${days} 工。每人按该日期的工资版本计算；加班标记不另加出勤。`) +
    table(review.rows.map(row=>({...row,is_overtime:Boolean(Number(row.is_overtime))})),columns,null,'batch-review-table') +
    `<p class="page-note">本批人工成本 ${money(review.total_minor)} · 任一条校验失败，整批不保存。</p>`;
}

async function openWorkdayBatch(context={}) {
  if (state.boot.read_only) throw new Error('当前只读');
  const request=beginEditorRequest();
  const previous=new Map((context.entries || []).map(entry=>[String(entry.worker_id),entry]));
  context={...(context.entries?.[0] || {}),...context};
  const options = await request.read('options?'+query({project_id:context.project_id || state.project}));
  const activeIds = new Set(options.active_workers.map(worker=>String(worker.value)));
  const unavailable = [...previous.keys()].filter(id=>!activeIds.has(id));
  const requestedProject = context.project_id || state.project;
  const projectId = options.active_projects.some(project=>String(project.value)===String(requestedProject)) ? requestedProject : '';
  const today = businessToday();
  const fields = [
    {name:'project_id',label:'独立核算项目',kind:'select',source:'active_projects',required:true},
    {name:'work_date',label:'日期',kind:'date',required:true,default:today},
    {name:'construction_site',label:'作业地点',required:true},
    {name:'project_site_id',label:'项目施工地点（可留空）',kind:'select',source:'sites'},
    {name:'work_type',label:'作业内容'}, {name:'notes',label:'说明 / 备注',kind:'textarea'},
  ];
  const body = notice('项目、日期和地点只填一次。勾选本次出勤的工人，每人分别选择0.5工或1工；新增只显示在职工人。') +
    (unavailable.length?notice(`返回修改时有 ${unavailable.length} 名原选工人已不在当前在职名单，本批不再包含他们。请核对工人档案和本批名单。`):'') +
    `<div class="form-grid">${fields.slice(0,4).map(field=>fieldHTML(field,field.name==='project_id'?projectId:context[field.name],options)).join('')}</div>` +
    `<details class="batch-common-details"><summary>补充作业内容 / 备注（可选）</summary><div class="form-grid">${fields.slice(4).map(field=>fieldHTML(field,context[field.name],options)).join('')}</div></details>` +
    `<section class="batch-attendance" aria-labelledby="batch-workers-title">
      <div class="section-head"><h3 id="batch-workers-title">本次出勤工人</h3><output id="batch-workday-summary" aria-live="polite"></output></div>
      <div class="batch-worker-tools"><input type="search" id="batch-worker-search" placeholder="搜索工人姓名" aria-label="搜索在职工人"><button type="button" id="batch-select-visible">全选当前列表</button><button type="button" id="batch-clear">取消全部</button></div>
      <p id="batch-worker-count" class="page-note" role="status" aria-live="polite"></p>
      <div class="batch-worker-list">${options.active_workers.map(worker=>batchWorkerHTML(worker,previous.has(String(worker.value)) || String(worker.value)===String(context.worker_id || context.id || ''),previous.get(String(worker.value)))).join('')}</div>
      <p id="batch-worker-empty" class="page-note" hidden>没有匹配的在职工人。请换个关键词，或先在工人档案中维护在职状态。</p>
      <small>默认每人1工；勾选后可改为0.5工。搜索不会清掉已勾选的人，保存前会显示全部明细。</small>
    </section>`;
  openEditor('批量登记工天',body,async()=>{
    const entries = collectBatchWorkdays();
    const result = await api('operation_preview',{name:'labor_batch',record:{entries,reason:entries[0].notes || '批量登记工天'}});
    openEditor('核对本批工天','<button type="button" id="batch-workday-back">返回修改本批工天</button>'+batchWorkdayReview(result.review)+
      '<label class="confirm-check"><input name="batch-confirmed" type="checkbox" required>已核对所有工人、日期、项目和工天，确认整批保存。</label>',async()=>{
      const count = await api('operation_apply',{token:result.token,confirmed:form.elements.namedItem('batch-confirmed').checked});
      cancelEditor=null;editor.close();toast(`已保存 ${count} 条工天记录。`);
      await refreshOptions();await renderRoute(false);
    },{label:'确认整批保存',cancel:()=>api('operation_discard',{token:result.token})});
    document.querySelector('#batch-workday-back').onclick=()=>safe(async()=>{
      const request=beginEditorRequest();
      await api('operation_discard',{token:result.token});
      request.check();
      cancelEditor=null;
      await openWorkdayBatch({entries});
    });
  },{label:'预览本批工天'});

  const workers = [...editor.querySelectorAll('.batch-worker')];
  const updateSummary = () => {
    const selected = workers.filter(row=>row.querySelector('[name=batch-worker]').checked);
    const days = selected.reduce((sum,row)=>sum+Number(row.querySelector('[name=work_days]').value),0);
    document.querySelector('#batch-workday-summary').textContent = `已选 ${selected.length} 人 · ${days} 工`;
    for (const row of workers) {
      const selected = row.querySelector('[name=batch-worker]').checked;
      row.classList.toggle('selected',selected);
      row.querySelectorAll('.workday-picker button,[name=is_overtime]').forEach(control=>control.disabled=!selected);
    }
  };
  editor.querySelector('.batch-worker-list').addEventListener('change',updateSummary);
  const filterWorkers = () => {
    const keyword = document.querySelector('#batch-worker-search').value.trim().toLocaleLowerCase();
    workers.forEach(row=>row.hidden=!row.querySelector('.batch-worker-name span').textContent.toLocaleLowerCase().includes(keyword));
    const visibleCount = workers.filter(row=>!row.hidden).length;
    document.querySelector('#batch-worker-count').textContent = `在职 ${workers.length} 人 · 当前显示 ${visibleCount} 人`;
    document.querySelector('#batch-worker-empty').hidden=visibleCount>0;
  };
  document.querySelector('#batch-worker-search').oninput=filterWorkers;
  document.querySelector('#batch-select-visible').onclick=()=>{
    workers.filter(row=>!row.hidden).forEach(row=>row.querySelector('[name=batch-worker]').checked=true);updateSummary();
  };
  document.querySelector('#batch-clear').onclick=()=>{
    workers.forEach(row=>row.querySelector('[name=batch-worker]').checked=false);updateSummary();
  };
  bindWorkdaySite();
  updateSummary();filterWorkers();
}
