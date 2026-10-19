'use strict';

const contractMetrics = [
  {key:'settlement_minor',kind:'settlements',label:'已确认收入'},
  {key:'receipt_minor',kind:'receipts',label:'已回款'},
  {key:'invoice_minor',kind:'invoices',label:'已开票'},
  {key:'receivable_minor',kind:'outstanding',label:'未回款'},
];

const contractDetailColumns = {
  settlements:[['settlement_date','确认日期'],['project_name','项目'],['amount_minor','确认金额'],['basis','确认依据']],
  receipts:[['receipt_date','到账日期'],['project_name','项目'],['contract_receipt_minor','归属本合同'],['amount_minor','整笔到账'],['payment_method','收款方式']],
  invoices:[['invoice_date','开票日期'],['invoice_no','发票号'],['project_name','项目'],['contract_invoice_minor','归属本合同']],
  outstanding:[['settlement_date','确认日期'],['project_name','项目'],['amount_minor','确认金额'],['received_minor','已核对回款'],['unreceived_minor','待收余额']],
};

function contractMoney(row, metric) {
  return `<button type="button" class="contract-money" data-contract-detail="${row.id}" data-kind="${metric.kind}" aria-label="${esc(row.name)} · ${metric.label} ${esc(money(row[metric.key]))}，查看明细">${esc(money(row[metric.key]))}</button>`;
}

function contractCustomerTable(rows) {
  if (!rows.length) return '<div class="empty"><strong>当前范围没有客户业务</strong>可清除筛选，或切换到全部业务。</div>';
  return `<div class="table-wrap contract-customer-table"><table id="contract-customers"><thead><tr><th>客户 / 业务</th><th class="amount">已确认收入</th><th class="amount">已回款</th><th class="amount">未回款</th><th>待处理 / 最近业务</th></tr></thead><tbody>${rows.map(row=>`<tr><td class="contract-name"><button type="button" class="contract-name-link" data-customer="${esc(row.customer_key)}">${esc(row.customer_name)}</button><small>${esc(contractCustomerBusinessHint(row))}${row.customer_partner_id?'':' · 客户档案待关联'}</small></td>${['settlement_minor','receipt_minor','receivable_minor'].map(key=>`<td class="amount"><button type="button" class="contract-money" data-customer="${esc(row.customer_key)}" aria-label="查看${esc(row.customer_name)}的业务明细">${esc(money(row[key]))}</button></td>`).join('')}<td class="contract-followup"><span>${esc(contractCustomerAttachmentHint(row))}</span><small>${esc(row.latest_date || '暂无业务日期')}</small></td></tr>`).join('')}</tbody></table></div>`;
}

function contractListTable(rows) {
  if (!rows.length) return '<div class="empty"><strong>没有符合条件的合同</strong>试试全部年份，或清除状态、主体和搜索条件。</div>';
  return `<div class="table-wrap contract-list-table"><table id="records"><thead><tr><th>合同 / 客户</th><th>计价方式 / 金额</th>${contractMetrics.map(metric=>`<th class="amount">${metric.label}</th>`).join('')}<th>附件</th><th>操作</th></tr></thead><tbody>${rows.map(row=>`<tr><td class="contract-name"><button type="button" class="contract-name-link" data-contract-detail="${row.id}" data-kind="overview">${esc(row.name)}</button><small>${esc(row.customer_name)} · ${esc(row.sign_date || '签约日期待补充')}</small><span class="status">${esc(row.status==='active'?'履约中':labels[row.status] || row.status)}</span></td><td class="contract-pricing"><span>${esc(labels[row.pricing_mode] || row.pricing_mode)}</span><strong>${row.pricing_mode==='actual'?'按实际结算':esc(money(row.tax_inclusive_amount_minor))}</strong></td>${contractMetrics.map(metric=>`<td class="amount">${contractMoney(row,metric)}</td>`).join('')}<td><button type="button" class="contract-attachment ${row.attachment_count && !row.missing_file_count?'':'needs-file'}" data-contract-files="${row.id}">${esc(row.attachment_label)}</button></td><td><button type="button" data-contract-detail="${row.id}" data-kind="overview">详情</button></td></tr>`).join('')}</tbody></table></div>`;
}

