'use strict';

function contractBusinessFilters(customer) {
  const business_scope=state.contractBusinessScope || 'all';
  return {customer,project_id:state.project,business_scope,
    year:business_scope==='contracts'?state.contractYear || '':'',
    status:business_scope==='contracts'?state.contractStatus || '':'',
    entity_id:state.contractEntity || '',keyword:state.keyword || ''};
}

function contractCustomerBusinessHint(row) {
  const parts=[];
  if (row.contract_count) parts.push(row.contract_count+' 份合同');
  if (row.cash_project_count) parts.push(row.cash_project_count+' 项零星现金工程');
  const unsigned=(row.project_business_count || 0)-(row.cash_project_count || 0);
  if (unsigned) parts.push(unsigned+' 项未关联合同业务');
  const contractFollowup=row.contract_followup_count ?? row.followup_count;
  if (contractFollowup) parts.push(contractFollowup+' 份合同待履约或收尾');
  else if (row.contract_count) parts.push('合同已办结');
  if (row.project_business_followup_count) parts.push(row.project_business_followup_count+' 项项目业务待跟进');
  return parts.join(' · ');
}

function contractCustomerAttachmentHint(row) {
  if (row.missing_attachment_count) return row.missing_attachment_count+' 份合同未上传附件';
  if (row.missing_file_count) return row.missing_file_count+' 个附件文件缺失';
  return row.contract_count?'合同附件已上传':'项目业务 · 未关联合同';
}

function contractBusinessCaption(data,onlyContracts) {
  const counts=`${data.contract_count} 份合同 · ${data.project_business_count || 0} 项未关联合同业务`;
  let note='全周期累计 · 已关联合同的份额只在合同中计入一次';
  if (onlyContracts) note='签约年份筛选 · 所选合同全周期累计';
  if (state.project) note='当前项目累计 · '+note;
  return {counts,note};
}

