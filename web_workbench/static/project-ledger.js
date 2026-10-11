'use strict';

const projectLedgerMetrics = [
  {key:'settlement_minor', kind:'settlements', label:'已确认收入', count:'settlement_count'},
  {key:'receipt_minor', kind:'receipts', label:'已回款', count:'receipt_count'},
  {key:'invoice_minor', kind:'invoices', label:'已开票', count:'invoice_count'},
  {key:'receivable_minor', kind:'outstanding', label:'未回款'},
];
const projectLedgerTabs = {active:'在建项目', history:'历史项目', all:'全部项目'};
const activeLedgerProject = row => ['筹备中','进行中'].includes(row.status);

// The table scrolls horizontally, so its native sticky header cannot follow the page.
const ProjectLedgerHeader = (() => {
  let floating, resize, source, table, columns;
  let frame = 0;
  let measure = true;

  function schedule() {
    if (!frame) frame = requestAnimationFrame(update);
  }

  function hide() {
    floating.hidden = true;
  }

  function update() {
    frame = 0;
    if (!source?.isConnected) {
      source = null;
      resize.disconnect();
      floating.replaceChildren();
      return hide();
    }
    if (editor.open || document.body.classList.contains('nav-open')
        || main.getAttribute('aria-busy') === 'true') return hide();
    const bounds = source.getBoundingClientRect();
    const header = source.tHead.getBoundingClientRect();
    const wrapper = source.parentElement;
    const box = wrapper.getBoundingClientRect();
    const viewport = window.visualViewport;
    const top = Math.max(viewport?.offsetTop || 0,
      document.querySelector('.topbar').getBoundingClientRect().bottom);
    if (header.top > top || bounds.bottom <= top) return hide();
    const left = Math.max(viewport?.offsetLeft || 0, box.left + wrapper.clientLeft);
    const right = Math.min((viewport?.offsetLeft || 0) + (viewport?.width || window.innerWidth),
      box.left + wrapper.clientLeft + wrapper.clientWidth);
    if (right <= left) return hide();
    if (measure) {
      const widths = [...source.tHead.rows[0].cells].map(cell=>cell.getBoundingClientRect().width);
      columns.forEach((column,index)=>column.style.width=widths[index]+'px');
      table.style.width = bounds.width + 'px';
      measure = false;
    }
    floating.style.left = left + 'px';
    floating.style.top = top + 'px';
    floating.style.width = (right - left) + 'px';
    floating.style.height = Math.min(header.height, bounds.bottom - top) + 'px';
    const pushedUp = Math.min(0, bounds.bottom - top - header.height);
    table.style.transform = `translate(${bounds.left - left}px, ${pushedUp}px)`;
    floating.hidden = false;
  }

  function follow(wrapper) {
    if (!floating) {
      floating = document.createElement('div');
      floating.id = 'project-ledger-floating-header';
      floating.className = 'project-ledger-floating-header';
      floating.setAttribute('aria-hidden','true');
      floating.hidden = true;
      document.body.append(floating);
      resize = new ResizeObserver(()=>{measure=true;schedule();});
      new MutationObserver(schedule).observe(main,
        {childList:true,subtree:true,attributes:true,attributeFilter:['aria-busy']});
      new MutationObserver(schedule).observe(editor,{attributes:true,attributeFilter:['open']});
      new MutationObserver(schedule).observe(document.body,{attributes:true,attributeFilter:['class']});
      document.addEventListener('scroll',schedule,{capture:true,passive:true});
      window.addEventListener('resize',schedule,{passive:true});
      window.visualViewport?.addEventListener('resize',schedule,{passive:true});
      window.visualViewport?.addEventListener('scroll',schedule,{passive:true});
    }
    resize.disconnect();
    source = wrapper?.querySelector('table');
    floating.hidden = true;
    if (!source?.tHead) {source=null;floating.replaceChildren();return;}
    table = document.createElement('table');
    const group = document.createElement('colgroup');
    columns = [...source.tHead.rows[0].cells].map(()=>document.createElement('col'));
    group.append(...columns);
    table.append(group,source.tHead.cloneNode(true));
    floating.replaceChildren(table);
    resize.observe(source);
    resize.observe(wrapper);
    resize.observe(document.querySelector('.topbar'));
    measure = true;
    schedule();
  }

  return {follow};
})();

function projectLedgerAmount(row, metric) {
  const hint = metric.count ? `${row[metric.count]} 笔 · 查看明细` : '查看待收明细';
  const policy = metric.kind==='invoices' && row.invoice_policy==='not_required' ? '不要求开票 · 查看记录' : hint;
  return `<button type="button" class="ledger-amount ${metric.kind}" data-ledger-id="${row.id}" data-ledger-kind="${metric.kind}" aria-label="${esc(row.name+' · '+metric.label+' '+money(row[metric.key])+'，查看明细')}"><strong>${esc(money(row[metric.key]))}</strong><small>${esc(policy)}</small></button>`;
}

