'use strict';

let entityRecordsVersion = 0;
let entityRecords = [];
const entityRegistryView = {kind:'contracts',status:'pending',search:'',page:0};
const entitySelected = new Set();
const entityMetricOrder = ['income','receipts','outstanding','costs','margin','invoices'];

function entityMetricButton(key, value, identity, definition) {
  return `<button type="button" class="entity-value" data-entity-metric="${key}" data-entity-id="${identity}" title="${esc(definition.definition)}"><span>${esc(definition.label)}</span><strong>${esc(money(value))}</strong><small>查看明细 →</small></button>`;
}

function entityTaxPanel(entity, overview) {
  const taxKeys = ['output_tax','input_gross','input_tax','deducted_tax'];
  let content=notice('进项票属于实际购买主体，不把整张多项目发票重复归入当前项目；切回全部项目查看。');
  if(overview.input_scope_available){
    const values=taxKeys.map(key=>entityMetricButton(key,entity.metrics[key+'_minor'],entity.id,overview.definitions[key])).join('');
    content=`<div class="entity-tax-values">${values}</div>`;
  }
  return `<details class="entity-tax-details">
    <summary>票据与进项税 <span>${overview.input_scope_available?'查看主体票据':'当前为项目范围'}</span></summary>
    ${content}<p class="page-note">税额供核对，不自动计算应缴税。进项票不重复增加成本，也不会在未确认抵扣时减少成本。</p>
  </details>`;
}

function entityCompanyCard(entity, overview) {
  const tax = entity.tax_identity==='general'?'一般纳税人':'小规模纳税人';
  const metrics = entityMetricOrder.map(key=>entityMetricButton(key,entity.metrics[key+'_minor'],entity.id,overview.definitions[key])).join('');
  return `<section class="card entity-company" data-company="${entity.id}">
    <div class="entity-company-heading"><div><span class="entity-tax-identity">${tax}${entity.active?'':' · 已停用'}</span><h2>${esc(entity.name)}</h2></div>
      <span class="entity-project-count">${entity.project_count} 个项目<small>在营 ${entity.active_project_count} · 待收款 ${entity.outstanding_project_count}</small></span></div>
    <div class="entity-values">${metrics}</div>${entityTaxPanel(entity,overview)}
    <div class="entity-company-footer"><span>${entity.tax_number?'税号已维护':'税号尚未维护'}</span>
      <button type="button" data-entity-tax="${entity.id}" ${state.boot.read_only?'disabled':''}>维护税号</button></div>
  </section>`;
}

function entityIncomeComparison(overview) {
  const total = BigInt(overview.total_metrics.income_minor);
  const parts = overview.entities.map(entity=>({name:entity.name,amount:entity.metrics.income_minor,id:entity.id}));
  if (BigInt(overview.pending_metrics.income_minor)>0n) parts.push({name:'待确认归属',amount:overview.pending_metrics.income_minor,id:''});
  const rows = parts.map((row,index)=>({...row,series:index,percent:total>0n?Number(BigInt(row.amount)*10000n/total)/100:0}));
  let content='<p class="page-note">当前范围还没有确认收入。</p>';
  if(total>0n){
    const track=rows.filter(row=>row.percent>0).map(row=>`<span class="entity-series-${row.series}" data-entity-width="${row.percent}"></span>`).join('');
    const legend=rows.map(row=>`<button type="button" data-entity-metric="income" data-entity-id="${row.id}"><span>${esc(row.name)}</span><strong>${esc(money(row.amount))}</strong><small>${row.percent.toFixed(1)}%</small></button>`).join('');
    content=`<div class="entity-income-track" aria-hidden="true">${track}</div><div class="entity-income-legend">${legend}</div>`;
  }
  return `<section class="card entity-comparison"><div class="section-head"><h2>项目收入归属</h2><small>同一项目不重复计入多个主体</small></div>${content}</section>`;
}