async function renderContractLedger() {
  state.collection = 'contracts';
  const parameters = new URLSearchParams(location.hash.split('?')[1] || '');
  const customer = parameters.get('customer') || '';
  // A link from project accounting should land directly on the relevant contracts.
  const listing = Boolean(customer || parameters.get('view')==='all' || state.project);
  const filters=contractBusinessFilters(customer);
  const data = await api('contract_ledger?'+query(filters));
  const onlyContracts=filters.business_scope==='contracts';
  const businessRows=(data.project_business || []).map(row=>({...row,_projectBusiness:true}));
  const rows = listing ? [...data.contracts,...businessRows] : data.customers;
  state.currentRows = data.contracts;
  state.page = Math.max(0,Math.min(state.page,Math.max(0,Math.ceil(rows.length/40)-1)));
  const visible=rows.slice(state.page*40,(state.page+1)*40);
  let title='合同与结算';
  if (customer) title=data.customer_name+' · 业务';
  else if (listing) title=onlyContracts?'全部合同':'全部业务';
  const actions = `${state.boot.read_only?'':'<button class="primary" id="contract-new">新增合同</button>'}<button id="contract-export">导出当前业务</button>`;
  const metrics = `<section class="contract-summary" aria-label="当前范围业务累计金额">${contractMetrics.map(metric=>`<button type="button" class="business-summary-button" data-business-detail="${metric.kind}"><span>${metric.label}</span><strong>${esc(money(data.summary[metric.key]))}</strong><small>查看逐笔明细</small></button>`).join('')}</section>`;
  const yearOptions = data.years.map(year=>`<option value="${esc(year)}" ${state.contractYear===year?'selected':''}>${year==='unknown'?'日期待补充':year+'年'}</option>`).join('');
  const entities = state.boot.options.entities || [];
  const selectOptions = (choices,value)=>choices.map(([key,label])=>`<option value="${esc(key)}" ${value===key?'selected':''}>${esc(label)}</option>`).join('');
  const scopeChoices=[['all','全部业务（含零星现金工程）'],['contracts','仅合同业务'],['uncontracted','零星及未关联合同业务']];
  const filterBar = `<div class="contract-filters"><label>业务范围<select id="contract-business-scope">${selectOptions(scopeChoices,filters.business_scope)}</select></label>${onlyContracts?`<label>签约年份<select id="contract-year"><option value="">全部年份</option>${yearOptions}</select></label><label>合同状态<select id="contract-status">${selectOptions([['','全部状态'],['active','履约中'],['draft','草稿'],['completed','已完成']],state.contractStatus || '')}</select></label>`:''}<label>经营主体<select id="contract-entity">${selectOptions([['','全部主体'],...entities.map(item=>[String(item.value),item.label]),['unknown','归属待确认']],state.contractEntity || '')}</select></label><div class="contract-search"><label class="sr-only" for="record-search">搜索客户、合同、项目或编号</label><input id="record-search" placeholder="搜索客户、合同、项目或编号" value="${esc(state.keyword || '')}"><button id="search-records">查询</button></div><button id="contract-clear" type="button">清除筛选</button></div>`;
  const caption=contractBusinessCaption(data,onlyContracts);
  main.innerHTML = header(title,'按客户查看合同、零星现金工程及未签业务；点击金额查看同范围的逐笔记录。',actions)+tabsHTML('contracts','contracts')+
    `<nav class="contract-breadcrumb" aria-label="合同导航">${listing?`<button id="contract-customers-back" type="button">← ${state.project?'返回上一级':'客户总览'}</button>`:'<span>客户总览</span>'}${customer?`<span aria-hidden="true"> / </span><span>${esc(data.customer_name)}</span>`:''}<span class="grow"></span>${listing?'':`<a href="#contracts?view=all">查看全部${onlyContracts?'合同':'业务'} →</a>`}</nav>`+
    `<h2 class="business-summary-heading">业务汇总${onlyContracts?' · 仅所选合同':' · 含零星现金工程'}</h2>`+metrics+filterBar+`<div class="contract-list-caption"><strong>${listing?'':data.customer_count+' 个客户 · '}${caption.counts}</strong><span>${esc(caption.note)}</span></div>`+
    (listing?contractBusinessLists(visible):contractCustomerTable(visible))+
    '<p class="page-note">收入只取有效收入确认，约定金额不算收入。未回款按逐笔待收余额计算，预收不抵其他项目；同一笔业务不会因同时有合同和项目重复计入。</p>'+
    `<div class="pagination"><span>第 ${state.page+1} 页 / ${Math.max(1,Math.ceil(rows.length/40))} 页</span><button id="prev-page" ${state.page===0?'disabled':''}>上一页</button><button id="next-page" ${(state.page+1)*40>=rows.length?'disabled':''}>下一页</button></div>`;
  main.querySelectorAll('[data-customer]').forEach(button=>button.onclick=()=>{location.hash='contracts?'+query({customer:button.dataset.customer});});
  main.querySelectorAll('[data-contract-detail]').forEach(button=>button.onclick=()=>safe(()=>openContractLedger(Number(button.dataset.contractDetail),button.dataset.kind)));
  main.querySelectorAll('[data-contract-files]').forEach(button=>button.onclick=()=>safe(()=>attachments('contracts',Number(button.dataset.contractFiles))));
  main.querySelectorAll('[data-project-business-id]').forEach(button=>button.onclick=()=>safe(()=>openProjectBusiness(Number(button.dataset.projectBusinessId),button.dataset.kind)));
  main.querySelectorAll('[data-business-detail]').forEach(button=>button.onclick=()=>safe(()=>openContractBusinessSummary(filters,button.dataset.businessDetail)));
  document.querySelector('#contract-customers-back')?.addEventListener('click',()=>state.project?WorkbenchNavigation.back():WorkbenchNavigation.backTo('contracts'));
  document.querySelector('#contract-new')?.addEventListener('click',()=>safe(()=>editRecord('contracts',null,{customer_partner_id:customer.startsWith('customer:')?Number(customer.split(':')[1]):undefined})));
  document.querySelector('#contract-export').onclick=()=>exportContractBusiness(data);
  for (const [id,key] of [['contract-business-scope','contractBusinessScope'],['contract-year','contractYear'],['contract-status','contractStatus'],['contract-entity','contractEntity']]) {
    document.querySelector('#'+id)?.addEventListener('change',event=>{state[key]=event.target.value;state.page=0;safe(()=>renderRoute(false));});
  }
  document.querySelector('#search-records').onclick=()=>{state.keyword=document.querySelector('#record-search').value;state.page=0;safe(()=>renderRoute(false));};
  document.querySelector('#record-search').onkeydown=event=>{if(event.key==='Enter')document.querySelector('#search-records').click();};
  document.querySelector('#contract-clear').onclick=()=>{state.contractYear='';state.contractStatus='';state.contractEntity='';state.contractBusinessScope='all';state.keyword='';state.page=0;safe(()=>renderRoute(false));};
  document.querySelector('#prev-page').onclick=()=>{state.page--;safe(()=>renderRoute(false));};
  document.querySelector('#next-page').onclick=()=>{state.page++;safe(()=>renderRoute(false));};
  bindTabs('contracts');
  const wrapper=main.querySelector('.contract-list-table,.contract-customer-table');
  if (wrapper) ProjectLedgerHeader.follow(wrapper);
}

