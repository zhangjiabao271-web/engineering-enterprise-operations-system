'use strict';

// These view preferences intentionally do not use the generic list's page/keyword.
function partnerPreferences(module) {
  state.partnerOverview ||= {};
  state.partnerOverview[module] ||= {year:'', keyword:'', status:'', kind:'', page:0};
  return state.partnerOverview[module];
}

function partnerLink(route, id, filters={}) {
  const key = route === 'projects' ? 'customer' : 'supplier';
  return `#${route}?${query({[key]:id,...filters})}`;
}

function partnerMoneyLink(row, key, module, year) {
  const route = module === 'customers' ? 'projects' : 'purchases';
  const filters = module === 'suppliers' ? {year} : {};
  const note = module === 'customers' ? '查看原项目全周期台账' : `查看 ${year} 年采购台账`;
  return `<a class="partner-money" href="${esc(partnerLink(route,row.id,filters))}" aria-label="${esc(row.name+'，'+money(row[key])+'，'+note)}">${esc(money(row[key]))}</a>`;
}

function partnerShare(value, amount, total, label) {
  if (value === null || value === undefined) return `<span class="partner-muted">${esc(label)}：暂无完整分母</span>`;
  const percent = (Number(value) / 100).toFixed(1);
  return `<div class="partner-share"><span>${esc(label)} ${percent}%</span><small>${esc(money(amount))} / ${esc(money(total))}</small><meter class="partner-share-track" min="0" max="10000" value="${Math.max(0,Math.min(10000,Number(value)))}" aria-label="${esc(label+' '+percent+'%')}"></meter></div>`;
}

function partnerRecent(row) {
  const names = {settlements:'收入确认',receipts:'到账回款',invoices:'开票',purchases:'采购',historical:'旧账回款'};
  if (!row.recent_activity.length) return '<span class="partner-muted">尚无业务往来</span>';
  return row.recent_activity.map(event => {
    const label = `${event.date} · ${names[event.module] || '业务'} ${money(event.amount_minor)}`;
    if (event.module === 'historical') return `<a href="#finance?tab=historical">${esc(label)}</a>`;
    const note = event.module==='receipts' ? ' · 本客商分配份额，详情为整笔到账' : '';
    return `<button type="button" class="partner-text" data-partner-record="${esc(event.module)}" data-record-id="${event.id}" title="${esc(event.number+note)}">${esc(label)}</button>`;
  }).join('');
}

function partnerActions(module,row) {
  const spec = state.boot.schema[module];
  let html = `<button type="button" data-partner-profile="${row.id}">档案</button>`;
  if (spec.can_update && !state.boot.read_only) html += `<button type="button" data-partner-edit="${row.id}">编辑</button>`;
  if (module === 'customers') {
    html += `<a href="${esc(partnerLink('projects',row.id))}">项目业务</a><a href="#contracts?${query({customer:'customer:'+row.id})}">合同</a>`;
    if (row.historical_projects.length) html += '<a href="#finance?tab=historical">独立旧账</a>';
  } else {
    html += `<a href="${esc(partnerLink('offers',row.id))}">报价</a><a href="${esc(partnerLink('purchases',row.id))}">采购</a><a href="#finance?${query({tab:'input_invoices',supplier:row.id})}">进项票</a>`;
  }
  const action=module==='customers'?'customer_deactivate':'supplier_deactivate';
  const definition=state.boot.operations?.[action];
  if (!state.boot.read_only && row.status!=='inactive' && definition) {
    html+=`<details class="operation-menu"><summary>档案操作</summary><div><button type="button" data-operation="${action}" data-id="${row.id}">${esc(definition.title)}</button></div></details>`;
  }
  return html;
}