async function renderEntities() {
  const overview = await api('operation_read?'+query({name:'entity_overview',project_id:state.project}));
  const counts = overview.counts;
  const pending = counts.pending + counts.conflict;
  main.innerHTML = header('经营主体', `${overview.scope} · 全周期经营事实，含已完工项目`,
    '<button type="button" id="entity-refresh">刷新</button><button type="button" class="primary" id="entity-start-review">处理业务归属</button>') +
    `<div class="entity-page-context"><span>合同确认一次，关联项目自动沿用</span><span>无合同的零星工程单独确认</span></div><section class="metrics entity-overview-metrics">${['income','receipts','outstanding'].map(key=>entityMetricButton(key,overview.total_metrics[key+'_minor'],'all',overview.definitions[key])).join('')}<button type="button" class="entity-value entity-pending-card" id="entity-pending-card"><span>待处理归属</span><strong>${pending}<small> 条</small></strong><small>待确认 ${counts.pending} · 冲突 ${counts.conflict}</small></button></section>` +
    (pending?`<div class="notice warning entity-scope-warning">下面两家主体只计入归属明确的业务。仍待归属的项目收入 ${esc(money(overview.pending_metrics.income_minor))}、项目成本 ${esc(money(overview.pending_metrics.costs_minor))}，单列待处理，不默认归到示例钢构或示例安装。</div>`:'')+
    `<div class="entity-companies">${overview.entities.map(entity=>entityCompanyCard(entity,overview)).join('')}</div>`+
    `<p class="entity-accounting-note">项目经营数据按合同／无合同项目的主体归属统计；销项票按票据主体单独统计，允许看出两者差异。账面毛利 = 确认收入 − 已登记含税项目成本，不是净利润或现金余额。独立历史旧账不计作本期工程收入。</p>`+
    entityIncomeComparison(overview)+
    `<section class="card entity-registry" id="entity-registry"><div class="section-head"><div><h2>业务归属台账</h2><p>已确认 ${counts.confirmed} 条 · 已自动归属 ${counts.inherited} 条 · 待确认 ${counts.pending} 条 · 冲突 ${counts.conflict} 条</p></div></div><div class="entity-registry-controls"><label>业务类型<select id="entity-kind">${overview.kinds.map(kind=>`<option value="${kind.kind}" ${entityRegistryView.kind===kind.kind?'selected':''}>${esc(kind.label)} · 待处理 ${kind.pending+kind.conflict}</option>`).join('')}</select></label><label>归属状态<select id="entity-status"><option value="pending">待处理（待确认／冲突）</option><option value="all">全部记录</option><option value="confirmed">已人工确认</option><option value="inherited">已自动归属</option><option value="conflict">仅归属冲突</option></select></label><label class="entity-registry-search">搜索业务<input type="search" id="entity-search" placeholder="名称、项目或单据号" value="${esc(entityRegistryView.search)}"></label></div><div class="entity-registry-batch"><button type="button" id="entity-select-all">全选当前筛选</button><button type="button" id="entity-clear-selection">取消勾选</button><span id="entity-selection-count" role="status">已选择 0 条</span><button type="button" class="primary" id="entity-confirm-selected" disabled>确认所选归属</button></div><p id="entity-registry-count" class="page-note" role="status"></p><div id="entity-registry-records"><div class="loading">正在核对业务归属…</div></div></section>`;
  main.querySelector('#entity-status').value = entityRegistryView.status;
  main.querySelectorAll('[data-entity-metric]').forEach(button=>button.onclick=()=>safe(()=>openEntityDetail(button.dataset.entityMetric,button.dataset.entityId)));
  main.querySelectorAll('[data-entity-width]').forEach(part=>part.style.width=Number(part.dataset.entityWidth)+'%');
  main.querySelectorAll('[data-entity-tax]').forEach(button=>button.onclick=()=>safe(()=>openOperation('entity_tax',{entity_id:Number(button.dataset.entityTax)})));
  main.querySelector('#entity-refresh').onclick=()=>safe(()=>renderRoute(false));
  const showPending = ()=>{
    entityRegistryView.status='pending';main.querySelector('#entity-status').value='pending';
    entityRegistryView.page=0;entitySelected.clear();renderEntityRecords();
    main.querySelector('#entity-registry').scrollIntoView({block:'start'});
  };
  main.querySelector('#entity-start-review').onclick=showPending;
  main.querySelector('#entity-pending-card').onclick=showPending;
  main.querySelector('#entity-kind').onchange=event=>safe(async()=>{
    entityRegistryView.kind=event.target.value;entityRegistryView.page=0;entitySelected.clear();await loadEntityRecords();
  });
  main.querySelector('#entity-status').onchange=event=>{
    entityRegistryView.status=event.target.value;entityRegistryView.page=0;entitySelected.clear();renderEntityRecords();
  };
  main.querySelector('#entity-search').oninput=event=>{
    entityRegistryView.search=event.target.value.trim();entityRegistryView.page=0;entitySelected.clear();renderEntityRecords();
  };
  main.querySelector('#entity-select-all').onclick=()=>{
    for (const row of entityMatchingRecords()) if(row.assignable)entitySelected.add(row.id);
    renderEntityRecords();
  };
  main.querySelector('#entity-clear-selection').onclick=()=>{entitySelected.clear();renderEntityRecords();};
  main.querySelector('#entity-confirm-selected').onclick=()=>safe(()=>openEntityAssignment(entityRecords.filter(row=>entitySelected.has(row.id))));
  await loadEntityRecords();
}

