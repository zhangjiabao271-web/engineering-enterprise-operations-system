'use strict';

function cockpitCards(metrics) {
  const cards = metrics.map(item=>{
    const amount = esc(money(item.amount_minor));
    return `<button type="button" class="cockpit-metric" data-cockpit-detail="${esc(item.key)}"
      title="${esc(item.definition)}" aria-label="${esc(item.label)} ${amount}，查看明细">
      <span class="cockpit-metric-label">${esc(item.label)}<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 18 18 6M6 6h12v12"/></svg></span>
      <strong>${amount}</strong><span class="cockpit-metric-note">${esc(item.note)}</span>
      <span class="cockpit-metric-link">查看明细</span></button>`;
  }).join('');
  return `<div class="cockpit-cards">${cards}</div>`;
}

function cockpitTrend(rows) {
  const series = [['income_minor','确认收入','income'],['receipt_minor','项目回款','receipts'],['cost_minor','项目成本','costs']];
  const maximum = Math.max(1,...rows.flatMap(row=>series.map(([key])=>Number(row[key]))));
  const legend = series.map(([,label],index)=>`<span><i class="cockpit-series-${index}"></i>${label}</span>`).join('');
  const groups = rows.map(row=>`<div class="cockpit-month"><div class="cockpit-bars">${series.map(([key,label,metric],index)=>`<button type="button" class="cockpit-bar cockpit-series-${index}" data-cockpit-bar-height="${Number(row[key])/maximum*100}" data-cockpit-detail="${metric}" data-cockpit-month="${row.month}" title="${row.month} ${label} ${money(row[key])}" aria-label="${row.month} ${label} ${money(row[key])}，查看本月明细"></button>`).join('')}</div><span>${esc(row.month.slice(2).replace('-','年'))}月</span></div>`).join('');
  return `<section class="card cockpit-trend"><div class="section-head"><h2>近六个月经营走势</h2><small>按各自发生日期 · 末月截至当前记录</small></div><div class="cockpit-legend">${legend}</div><div class="cockpit-chart"><div class="cockpit-chart-scale"><span>${esc(money(Math.round(maximum)))}</span><span>¥0.00</span></div><div class="cockpit-chart-months">${groups}</div></div></section>`;
}

function cockpitComposition(rows) {
  const total = rows.reduce((sum,row)=>sum+BigInt(row.amount_minor),0n);
  const portions = rows.map((row,index)=>({ ...row,index,percent:total>0n?Number(BigInt(row.amount_minor)*10000n/total)/100:0 }));
  return `<section class="card cockpit-composition"><div class="section-head"><h2>项目成本花在哪里</h2><button type="button" class="quiet" data-cockpit-detail="costs">成本明细</button></div>${total===0n?'<p class="cockpit-empty">当前范围尚无已归集成本。</p>':`<div class="cockpit-cost-strip" aria-hidden="true">${portions.map(row=>`<span class="cockpit-series-${row.index}" data-cockpit-width="${row.percent}"></span>`).join('')}</div><div class="cockpit-breakdown">${portions.map(row=>`<div><span><i class="cockpit-series-${row.index}"></i>${esc(row.name)}</span><strong>${esc(money(row.amount_minor))}</strong><small>${row.percent.toFixed(1)}%</small></div>`).join('')}</div>`}<p class="cockpit-footnote">含采购税费、人工和已登记费用；不等同于已支付资金。</p></section>`;
}

function cockpitCustomers(rows) {
  const total = rows.reduce((sum,row)=>sum+BigInt(row.amount_minor),0n);
  const shown = rows.slice(0,5);
  if (rows.length>5) shown.push({name:`其他 ${rows.length-5} 位客户`,amount_minor:rows.slice(5).reduce((sum,row)=>sum+BigInt(row.amount_minor),0n).toString()});
  return `<section class="card cockpit-customers"><div class="section-head"><h2>未回款主要在哪些客户</h2><button type="button" class="quiet" data-cockpit-detail="outstanding">全部欠款</button></div>${total===0n?'<p class="cockpit-empty">当前范围没有应收未收余额。</p>':`<div class="cockpit-customer-list">${shown.map(row=>{const percent=Number(BigInt(row.amount_minor)*10000n/total)/100;return `<div><div class="cockpit-customer-label"><span>${esc(row.name)}</span><strong>${esc(money(row.amount_minor))}</strong><small>${percent.toFixed(1)}%</small></div><div class="cockpit-customer-track"><span data-cockpit-width="${percent}"></span></div></div>`;}).join('')}</div>`}</section>`;
}