function partnerTable(rows,module,summary,year) {
  const customer = module === 'customers';
  const columns = customer ? [
    ['name','客户 / 档案状态'],['yearly_business_minor','年度收入'],['yearly_receipt_minor','年度实际回款'],
    ['current_receivable_minor','全周期未收'],['active_project_count','在做项目'],
    ['shares','贡献 / 未收占比'],['recent','最近往来'],['contact','联系资料'],
  ] : [
    ['name','供应商 / 档案状态'],['category','主营类别'],['yearly_purchase_minor','年度采购额'],
    ['purchase_count','年度采购单数'],['shares','采购占比'],['recent','最近采购'],['contact','联系资料'],
  ];
  const holder = document.createElement('div');
  holder.innerHTML = table(rows,columns,row=>partnerActions(module,row),'partner-records',module);
  holder.querySelectorAll('tbody tr').forEach((tr,index) => {
    const row = rows[index];
    const cell = key => tr.querySelector(`[data-column="${key}"]`);
    columns.forEach(([key,label])=>{const td=cell(key);td.dataset.label=label;td.removeAttribute('title');});
    const roles = [];
    if (row.roles.includes('customer')) roles.push('客户');
    if (row.roles.includes('supplier')) roles.push('供应商');
    const status = {active:'已启用',pending:'档案待确认',inactive:'档案已停用'}[row.status] || '档案状态待核对';
    cell('name').innerHTML = `<button type="button" class="partner-name" data-partner-profile="${row.id}">${esc(row.name)}</button><small>${esc(roles.join(' / '))} · ${esc(status)}</small>`;
    cell('contact').innerHTML = `<span>${esc(row.contact || '联系人待补充')}</span><small>${esc(row.contact_phone || '电话待补充')}</small>`;
    cell('recent').innerHTML = partnerRecent(row);
    tr.querySelector('.table-actions').dataset.label='操作';
    if (customer) {
      for (const key of ['yearly_business_minor','yearly_receipt_minor','current_receivable_minor']) cell(key).innerHTML=partnerMoneyLink(row,key,module);
      cell('yearly_business_minor').insertAdjacentHTML('beforeend','<small>按收入确认日期</small>');
      cell('yearly_receipt_minor').insertAdjacentHTML('beforeend',`<small>按实际到账日期${BigInt(row.yearly_historical_receipt_minor || 0)>0n?' · 含旧账 '+esc(money(row.yearly_historical_receipt_minor)):''}</small>`);
      let balanceNote = '逐项目计算，不跨项目抵扣';
      if (row.historical_projects.length) {
        // A total spanning engineering and old debt has no single detail view.
        // Link the explicit components instead of implying projects contain old debt.
        cell('current_receivable_minor').innerHTML=`<strong>${esc(money(row.current_receivable_minor))}</strong><small><a href="${esc(partnerLink('projects',row.id))}">工程 ${esc(money(row.project_receivable_minor))}</a> · <a href="#finance?tab=historical">旧账 ${esc(money(row.historical_receivable_minor))}</a></small>`;
        if (BigInt(row.yearly_historical_receipt_minor || 0)>0n) {
          const projectReceipts=BigInt(row.yearly_receipt_minor)-BigInt(row.yearly_historical_receipt_minor);
          cell('yearly_receipt_minor').innerHTML=`<strong>${esc(money(row.yearly_receipt_minor))}</strong><small><a href="${esc(partnerLink('projects',row.id))}">工程到账 ${esc(money(projectReceipts))}</a> · <a href="#finance?tab=historical">旧账到账 ${esc(money(row.yearly_historical_receipt_minor))}</a></small>`;
        }
      }
      if (!row.receivable_complete) balanceNote += ` · ${row.historical_unknown_count} 个旧账总额待确认，未收不完整`;
      if (BigInt(row.pending_receipt_minor || 0)>0n) balanceNote += ` · 预收/待分配 ${money(row.pending_receipt_minor)} 未抵减`;
      cell('current_receivable_minor').insertAdjacentHTML('beforeend',`<small>${esc(balanceNote)}</small>`);
      cell('active_project_count').innerHTML=`<a href="${esc(partnerLink('projects',row.id))}">${row.active_project_count} 个</a><small>筹备中 / 进行中</small>`;
      cell('shares').innerHTML = partnerShare(row.income_share_bps,row.yearly_business_minor,summary.yearly_business_minor,'收入贡献')+
        partnerShare(row.receivable_share_bps,row.current_receivable_minor,summary.current_receivable_minor,'未收占比');
    } else {
      cell('yearly_purchase_minor').innerHTML=partnerMoneyLink(row,'yearly_purchase_minor',module,year)+'<small>含税、含运费</small>';
      cell('purchase_count').innerHTML=`<a href="${esc(partnerLink('purchases',row.id,{year}))}">${row.purchase_count} 单</a><small>多材料仍按一单</small>`;
      cell('shares').innerHTML=partnerShare(row.purchase_share_bps,row.yearly_purchase_minor,summary.yearly_purchase_minor,'采购占比');
    }
  });
  return holder.innerHTML;
}

function partnerMetrics(data,module) {
  const summary = data.summary;
  const keys = module === 'customers' ? ['yearly_business_minor','yearly_receipt_minor','current_receivable_minor','active_project_count'] :
    ['yearly_purchase_minor','purchase_count','purchasing_supplier_count','partner_count'];
  const labels = module === 'customers' ? ['年度收入（已确认）','年度实际回款','全周期未收（已知）','在做项目'] :
    ['年度采购额','年度采购单数','本年有采购供应商','当前范围供应商档案'];
  const hints = module === 'customers' ? [
    `${data.year} 年收入确认，不取合同签约额`,`${data.year} 年到账，有效分配及公司范围独立旧账`,
    summary.historical_unknown_count ? `${summary.historical_unknown_count} 个旧账总额待确认，余额不完整` : '全部已录入有效事实，逐项目独立计算',
    '筹备中 / 进行中，同一项目只计一次',
  ] : [
    `${data.year} 年采购日期，含税含运费${data.project_id?'，仅本项目分摊':''}`,
    '按采购单计数，不按材料行计数','当前年度至少有一张有效采购单','包含待确认和已停用档案',
  ];
  const holder = document.createElement('div');
  holder.innerHTML = metricCards(Object.fromEntries(keys.map(key=>[key,summary[key]])));
  holder.querySelectorAll('.metric').forEach((card,index)=>{
    card.querySelector('label').textContent=labels[index];
    card.querySelector('small').textContent=hints[index];
  });
  return holder.innerHTML;
}