function entityMatchingRecords() {
  return entityRecords.filter(row=>{
    const matches = entityRegistryView.status==='all' ||
      (entityRegistryView.status==='pending'?['pending','conflict'].includes(row.ownership_status):row.ownership_status===entityRegistryView.status);
    const text = [row.label,row.projects,row.date,row.entity_name,row.ownership_source].join(' ').toLowerCase();
    return matches && text.includes(entityRegistryView.search.toLowerCase());
  });
}

async function loadEntityRecords() {
  const version = ++entityRecordsVersion;
  entityRecords=[];entitySelected.clear();updateEntitySelection();
  main.querySelector('#entity-registry-records').innerHTML='<div class="loading" role="status">正在核对业务归属…</div>';
  const rows = await api('operation_read?'+query({name:'entity_records',kind:entityRegistryView.kind,project_id:state.project}));
  if (version!==entityRecordsVersion || state.route!=='entities') return;
  entityRecords=rows;entitySelected.clear();renderEntityRecords();
}

function entityRegistryRowHTML(row) {
  let statusClass='';
  if(row.ownership_status==='conflict')statusClass='conflict';
  else if(row.entity_id)statusClass='good';
  let action=`<button type="button" data-entity-contracts="${row.id}">查看关联合同</button>`;
  if(row.assignable){
    const label=row.ownership_status==='confirmed'?'更正归属':'确认归属';
    action=`<button type="button" data-entity-assign="${row.id}" ${state.boot.read_only?'disabled':''}>${label}</button>`;
  }
  const project=row.projects&&row.projects!==row.label?row.projects:'';
  const amount=row.amount_minor===null?'—':money(row.amount_minor);
  return `<tr>
    <td><input type="checkbox" aria-label="选择${esc(row.label)}" data-entity-select="${row.id}" ${entitySelected.has(row.id)?'checked':''} ${!row.assignable||state.boot.read_only?'disabled':''}></td>
    <td><strong>${esc(row.label)}</strong><small>${esc(project)} ${esc(row.date||'')}</small></td>
    <td><span class="status ${statusClass}">${esc(row.ownership_label)}</span><small>${esc(row.entity_name)}</small></td>
    <td>${esc(row.ownership_source||'待核对')}<small>${esc(row.ownership_reason)}</small></td>
    <td class="amount">${esc(amount)}</td>
    <td class="table-actions">${action}<button type="button" data-entity-history="${row.id}">依据与历史</button></td>
  </tr>`;
}

function renderEntityRecords() {
  const host=main.querySelector('#entity-registry-records');
  if(!host)return;
  const matches = entityMatchingRecords();
  const pages=Math.max(1,Math.ceil(matches.length/50));
  entityRegistryView.page=Math.min(entityRegistryView.page,pages-1);
  const rows=matches.slice(entityRegistryView.page*50,(entityRegistryView.page+1)*50);
  main.querySelector('#entity-registry-count').textContent=`当前筛选 ${matches.length} 条。已自动归属的记录无需重复确认；有合同的项目请从合同处理。`;
  const body=rows.map(entityRegistryRowHTML).join('');
  host.innerHTML = rows.length?`<div class="table-wrap entity-registry-table"><table><thead><tr><th scope="col">选择</th><th scope="col">业务记录</th><th scope="col">归属状态 / 主体</th><th scope="col">归属依据</th><th scope="col">单据金额</th><th scope="col">操作</th></tr></thead><tbody>${body}</tbody></table></div>`:`<div class="empty"><strong>${entityRegistryView.status==='pending'?'这类业务没有待处理归属':'没有匹配的业务记录'}</strong><span>切换业务类型或选择“全部记录”，即可查看已确认及已自动归属的业务。</span></div>`;
  if(pages>1)host.insertAdjacentHTML('beforeend',`<div class="pagination"><span>第 ${entityRegistryView.page+1} / ${pages} 页 · 每页50条</span><button type="button" id="entity-previous" ${!entityRegistryView.page?'disabled':''}>上一页</button><button type="button" id="entity-next" ${entityRegistryView.page===pages-1?'disabled':''}>下一页</button></div>`);
  host.querySelector('#entity-previous')?.addEventListener('click',()=>{entityRegistryView.page--;renderEntityRecords();});
  host.querySelector('#entity-next')?.addEventListener('click',()=>{entityRegistryView.page++;renderEntityRecords();});
  host.querySelectorAll('[data-entity-select]').forEach(input=>input.onchange=()=>{
    input.checked?entitySelected.add(Number(input.dataset.entitySelect)):entitySelected.delete(Number(input.dataset.entitySelect));updateEntitySelection();
  });
  host.querySelectorAll('[data-entity-assign]').forEach(button=>button.onclick=()=>safe(()=>openEntityAssignment(entityRecords.filter(row=>row.id===Number(button.dataset.entityAssign)))));
  host.querySelectorAll('[data-entity-contracts]').forEach(button=>button.onclick=()=>safe(()=>openEntityContracts(entityRecords.find(row=>row.id===Number(button.dataset.entityContracts)))));
  host.querySelectorAll('[data-entity-history]').forEach(button=>button.onclick=()=>safe(()=>openEntityOwnershipHistory(entityRecords.find(row=>row.id===Number(button.dataset.entityHistory)))));
  updateEntitySelection();
}