function bindCockpitDetails(host) {
  // Assign validated numeric styles through CSSOM; HTML style attributes are blocked by CSP.
  host.querySelectorAll('[data-cockpit-bar-height]').forEach(bar=>bar.style.setProperty('--bar-height',Number(bar.dataset.cockpitBarHeight)+'%'));
  host.querySelectorAll('[data-cockpit-width]').forEach(part=>part.style.width=Number(part.dataset.cockpitWidth)+'%');
  host.querySelectorAll('[data-cockpit-detail]').forEach(button=>button.onclick=()=>safe(()=>openCockpitDetail(button.dataset.cockpitDetail,button.dataset.cockpitMonth)));
}

function renderCockpit(data) {
  const cockpit = data.cockpit;
  main.innerHTML = header('经营驾驶舱',`${cockpit.scope} · 累计经营事实`, '<a href="#finance?tab=receipts">逐笔回款记录</a><a href="#profit">项目核算</a>')+
    `<div class="cockpit-context"><span>在营项目 <strong>${cockpit.active_count}</strong></span><span>其中可核算 <strong>${cockpit.accountable_count}</strong></span><span>尚有应收 <strong>${cockpit.outstanding_count}</strong> 个项目</span></div>`+
    cockpitCards(cockpit.metrics.slice(0,4))+
    `<div class="cockpit-section-title"><h2>成本、结果与待办</h2><small>点击数字卡片，展开对应记录</small></div>`+cockpitCards(cockpit.metrics.slice(4))+
    cockpitTrend(cockpit.trend)+`<div class="cockpit-breakdowns">${cockpitComposition(cockpit.composition)}${cockpitCustomers(cockpit.customers)}</div>`+
    `<section class="card cockpit-priorities"><div class="section-head"><h2>先看看这些待收款项目</h2><small>按未回款金额排序 · 含已完工待收款项目</small></div>${cockpit.priorities.length?cockpit.priorities.map(row=>`<button type="button" class="cockpit-priority" data-cockpit-project="${row.project_id}"><span>${esc(row.project_name)}<small>${esc(row.customer_name || '客户待完善')} · ${esc(row.project_status)}</small></span><strong>${esc(money(row.receivable_minor))}</strong><span class="cockpit-open-label">查看明细 →</span></button>`).join(''):'<p class="cockpit-empty">当前工程项目没有应收未收余额。</p>'}</section>`+
    `<details class="card cockpit-projects"><summary>在营项目经营事实 <span>${cockpit.projects.length} 个项目</span></summary><div class="cockpit-project-table"></div></details>`;
  bindCockpitDetails(main);
  main.querySelector('.cockpit-context').insertAdjacentHTML('afterend','<section id="operating-attention" aria-label="今日自检与经营关注"><p class="cockpit-footnote">正在核对今日自检与经营关注…</p></section>');
  safe(()=>loadOperatingAttention());
  main.querySelectorAll('[data-cockpit-project]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.cockpitProject),'outstanding')));
  main.querySelector('.cockpit-projects').addEventListener('toggle',event=>{
    const host = event.currentTarget.querySelector('.cockpit-project-table');
    if (!event.currentTarget.open || host.childElementCount) return;
    const projects = cockpit.projects.map(row=>({...row,gross_profit_minor:row.is_accountable?row.gross_profit_minor:'待完善核算'}));
    host.innerHTML = table(projects,[['project_name','独立项目'],['settlement_minor','已确认收入'],['receipt_minor','已回款'],['invoice_minor','已开票'],['total_cost_minor','项目成本'],['gross_profit_minor','已确认毛利']],row=>`<button type="button" data-cockpit-ledger="${row.project_id}">经营明细</button><button type="button" data-cockpit-cost="${row.project_id}">成本明细</button>`,'cockpit-project-records');
    host.querySelectorAll('[data-cockpit-ledger]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.cockpitLedger))));
    host.querySelectorAll('[data-cockpit-cost]').forEach(button=>button.onclick=()=>safe(()=>openOperation('project_cost_details',{id:button.dataset.cockpitCost})));
  });
}

function cockpitDetailPage(rows, columns, keyword, page) {
  const search = keyword.toLowerCase();
  const matched = rows.filter(row=>columns.some(([key])=>String(row[key] ?? '').toLowerCase().includes(search)));
  const pages = Math.max(1,Math.ceil(matched.length/100));
  page = Math.max(0,Math.min(page,pages-1));
  return {
    rows:matched.slice(page*100,(page+1)*100),page,pages,count:matched.length,
    total:matched.reduce((sum,row)=>sum+BigInt(row.amount_minor),0n),
  };
}

async function openCockpitDetail(metric, month, keyword='', page=0) {
  const project = state.project;
  openEditor('正在读取经营明细', '<div class="loading" role="status">正在核对对应记录…</div>',null,{note:'只读明细 · 不修改账目',cancelLabel:'返回驾驶舱'});
  const request = beginEditorRequest();
  let data;
  try { data=await request.read('cockpit_detail?'+query({metric,project_id:project,month})); }
  catch(error) {
    request.check();
    openEditor('明细暂未读取',notice(error.message),null,{note:'只读明细',cancelLabel:'返回驾驶舱'});
    throw error;
  }
  const projectRows = ['outstanding','uninvoiced','profit'].includes(metric);
  const columns = projectRows ? [['project_name','项目'],['amount_minor',data.label],['settlement_minor','已确认收入'],['receipt_minor','已回款'],['invoice_minor','已开票']] :
    [['date','日期'],['project','归属项目'],['number','单据编号'],...(metric==='costs' || metric==='unassigned'?[['category','成本分类'],['counterparty','往来对象']]:[['description',metric==='receipts'?'收款方式':'说明']]),['amount_minor',metric==='receipts'?'本项目回款金额':'金额']];
  const actions = row=>row.project_id?`<button type="button" data-cockpit-row-project="${row.project_id}">项目明细</button>`:'';
  const body = `<div class="cockpit-detail-summary"><div><small>${esc(data.scope)}${month?' · '+esc(month):' · 累计'}</small><strong>${esc(money(data.total_minor))}</strong></div><span>${data.rows.length} 条明细</span></div><p class="cockpit-detail-definition">${esc(data.definition)}</p><label class="cockpit-detail-search">筛选明细<input type="search" id="cockpit-detail-search" placeholder="项目、单号或关键词"></label><p id="cockpit-detail-count" class="cockpit-footnote" role="status"></p><div id="cockpit-detail-table"></div>`;
  openEditor(data.label+'明细',body,null,{note:'只读明细 · 当前账本快照',cancelLabel:'返回驾驶舱'});
  const host = editor.querySelector('#cockpit-detail-table');
  const render = keyword=>{
    const details = cockpitDetailPage(data.rows,columns,keyword,page);
    page=details.page;
    editor.querySelector('#cockpit-detail-count').textContent=`当前匹配 ${details.count} 条 · 合计 ${money(details.total)}${keyword?'（关键词筛选后）':''}`;
    const empty = `<div class="empty"><strong>${keyword?'没有匹配的明细':'当前范围暂无这类记录'}</strong>${keyword?'可更换关键词或清空筛选。':'已有账目不会由示例记录补齐。'}</div>`;
    host.innerHTML=details.rows.length?table(details.rows,columns,actions,'cockpit-detail-records'):empty;
    if (details.pages>1) {
      host.insertAdjacentHTML('beforeend',`<div class="pagination cockpit-detail-pagination"><span>第 ${page+1} / ${details.pages} 页 · 每页最多100条</span><button type="button" id="cockpit-prev-page" ${page===0?'disabled':''}>上一页</button><button type="button" id="cockpit-next-page" ${page===details.pages-1?'disabled':''}>下一页</button></div>`);
      host.querySelector('#cockpit-prev-page').onclick=()=>{page--;render(keyword);};
      host.querySelector('#cockpit-next-page').onclick=()=>{page++;render(keyword);};
    }
    host.querySelectorAll('[data-cockpit-row-project]').forEach(button=>button.onclick=()=>safe(()=>{
      const id=Number(button.dataset.cockpitRowProject);
      const options={back:()=>openCockpitDetail(metric,month,keyword,page),backLabel:'返回驾驶舱明细'};
      if (metric==='costs') return openOperation('project_cost_details',{id},options);
      const kind={income:'settlements',invoices:'invoices',receipts:'receipts',outstanding:'outstanding'}[metric] || 'settlements';
      return openProjectLedger(id,kind,options);
    }));
  };
  const searchInput=editor.querySelector('#cockpit-detail-search');
  searchInput.value=keyword;
  render(keyword);
  searchInput.addEventListener('input',event=>{page=0;render(event.target.value.trim());});
}