function partnerBoundary(data,module) {
  const u = data.unattributed;
  const notes = [];
  if (module === 'customers') {
    notes.push('发票仅作往来证据，不当收入或欠款。预收及未关联收入的回款不冲减未收。');
    if (data.project_id) notes.push('当前只汇总所选工程项目；独立旧账及未分配到账未套用工程项目编号。');
    else notes.push('明确归属客户的独立旧账回款计入年度到账，已知旧账余额单列；旧账不形成工程收入。');
    if (BigInt(u.yearly_business_minor || 0)>0n || BigInt(u.yearly_receipt_minor || 0)>0n || BigInt(u.current_receivable_minor || 0)>0n) {
      notes.push(`客户待归属：年度收入 ${money(u.yearly_business_minor)}，年度回款 ${money(u.yearly_receipt_minor)}，全周期未收 ${money(u.current_receivable_minor)}，未摊入任何客户。`);
    }
    if (BigInt(u.unallocated_receipt_minor || 0)>0n) notes.push(`本年到账中 ${money(u.unallocated_receipt_minor)} 尚无有效分配，未虚构客户归属。`);
    if (u.overallocated_receipt_count) notes.push(`${u.overallocated_receipt_count} 笔到账分配超额，请在回款台账核对，勿将当前汇总作为已核准现金。`);
  } else {
    notes.push('采购已付款标记不等于银行现金流水；此页不推算应付或已支付现金。');
    if (data.project_id) notes.push('当前项目采购额只计有效分摊份额，采购单数为涉及本项目的去重单数。');
    if (u.purchase_count) notes.push(`本年 ${u.purchase_count} 单、${money(u.yearly_purchase_minor)} 未关联供应商档案，未按商户名称猜配。`);
  }
  const navigationNote=module==='customers' ? '收入／回款金额跳原项目全周期台账，不是当前年度明细；工程与旧账分别跳转。' : '年度采购额／单数跳所选年度采购，动作栏采购入口仍为全周期。';
  return `<details class="partner-boundary"><summary>口径与待归属事项${notes.length>2?' · 请核对':''}</summary>${notes.map(note=>`<p>${esc(note)}</p>`).join('')}<p>占比分母：${esc(data.share_basis)}。${esc(navigationNote)}</p><a href="#finance?tab=receipts">回款台账</a></details>`;
}

function partnerAttention(data,module) {
  const pending=Object.values(data.unattributed).some(value=>BigInt(value || 0)>0n);
  if (pending) return notice('存在待归属或待核对事实，未强行分配到客商。顶部只计已归属部分；请展开页尾“口径与待归属事项”核对。','warning');
  if (module==='customers' && data.summary.historical_unknown_count) {
    return notice('部分旧账总额尚未确认：当前未收仅是已知部分，未收占比暂不显示。','warning');
  }
  return '';
}