function updateEntitySelection() {
  main.querySelector('#entity-selection-count').textContent=`已选择 ${entitySelected.size} 条`;
  main.querySelector('#entity-confirm-selected').disabled=!entitySelected.size || state.boot.read_only;
  main.querySelector('#entity-select-all').disabled=state.boot.read_only || !entityMatchingRecords().some(row=>row.assignable);
  main.querySelector('#entity-clear-selection').disabled=!entitySelected.size;
}

async function openEntityAssignment(rows) {
  if(!rows.length || rows.some(row=>!row.assignable))throw new Error('请选择可确认的业务记录；有合同的项目请先确认合同。');
  const request=beginEditorRequest();
  const options=await request.read('options');
  const kind=rows[0].kind;
  const ids=rows.map(row=>row.id);
  const title=`确认${rows[0].kind_label}归属 · ${rows.length}条`;
  openEditor(title,
    notice(kind==='contracts'?'合同确认后，关联项目会自动沿用。不重复填写项目归属，也不改变收入、回款或成本金额。':'只确认所选记录的实际经营主体；不改动金额、日期或项目分配。')+
    table(rows,[['label','所选业务'],['entity_name','当前主体'],['ownership_label','当前状态']],null,'entity-assignment-selected')+
    `<div class="form-grid entity-assignment-form">${fieldHTML({name:'entity_id',label:'实际经营主体',kind:'select',source:'entities',required:true},'',options)}${fieldHTML({name:'reason',label:'确认依据 / 更正原因',kind:'textarea',required:true},'',options)}</div>`,async()=>{
      const values={kind,ids,entity_id:form.elements.entity_id.value,reason:form.elements.reason.value};
      const result=await api('operation_preview',{name:'entity_assign',record:values});
      const projects=result.review.ownership_projects || [];
      openEditor('确认经营主体 · 预览',notice(result.review.notice)+
        table(result.review.rows,[['label','所选业务'],['before_entity','原归属'],['after_entity','确认后归属'],['change','操作']],null,'entity-assignment-preview')+
        `<section class="entity-project-impact"><h3>关联项目影响 · ${projects.length}个</h3>${projects.length?table(projects,[['project_name','关联项目'],['before_entity','原归属'],['after_entity','确认后归属'],['ownership_label','状态'],['ownership_reason','说明']],null,'entity-project-preview'):notice('本次没有项目归属变化。已确认相同主体的记录不会重复写入。')}</section>`+
        `<p class="page-note">确认依据：${esc(values.reason)}</p><label class="confirm-check"><input name="entity-confirmed" type="checkbox" required>已核对所选记录、实际主体和关联项目影响，确认执行。</label>`,async()=>{
          const applied=await api('operation_apply',{token:result.token,confirmed:form.elements.namedItem('entity-confirmed').checked});
          cancelEditor=null;editor.close();
          const derived=projects.filter(row=>row.ownership_label==='已自动归属').length;
          toast(applied?.updated_count===0?'这些记录已经确认相同主体，未重复写入。':`已确认 ${applied?.updated_count ?? rows.length} 条归属${derived?`，${derived} 个项目已自动沿用`:''}。可在“全部记录／已人工确认”查看。`);
          entitySelected.clear();await refreshOptions();await renderRoute(false);
        },{label:'确认归属',cancel:()=>api('operation_discard',{token:result.token})});
    },{label:'预览归属变化'});
}

