'use strict';

async function renderCostLedger(parent, module) {
  const data = await api('dashboard?' + query({view:'cost',project_id:state.project,month:state.costMonth || 'all'}));
  const columns = [['business_date','日期'],['allocation_project_names','归属项目'],['source_no','来源编号'],['source_type','成本来源'],['category','类别'],['counterparty','对方 / 工人'],['amount_minor','成本金额']];
  const months = [...new Set([...(data.period==='all'?[]:[data.period]),...data.months])];
  const pages = Math.max(1,Math.ceil(data.rows.length/40));
  state.costPage = Math.min(state.costPage || 0,pages-1);
  const amounts = {
    total_cost_minor:data.summary.total_minor,
    purchase_minor:data.summary.purchase_minor,
    labor_minor:data.summary.labor_minor,
    manual_minor:data.summary.manual_minor,
  };
  const create = state.boot.read_only?'':'<button id="cost-ledger-create" class="primary">登记其他费用</button>';
  const actions = row => {
    const source = {purchase:'purchases',labor:'worklogs',manual:'costs'}[row.source_type];
    if (!source) return '';
    const spec = state.boot.schema[source];
    const button = (action,label) => `<button data-cost-module="${source}" data-cost-record="${row.id}" data-cost-action="${action}">${label}</button>`;
    return button('detail','来源明细') + (spec.can_update && !state.boot.read_only?button('edit','修改原记录'):'') + (spec.attachment?button('attachments','附件'):'');
  };
  main.innerHTML = header('成本台账','汇总与明细使用同一期间、同一项目范围。修改来源记录后，成本与核算同步更新。',create) +
    tabsHTML(parent,module) + `<p class="page-note">采购和工天自动计入成本，不要重复登记；这里只新增其他费用。<a href="#purchases">登记采购</a> · <a href="#labor">登记工天</a></p><section class="card"><div class="filter-bar"><label>统计期间<select id="cost-ledger-period"><option value="all" ${data.period==='all'?'selected':''}>全部期间（全周期）</option>${months.map(month=>`<option value="${esc(month)}" ${month===data.period?'selected':''}>${esc(month)}</option>`).join('')}</select></label><p class="page-note">${esc(data.period_label)} · 项目筛选时只计本项目分摊份额；修改入口编辑原整笔记录，不重复登记成本或付款。</p></div></section>` +
    metricCards(amounts) + table(data.rows.slice(state.costPage*40,(state.costPage+1)*40),columns,actions,'cost-ledger-records') +
    `<div class="pagination"><button id="cost-ledger-prev" ${state.costPage===0?'disabled':''}>上一页</button><span>第 ${state.costPage+1} / ${pages} 页 · 共 ${data.rows.length} 条，汇总包含当前期间全部明细</span><button id="cost-ledger-next" ${state.costPage+1===pages?'disabled':''}>下一页</button></div>`;
  bindTabs(parent);
  document.querySelector('#cost-ledger-period').onchange = event => {
    state.costMonth=event.target.value;state.costPage=0;safe(()=>renderRoute(false));
  };
  if (create) document.querySelector('#cost-ledger-create').onclick=()=>safe(()=>editRecord('costs'));
  for (const [id,direction] of [['cost-ledger-prev',-1],['cost-ledger-next',1]]) {
    document.querySelector('#'+id).onclick=()=>{state.costPage+=direction;safe(()=>renderRoute(false));};
  }
  main.querySelectorAll('[data-cost-action]').forEach(button=>button.onclick=()=>safe(()=>{
    const module=button.dataset.costModule,id=Number(button.dataset.costRecord);
    if(button.dataset.costAction==='edit')return editRecord(module,id);
    if(button.dataset.costAction==='attachments')return attachments(module,id);
    return detailRecord(module,id);
  }));
}