async function renderPartnerOverview(module) {
  if (!['customers','suppliers'].includes(module)) throw new Error('请选择客户或供应商');
  const preferences = partnerPreferences(module);
  const request = state.partnerOverviewRequest = (state.partnerOverviewRequest || 0) + 1;
  const project = state.project;
  const data = await api('dashboard?'+query({view:module,year:preferences.year,project_id:project}));
  if (request !== state.partnerOverviewRequest || project !== state.project || state.route !== module) return;
  preferences.year = String(data.year);
  const needle = preferences.keyword.trim().toLocaleLowerCase();
  const filtered = data.rows.filter(row=>(!preferences.status || row.status===preferences.status) &&
    (!needle || [row.name,row.short_name,row.partner_code,row.unified_credit_code,row.contact,row.contact_phone,row.category,row.notes]
      .some(value=>String(value || '').toLocaleLowerCase().includes(needle))));
  // The API JSON adapter serializes sets as arrays; never derive a second profile.
  data.rows.forEach(row=>{row.roles=Array.isArray(row.roles)?row.roles:[];});
  let tabs = '';
  let rows = filtered;
  if (module === 'suppliers') {
    const kinds = [['manufacturer','生产厂家'],['distributor','经销门店']];
    if (filtered.some(row=>row.supplier_kind==='unclassified')) kinds.push(['unclassified','待分类']);
    if (!kinds.some(([kind])=>kind===preferences.kind)) {
      preferences.kind=(kinds.find(([kind])=>filtered.some(row=>row.supplier_kind===kind)) || kinds[0])[0];
    }
    tabs = `<div class="tabs partner-tabs" aria-label="供应商分组">${kinds.map(([kind,label])=>`<button type="button" data-partner-kind="${kind}" aria-pressed="${preferences.kind===kind}" class="${preferences.kind===kind?'active':''}">${label}（${filtered.filter(row=>row.supplier_kind===kind).length}）</button>`).join('')}</div>`;
    rows=filtered.filter(row=>row.supplier_kind===preferences.kind);
  }
  preferences.page=Math.max(0,Math.min(preferences.page,Math.max(0,Math.ceil(rows.length/40)-1)));
  const spec=state.boot.schema[module];
  const title=module==='customers'?'客户经营概览':'供应商经营概览';
  const description=project?'所选项目的客商经营汇总；基础档案仍为全公司唯一资料。':'跨项目看经营与往来，同一客商可以同时是客户和供应商。';
  main.innerHTML=`<div class="partner-overview">${header(title,description,
    `${spec.can_create&&!state.boot.read_only?'<button type="button" class="primary" id="partner-new">新增档案</button>':''}<button type="button" id="partner-refresh">刷新</button>`)}
    ${partnerMetrics(data,module)}${partnerAttention(data,module)}
    <form class="filter-bar partner-filters" id="partner-filters"><label>经营年度<select id="partner-year">${data.years.map(year=>`<option value="${year}" ${String(year)===preferences.year?'selected':''}>${year} 年</option>`).join('')}</select></label>
    <label class="partner-search">搜索档案<input id="partner-search" value="${esc(preferences.keyword)}" placeholder="名称、联系人、电话或档案编号"></label>
    <label>档案状态<select id="partner-status"><option value="">全部状态</option>${[['active','已启用'],['pending','待确认'],['inactive','已停用']].map(([value,label])=>`<option value="${value}" ${value===preferences.status?'selected':''}>${label}</option>`).join('')}</select></label>
    <button type="submit">查询</button><button type="button" id="partner-reset">重置搜索</button></form>
    ${tabs}<p class="partner-scope">${rows.length} 个匹配档案 · 顶部合计与占比为${project?'当前项目':'全公司'}全部已归属客商，不随搜索、分组或分页变化</p>
    ${partnerTable(rows.slice(preferences.page*40,preferences.page*40+40),module,data.summary,data.year)}
    <div class="pagination"><span>第 ${preferences.page+1} / ${Math.max(1,Math.ceil(rows.length/40))} 页</span><button type="button" id="partner-prev" ${preferences.page===0?'disabled':''}>上一页</button><button type="button" id="partner-next" ${(preferences.page+1)*40>=rows.length?'disabled':''}>下一页</button></div>
    ${partnerBoundary(data,module)}</div>`;
  const refresh=()=>safe(()=>renderRoute(false));
  main.querySelector('#partner-refresh').onclick=refresh;
  main.querySelector('#partner-new')?.addEventListener('click',()=>safe(()=>editRecord(module)));
  main.querySelector('#partner-filters').onsubmit=event=>{
    event.preventDefault();
    preferences.year=main.querySelector('#partner-year').value;
    preferences.keyword=main.querySelector('#partner-search').value;
    preferences.status=main.querySelector('#partner-status').value;
    preferences.page=0;
    refresh();
  };
  main.querySelector('#partner-year').onchange=()=>main.querySelector('#partner-filters').requestSubmit();
  main.querySelector('#partner-status').onchange=()=>main.querySelector('#partner-filters').requestSubmit();
  main.querySelector('#partner-reset').onclick=()=>{preferences.keyword='';preferences.status='';preferences.page=0;refresh();};
  main.querySelector('#partner-prev').onclick=()=>{preferences.page--;refresh();};
  main.querySelector('#partner-next').onclick=()=>{preferences.page++;refresh();};
  main.querySelectorAll('[data-partner-kind]').forEach(button=>button.onclick=()=>{preferences.kind=button.dataset.partnerKind;preferences.page=0;refresh();});
  main.querySelectorAll('[data-partner-profile]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(module,Number(button.dataset.partnerProfile))));
  main.querySelectorAll('[data-partner-edit]').forEach(button=>button.onclick=()=>safe(()=>editRecord(module,Number(button.dataset.partnerEdit))));
  main.querySelectorAll('[data-partner-record]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(button.dataset.partnerRecord,Number(button.dataset.recordId))));
  bindOperationActions(main);
}