function projectLedgerTable(rows) {
  if (!rows.length) return '<div class="empty"><strong>当前范围没有项目</strong>试试切换在建／历史项目，或清除搜索条件。</div>';
  const body = rows.map(row => `<tr><td class="ledger-project"><button type="button" class="ledger-project-name" data-ledger-id="${row.id}" data-ledger-kind="settlements">${esc(row.name)}</button><small>${esc(row.project_code)} · ${esc(row.customer_name || '客户待补充')}</small><span class="ledger-project-state">${esc(row.status)} · ${esc(display(row.business_mode,'business_mode'))}</span></td>${projectLedgerMetrics.map(metric=>`<td class="amount">${projectLedgerAmount(row,metric)}</td>`).join('')}<td class="table-actions">${projectLedgerActions(row.id)}</td></tr>`).join('');
  return `<div class="table-wrap project-ledger-table"><table id="records"><thead><tr><th>项目 / 客户 / 状态</th>${projectLedgerMetrics.map(metric=>`<th class="amount">${metric.label}</th>`).join('')}<th class="table-actions">操作</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function projectLedgerActions(id) {
  const operations = Object.entries(state.boot.operations).filter(([,definition])=>definition.module==='projects' && (definition.read || !state.boot.read_only));
  return `${state.boot.read_only?'':`<button data-action="edit" data-id="${id}">编辑</button>`}<details class="operation-menu"><summary>项目管理</summary><div><button data-action="sites" data-id="${id}">施工地点</button><button data-action="cashflow" data-id="${id}">现金流</button>${operations.map(([name,definition])=>`<button data-operation="${name}" data-id="${id}">${esc(definition.title)}</button>`).join('')}</div></details>`;
}

async function renderProjectLedger() {
  state.collection = 'projects';
  const response = await api('rows?'+query({module:'projects',project_id:state.project,keyword:state.keyword || ''}));
  const all = response.rows;
  const selected = state.project ? 'all' : state.projectLedgerTab || 'active';
  const rows = all.filter(row=>selected==='all' || activeLedgerProject(row)===(selected==='active'));
  state.currentRows = rows;
  state.page = Math.min(state.page, Math.max(0,Math.ceil(rows.length/40)-1));
  const counts = {active:all.filter(activeLedgerProject).length, history:all.filter(row=>!activeLedgerProject(row)).length, all:all.length};
  const tabs = state.project ? '' : `<div class="tabs ledger-status-tabs" role="tablist" aria-label="项目状态">${Object.entries(projectLedgerTabs).map(([key,label])=>`<button type="button" role="tab" data-ledger-tab="${key}" aria-selected="${selected===key}" class="${selected===key?'active':''}">${label}<span>${counts[key]}</span></button>`).join('')}</div>`;
  main.innerHTML = header('项目台账','确认了多少、收了多少、开了多少，一行看清；点击金额查看逐笔记录。',
    `${state.boot.read_only?'':'<button class="primary" id="new-record">新增项目</button>'}<button id="export-csv">导出台账</button>`) + tabs +
    `<div class="filter-bar"><input id="record-search" placeholder="搜索项目、客户或编号" value="${esc(state.keyword || '')}" aria-label="搜索项目台账"><button id="search-records">查询</button><span class="grow"></span><small>${rows.length} 个项目 · 全周期累计</small></div>` +
    projectLedgerTable(rows.slice(state.page*40,state.page*40+40)) +
    `<p class="page-note">收入确认、实际回款与销项发票分别统计，不互相替代。回款按本项目归属金额计入；作废记录不计入。</p>` +
    `<div class="pagination"><span>第 ${state.page+1} 页 / ${Math.max(1,Math.ceil(rows.length/40))} 页</span><button id="prev-page" ${state.page===0?'disabled':''}>上一页</button><button id="next-page" ${(state.page+1)*40>=rows.length?'disabled':''}>下一页</button></div>`;
  main.querySelectorAll('[data-ledger-tab]').forEach(button=>button.onclick=()=>{
    state.projectLedgerTab=button.dataset.ledgerTab; state.page=0;
    safe(()=>renderRoute(false));
  });
  main.querySelectorAll('[data-ledger-id]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.ledgerId),button.dataset.ledgerKind)));
  document.querySelector('#new-record')?.addEventListener('click',()=>safe(()=>editRecord('projects')));
  document.querySelector('#export-csv').onclick=()=>downloadCSV(rows,state.boot.schema.projects.columns,'项目台账');
  document.querySelector('#search-records').onclick=()=>{
    state.keyword=document.querySelector('#record-search').value; state.page=0;
    safe(()=>renderRoute(false));
  };
  document.querySelector('#record-search').onkeydown=event=>{if(event.key==='Enter')document.querySelector('#search-records').click();};
  document.querySelector('#prev-page').onclick=()=>{state.page--;safe(()=>renderRoute(false));};
  document.querySelector('#next-page').onclick=()=>{state.page++;safe(()=>renderRoute(false));};
  bindRecordActions('projects');
  bindOperationActions(main);
  ProjectLedgerHeader.follow(main.querySelector('.project-ledger-table'));
}

const projectLedgerColumns = {
  settlements:[['settlement_date','确认日期'],['settlement_no','确认编号'],['contract_no','合同'],['basis','确认依据'],['amount_minor','确认金额'],['received_minor','已回款'],['unreceived_minor','未回款']],
  receipts:[['receipt_date','到账日期'],['receipt_no','回款编号'],['payer_name_snapshot','付款人'],['payment_method','收款方式'],['amount_minor','整笔到账'],['project_amount_minor','归属本项目'],['settlement_no','关联收入确认'],['notes','备注']],
  invoices:[['invoice_date','开票日期'],['invoice_no','发票号码'],['buyer_name_snapshot','购方'],['amount_minor','价税合计'],['received_minor','已抵扣回款'],['unreceived_minor','发票余额'],['attachment_status','附件']],
  outstanding:[['settlement_date','确认日期'],['settlement_no','确认编号'],['basis','确认依据'],['amount_minor','确认金额'],['received_minor','已回款'],['unreceived_minor','尚未收回']],
};
const projectLedgerNotes = {
  settlements:'包括手工确认、开票联动或回款补足形成的有效收入确认；施工产值与合同金额不重复计入。',
  receipts:'合计按“归属本项目”统计，同一笔到账分给多个项目时，不会把整笔金额重复计入。',
  invoices:'发票按价税合计统计；发票余额是开票后的收款抵扣余额，不代表项目未回款。',
  outstanding:'列出仍有余额的收入确认。预收款及待分配回款不会直接抹掉其他收入的应收余额。',
};

async function openProjectLedger(id, kind='settlements', options={}) {
  const request = beginEditorRequest();
  const data = await request.read('project_ledger?'+query({id}));
  showProjectLedger(data,kind,options);
}

function showProjectLedger(data, kind, options={}) {
  const project = data.project;
  const metric = projectLedgerMetrics.find(item=>item.kind===kind) || projectLedgerMetrics[0];
  kind = metric.kind;
  const detail = data.details[kind];
  const module = kind==='outstanding'?'settlements':kind;
  const cards = `<section class="ledger-detail-metrics" aria-label="项目金额">${projectLedgerMetrics.map(item=>`<button type="button" data-ledger-detail="${item.kind}" aria-pressed="${item.kind===kind}" class="${item.kind===kind?'selected':''}"><span>${item.label}</span><strong>${esc(money(data.summary[item.key]))}</strong></button>`).join('')}</section>`;
  const actions = row=>`<button type="button" data-ledger-record="${row.id}">详情</button>${state.boot.schema[module].attachment?`<button type="button" data-ledger-attachment="${row.id}">附件</button>`:''}`;
  const body = `<div class="ledger-detail-meta"><span>${esc(project.customer_name || '客户待补充')} · ${esc(project.status)} · ${esc(project.project_code)}</span><button type="button" id="ledger-close">← ${esc(options.backLabel || '返回项目台账')}</button></div>` + cards +
    `<div class="ledger-detail-title"><h3>${metric.label}明细</h3><span>${detail.rows.length} 笔 · 合计 <strong>${esc(money(detail.total_minor))}</strong></span></div>` +
    notice(projectLedgerNotes[kind]) + (detail.reconciled?'':notice('明细合计与台账汇总暂不一致，请核对收入及回款分配；本次只读查看未改动账目。','warning')) +
    `<div class="project-ledger-detail-table">${detail.rows.length?table(detail.rows,projectLedgerColumns[kind],actions,'project-ledger-details',module):`<div class="empty"><strong>${kind==='outstanding'?'当前没有待收余额':'尚无'+metric.label+'记录'}</strong>当前项目全周期内没有符合条件的有效记录。</div>`}</div>`;
  openEditor(project.name+' · 经营台账',body,null,{cancelLabel:'返回台账',note:'项目全周期 · 只读明细'});
  document.querySelector('#ledger-close').onclick=()=>safe(options.back || closeEditor);
  form.querySelectorAll('[data-ledger-detail]').forEach(button=>button.onclick=()=>showProjectLedger(data,button.dataset.ledgerDetail,options));
  form.querySelectorAll('[data-ledger-record]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(module,Number(button.dataset.ledgerRecord),{
    back:()=>openProjectLedger(project.id,kind,options),backLabel:'返回'+metric.label+'明细',
  })));
  form.querySelectorAll('[data-ledger-attachment]').forEach(button=>button.onclick=()=>safe(()=>attachments(module,Number(button.dataset.ledgerAttachment))));
}