function contractBusinessLists(rows) {
  const contracts=rows.filter(row=>!row._projectBusiness);
  const projects=rows.filter(row=>row._projectBusiness);
  if (!rows.length) return '<div class="empty"><strong>当前范围没有业务记录</strong>可清除筛选，或切换到全部业务。</div>';
  let html='';
  if (projects.length) {
    const body=projects.map(row=>`<tr><td class="contract-name"><button type="button" class="contract-name-link" data-project-business-id="${row.project_id}" data-kind="settlements">${esc(row.name)}</button><small>${esc(row.customer_name)} · ${esc(row.project_status)}</small><span class="status">${row.business_mode==='cash'?'零星现金工程':'未关联合同业务'}</span><small>仅本项目未关联合同部分，不重复计入合同</small></td>${contractMetrics.map(metric=>`<td class="amount"><button type="button" class="contract-money" data-project-business-id="${row.project_id}" data-kind="${metric.kind}" aria-label="${esc(row.name+' · '+metric.label+' '+money(row[metric.key]))}">${esc(money(row[metric.key]))}</button></td>`).join('')}<td class="contract-followup"><span>预收／待核对 ${esc(money(row.pending_receipt_minor))}</span><small>${esc(row.latest_date || '尚无有效业务记录')}</small><button type="button" data-project-business-id="${row.project_id}" data-kind="receipts">查看原始记录</button></td></tr>`).join('');
    html+=`<section class="project-business-section"><h2>零星现金工程与未关联合同业务</h2><p class="page-note">收入、回款保留在原项目，不自动挂入某份合同；约定金额不计收入。</p><div class="table-wrap contract-list-table"><table id="project-business-records"><thead><tr><th>项目 / 客户 / 类型</th>${contractMetrics.map(metric=>`<th class="amount">${metric.label}</th>`).join('')}<th>回款说明 / 最近业务</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
  }
  if (contracts.length) html+=`<section class="contract-business-section"><h2>合同业务</h2>${contractListTable(contracts)}</section>`;
  return html;
}

function exportContractBusiness(data) {
  const contracts=data.contracts.map(row=>({...row,business_kind:'合同业务',
    contract_amount_label:row.pricing_mode==='actual'?'按实际结算':money(row.tax_inclusive_amount_minor)}));
  const projects=(data.project_business || []).map(row=>({...row,
    business_kind:row.business_mode==='cash'?'零星现金工程':'未关联合同业务',
    contract_amount_label:'未关联合同部分',status:row.project_status}));
  downloadCSV([...contracts,...projects],[['business_kind','业务类型'],['name','合同 / 项目'],['customer_name','客户'],
    ['sign_date','合同签约日期'],['contract_amount_label','合同约定口径'],
    ...contractMetrics.map(metric=>[metric.key,metric.label]),['status','状态']],'合同与项目业务台账');
}

const businessFactNotes={
  settlements:'只统计有效收入确认；现金工程约定金额和合同金额不会直接作为收入。',
  receipts:'按本次业务范围的有效分配份额计入；整笔到账仅供核对。预收计入到账，但不会自动变成收入或抵其他项目欠款。',
  invoices:'按发票价税合计的有效归属份额计入；合同已计份额与项目未关联份额不重叠。',
  outstanding:'逐笔列出有效收入确认的待收余额；未关联收入的回款不跨项目抵扣。',
};

function businessFactBody(data,kind) {
  const metric=contractMetrics.find(item=>item.kind===kind) || contractMetrics[0];
  const detail=data.details[metric.kind];
  const factColumns=contractDetailColumns[metric.kind].map(([key,label])=>[
    key,['contract_receipt_minor','contract_invoice_minor'].includes(key)?'归属当前范围':label,
  ]);
  const columns=[['scope_label','业务归属'],['contract_name','合同'],...factColumns];
  const needsReview=detail.rows.some(row=>row.needs_review);
  if (needsReview) columns.push(['review_reason','待核对说明']);
  const module=metric.kind==='outstanding'?'settlements':metric.kind;
  const cards=`<section class="ledger-detail-metrics" aria-label="当前业务范围金额">${contractMetrics.map(item=>`<button type="button" data-business-tab="${item.kind}" aria-pressed="${metric.kind===item.kind}" class="${metric.kind===item.kind?'selected':''}"><span>${item.label}</span><strong>${esc(money(data.summary[item.key]))}</strong></button>`).join('')}</section>`;
  const contents=`<div class="ledger-detail-title"><h3>${metric.label}明细</h3><span>${detail.rows.length} 笔 · 合计 <strong>${esc(money(detail.total_minor))}</strong></span></div>`+notice(businessFactNotes[metric.kind])+
    (needsReview?notice('有记录的业务关联待核对，已保留实际金额；冲突回款没有抵减收入未收，请查看待核对说明。','warning'):'')+
    (detail.reconciled?'':notice('明细合计与汇总不一致，请核对关联记录；本次查看未改账。','warning'))+
    table(detail.rows,columns,row=>`<button type="button" data-business-fact="${row.id}" data-module="${module}">原记录</button><button type="button" data-business-files="${row.id}" data-module="${module}">附件</button>`,'business-facts',module);
  return {kind:metric.kind,html:cards+contents};
}

async function openContractBusinessSummary(filters,kind) {
  const request=beginEditorRequest();
  const data=await request.read('contract_business_detail?'+query(filters));
  showContractBusinessFacts(data,kind,{filters});
}

async function openProjectBusiness(id,kind='settlements',entity=state.contractEntity || '') {
  const request=beginEditorRequest();
  const data=await request.read('contract_project_business?'+query({id,project_id:state.project,entity_id:entity}));
  showContractBusinessFacts(data,kind,{projectId:id,entity});
}

function showContractBusinessFacts(data,kind,context) {
  const facts=businessFactBody(data,kind);
  const project=data.project;
  const title=project?project.name+' · 未关联合同部分':'当前范围 · 业务汇总明细';
  const prefix=project?`<p class="page-note">${esc(project.customer_name || '客户待关联')} · 只读原项目业务，不新增收入或到账。</p><button type="button" id="business-full-project">查看项目全周期台账</button>`:'';
  const reopen=()=>context.projectId?openProjectBusiness(context.projectId,facts.kind,context.entity):openContractBusinessSummary(context.filters,facts.kind);
  openEditor(title,prefix+facts.html,null,{cancelLabel:'返回业务列表',note:'含零星现金工程 · 与当前汇总范围一致'});
  form.querySelectorAll('[data-business-tab]').forEach(button=>button.onclick=()=>showContractBusinessFacts(data,button.dataset.businessTab,context));
  form.querySelectorAll('[data-business-fact]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(button.dataset.module,Number(button.dataset.businessFact),{back:reopen,backLabel:'返回业务明细'})));
  form.querySelectorAll('[data-business-files]').forEach(button=>button.onclick=()=>safe(()=>attachments(button.dataset.module,Number(button.dataset.businessFiles),false,{back:reopen,backLabel:'返回业务明细'})));
  document.querySelector('#business-full-project')?.addEventListener('click',()=>safe(()=>openProjectLedger(context.projectId,facts.kind,{back:reopen,backLabel:'返回未关联合同业务'})));
}