async function openEntityContracts(row) {
  const request=beginEditorRequest();
  const contracts=(await request.read('operation_read?'+query({name:'entity_records',kind:'contracts'}))).filter(item=>row.contract_ids.includes(item.id));
  openEditor('关联合同 · '+row.label,notice('项目沿用这些合同的主体。请在合同处确认或更正，项目无需重复归集。')+
    table(contracts,[['label','关联合同'],['entity_name','经营主体'],['ownership_label','归属状态']],item=>`<button type="button" data-linked-entity-contract="${item.id}" ${state.boot.read_only?'disabled':''}>${item.entity_id?'更正合同主体':'确认合同主体'}</button>`,'entity-linked-contracts'),null,{cancelLabel:'返回归属台账'});
  editor.querySelectorAll('[data-linked-entity-contract]').forEach(button=>button.onclick=()=>safe(()=>openEntityAssignment(contracts.filter(item=>item.id===Number(button.dataset.linkedEntityContract)))));
}

async function openEntityOwnershipHistory(row) {
  const request=beginEditorRequest();
  const history=await request.read('operation_read?'+query({name:'entity_history',kind:row.kind,id:row.id}));
  openEditor('归属依据 · '+row.label,`<div class="detail-grid"><div class="detail-cell"><small>当前归属</small><p>${esc(row.entity_name)} · ${esc(row.ownership_label)}</p></div><div class="detail-cell"><small>归属依据</small><p>${esc(row.ownership_source)} · ${esc(row.ownership_reason)}</p></div></div>`+
    (row.contract_names?.length?notice('关联的合同：'+row.contract_names.join('、')):'')+
    `<h3 class="entity-history-title">人工确认历史</h3>${history.length?table(history,[['created_at','确认时间'],['before_entity','原主体'],['after_entity','确认主体'],['reason','确认依据']],null,'entity-ownership-history'):notice(row.ownership_status==='inherited'?'本条业务自动沿用明确关系，没有重复生成一条人工确认记录；合同确认记录可在关联合同的依据与历史中查看。':'尚未有人工确认历史。')}`,null,{cancelLabel:'返回归属台账'});
}

async function openEntityDetail(metric, identity) {
  openEditor('正在读取经营明细','<div class="loading" role="status">正在核对主体和对应记录…</div>',null,{cancelLabel:'返回经营主体'});
  const request=beginEditorRequest();
  const data=await request.read('operation_read?'+query({name:'entity_detail',metric,entity_id:identity,project_id:state.project}));
  const columns=[['date','日期'],['project','项目'],['number','单据 / 项目编号'],['description','说明'],['amount_minor',data.label]];
  openEditor(data.label+'明细',`<div class="entity-detail-summary"><div><small>${esc(data.entity_name)} · ${esc(data.scope)}</small><strong>${esc(money(data.total_minor))}</strong></div><span>${data.rows.length} 条明细</span></div><p class="page-note">${esc(data.definition)}</p><label class="entity-detail-search">筛选明细<input type="search" id="entity-detail-search" placeholder="项目、日期或单号"></label><p id="entity-detail-count" class="page-note" role="status"></p><div id="entity-detail-rows"></div>`,null,{cancelLabel:'返回经营主体'});
  let page=0;
  const render=keyword=>{
    const detail=cockpitDetailPage(data.rows,columns,keyword,page);page=detail.page;
    editor.querySelector('#entity-detail-count').textContent=`当前匹配 ${detail.count} 条 · 合计 ${money(detail.total)}`;
    const host=editor.querySelector('#entity-detail-rows');
    host.innerHTML=table(detail.rows,columns,row=>row.project_id?`<button type="button" data-entity-detail-project="${row.project_id}">项目经营明细</button>`:'','entity-detail-table');
    if(detail.pages>1)host.insertAdjacentHTML('beforeend',`<div class="pagination"><span>第 ${page+1} / ${detail.pages} 页</span><button type="button" id="entity-detail-previous" ${page===0?'disabled':''}>上一页</button><button type="button" id="entity-detail-next" ${page===detail.pages-1?'disabled':''}>下一页</button></div>`);
    host.querySelector('#entity-detail-previous')?.addEventListener('click',()=>{page--;render(keyword);});
    host.querySelector('#entity-detail-next')?.addEventListener('click',()=>{page++;render(keyword);});
    host.querySelectorAll('[data-entity-detail-project]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.entityDetailProject),'settlements',{back:()=>openEntityDetail(metric,identity),backLabel:'返回主体经营明细'})));
  };
  render('');editor.querySelector('#entity-detail-search').oninput=event=>{page=0;render(event.target.value.trim());};
}