async function openContractLedger(id, kind='overview') {
  const request = beginEditorRequest();
  const data = await request.read('contract_ledger_detail?'+query({id,project_id:state.project,
    entity_id:state.contractEntity || '',business_scope:state.contractBusinessScope || 'all'}));
  showContractLedger(data,kind);
}

function showContractLedger(data, kind='overview') {
  const row=data.contract;
  const metric=contractMetrics.find(item=>item.kind===kind);
  const cards=`<section class="ledger-detail-metrics" aria-label="合同金额">${contractMetrics.map(item=>`<button type="button" data-contract-tab="${item.kind}" aria-pressed="${kind===item.kind}" class="${kind===item.kind?'selected':''}"><span>${item.label}</span><strong>${esc(money(data.summary[item.key]))}</strong></button>`).join('')}</section>`;
  const allocationRows=data.allocations.map(item=>({...item,allocation_label:item.pricing_mode==='actual'?'据实关联':money(item.allocated_amount_minor)}));
  const overview=`<div class="detail-grid">${[['客户',row.customer_name],['合同编号',row.contract_no],['签约日期',row.sign_date],['合同状态',row.status==='active'?'履约中':labels[row.status]],['计价方式',labels[row.pricing_mode]],['约定金额',row.pricing_mode==='actual'?'按实际结算':money(row.tax_inclusive_amount_minor)],['经营主体',row.entity_name],['归属依据',row.ownership_source],['预收／待核对回款',money(row.pending_receipt_minor)],['备注',row.notes || '无']].map(([name,value])=>`<div class="detail-cell"><small>${esc(name)}</small><p>${esc(value || '待补充')}</p></div>`).join('')}</div><h3 class="contract-project-heading">关联项目 / 分配</h3>${table(allocationRows,[['project_name','项目'],['allocation_label','关联 / 分配金额'],['notes','关联说明']],null,'contract-projects','allocations')}`;
  let contents=overview;
  if (metric) {
    const detail=data.details[kind];
    const module=kind==='outstanding'?'settlements':kind;
    const notes={receipts:'“归属本合同”按实际分配金额统计；整笔到账只用于核对，不重复计入其他合同。',invoices:'按销项发票价税合计或明确的收入分配统计，发票余额另见开票台账。',outstanding:'逐笔列出尚未收清的收入确认；预收／待核对回款不会抵掉其他项目的待收余额。',settlements:'列出这份合同已生效的收入确认，日期可以跨签约年份。'};
    contents=`<div class="ledger-detail-title"><h3>${metric.label}明细</h3><span>${detail.rows.length} 笔 · 合计 <strong>${esc(money(detail.total_minor))}</strong></span></div>`+notice(notes[kind])+(detail.reconciled?'':notice('明细合计与汇总不一致，请核对关联记录。','warning'))+table(detail.rows,contractDetailColumns[kind],item=>`<button type="button" data-contract-fact="${item.id}" data-module="${module}">详情</button><button type="button" data-contract-fact-files="${item.id}" data-module="${module}">附件</button>`,'contract-facts',module);
  }
  const entityNote=data.entity_scope?notice('金额按当前业务主体范围统计；合同资料仍保留合同自身的经营主体，不改写归属。'):'';
  const body=`<div class="ledger-detail-meta"><span>${esc(row.customer_name)} · ${data.project_id?'当前项目累计':'合同全周期累计'}</span><button type="button" id="contract-dialog-back">返回合同列表</button></div>`+entityNote+cards+
    `<div class="stack-actions contract-dialog-actions"><button type="button" data-contract-tab="overview">合同资料</button><button type="button" id="contract-dialog-files">合同附件 · ${row.attachment_count}</button><button type="button" id="contract-dialog-analysis">资料现金流分析</button>${state.boot.read_only?'':`<button type="button" id="contract-dialog-edit">编辑合同</button><button type="button" id="contract-dialog-allocation">关联项目 / 分配</button><button type="button" id="contract-dialog-settlement">登记收入确认</button>`}${operationActions('contracts',row.id)}</div>`+contents;
  openEditor(row.name+' · 合同详情',body,null,{cancelLabel:'返回合同列表',note:'签约年份与业务发生年份分别记录'});
  document.querySelector('#contract-dialog-back').onclick=closeEditor;
  form.querySelectorAll('[data-contract-tab]').forEach(button=>button.onclick=()=>showContractLedger(data,button.dataset.contractTab));
  document.querySelector('#contract-dialog-files').onclick=()=>safe(()=>attachments('contracts',row.id,false,{back:()=>openContractLedger(row.id,kind),backLabel:'返回合同详情'}));
  document.querySelector('#contract-dialog-analysis').onclick=()=>safe(async()=>{
    state.scope={contract_id:row.id,project_id:state.project?Number(state.project):null};
    state.selectedAnalysis=null;
    await closeEditor();
    location.hash='cashflow';
  });
  document.querySelector('#contract-dialog-edit')?.addEventListener('click',()=>safe(()=>editRecord('contracts',row.id)));
  document.querySelector('#contract-dialog-allocation')?.addEventListener('click',()=>safe(()=>editRecord('allocations',null,{contract_id:row.id})));
  document.querySelector('#contract-dialog-settlement')?.addEventListener('click',()=>safe(()=>editRecord('settlements',null,{contract_id:row.id,project_id:data.project_id || (data.allocations.length===1?data.allocations[0].project_id:undefined)})));
  form.querySelectorAll('[data-contract-fact]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(button.dataset.module,Number(button.dataset.contractFact),{back:()=>openContractLedger(row.id,kind),backLabel:'返回合同'+metric.label+'明细'})));
  form.querySelectorAll('[data-contract-fact-files]').forEach(button=>button.onclick=()=>safe(()=>attachments(button.dataset.module,Number(button.dataset.contractFactFiles))));
  bindOperationActions(form);
}
