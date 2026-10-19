'use strict';

function governanceMaterialDetails(row) {
  const items = row.items || [];
  if (!items.length) return row.material_details || row.materials || '材料明细待补充，请查看原单';
  const lines = items.map(item => {
    const mode = item.settlement_mode || 'quantity';
    const unit = mode === 'weight' ? item.weight_unit : item.unit_snapshot;
    const quantity = mode === 'weight' ? item.net_weight : item.quantity;
    const priceKey = item.price_basis === 'inclusive' ? 'tax_inclusive_unit_price_cents' : 'material_unit_price_cents';
    const price = projectPurchasePrice({settlement_mode:mode, unit,
      unit_price_cents:item[priceKey] ?? item.unit_price_cents, unit_price_yuan:item.weight_unit_price});
    const quantityLabel = mode === 'total' ? '整批结算（数量不作推定）' :
      `${quantity ?? '数量待补充'} ${unit || ''}${mode === 'weight' ? ' · 过磅' : ''}`.trim();
    return [item.material_name_snapshot || '材料名称待补充', item.specification_snapshot,
      quantityLabel, `单价 ${price}${item.price_basis === 'inclusive' ? '（含税）' : '（未税）'}`,
      `本条含税 ${money(item.line_amount_cents)}`].filter(Boolean).join(' · ');
  });
  if (row.freight_amount_cents != null && BigInt(row.freight_amount_cents) !== 0n) {
    lines.push('整单另含运费：' + money(row.freight_amount_cents));
  }
  return lines.join('\n');
}

function governanceRecordOption(row, source) {
  const parts = source === 'unassigned_purchases' ? [row.purchase_date, row.supplier_name || '供应商待补充',
    (row.material_details || row.materials || '材料明细待补充').replaceAll('\n',' / '),
    money(row.amount_minor), row.order_no] : [row.work_date, row.worker_name,
    row.construction_site || '地点待补充', row.work_type, `${row.work_days ?? '待补充'} 工`, money(row.amount_minor)];
  return {value:row.id, label:parts.filter(Boolean).join(' · ') + `（记录${row.id}）`};
}

function governanceSourceTable(rows, module, actions) {
  const purchases = module === 'purchases';
  const columns = purchases ? [['material_details','材料 / 规格 / 数量 / 单价'],['supplier_name','供应商 / 商户'],
    ['amount_minor','整单含税金额'],['purchase_date','采购日期'],['order_no','采购单'],
    ['payment_status','付款状态'],['notes','备注']] : [['work_date','日期'],['worker_name','工人'],
    ['construction_site','作业地点'],['work_type','工作内容'],['work_days','工天'],['amount_minor','人工成本'],['notes','备注']];
  const facts = purchases ? rows.map(row=>({...row,material_details:governanceMaterialDetails(row)})) : rows;
  return table(facts,columns,actions,'governance-'+module,module);
}

function renderGovernance(data) {
  const summary = data.summary;
  main.innerHTML = header('待办与核对','先看清来源，再确认归属；不会按材料名称自动猜项目。') +
    metricCards({purchase_record_count:summary.purchase_record_count,purchase_amount_minor:summary.purchase_amount_minor,
      labor_record_count:summary.labor_record_count,labor_amount_minor:summary.labor_amount_minor}) +
    notice('待归集采购和工天是全公司范围，不受右上角项目筛选影响。已完成多项目均摊的采购不算待归集；确认归集后才更新项目成本。');
  for (const [key,module,title,operation] of [
    ['unassigned_purchases','purchases','待归集采购','governance_purchase'],
    ['unassigned_labor','worklogs','待归集工天','governance_labor'],
  ]) {
    const rows = data[key];
    const actions = row=>`<button type="button" data-governance-detail="${module}" data-id="${row.id}">${module==='purchases'?'查看原单':'查看工天'}</button>` +
      (state.boot.schema[module].attachment ? `<button type="button" data-governance-attachment="${module}" data-id="${row.id}">附件</button>` : '') +
      (!state.boot.read_only ? `<button type="button" data-governance-assign="${operation}" data-id="${row.id}">归集到项目</button>` : '');
    const count = `${rows.length} ${module==='purchases'?'笔采购单':'条工天'} · 当前均未归集`;
    const empty = `<div class="empty"><strong>当前没有${title}</strong>${module==='purchases'?'有效采购均已有项目归属或完成均摊。':'有效工天均已有项目归属。'}不代表没有历史业务。</div>`;
    main.insertAdjacentHTML('beforeend',`<section class="card governance-table" data-governance-section="${module}"><div class="section-head"><h2>${title}</h2><span>${count}</span></div>${rows.length?governanceSourceTable(rows,module,actions):empty}</section>`);
  }
  main.insertAdjacentHTML('beforeend',`<section class="card governance-table"><div class="section-head"><h2>项目完整性</h2><span>${state.project?'当前所选项目':'在建项目'} · 独立核算</span></div>${table(data.projects,
    [['name','项目'],['project_code','项目编码'],['customer_name','客户'],['contract_count','合同数'],['settlement_count','收入确认数']],
    row=>`<button type="button" data-governance-project="${row.id}">查看项目台账</button>`,'governance-projects')}</section>`);
  main.querySelectorAll('[data-governance-detail]').forEach(button=>button.onclick=()=>safe(()=>detailRecord(
    button.dataset.governanceDetail,Number(button.dataset.id),{back:closeEditor,backLabel:'返回待办与核对'})));
  main.querySelectorAll('[data-governance-attachment]').forEach(button=>button.onclick=()=>safe(()=>attachments(button.dataset.governanceAttachment,Number(button.dataset.id))));
  main.querySelectorAll('[data-governance-assign]').forEach(button=>button.onclick=()=>safe(()=>openOperation(
    button.dataset.governanceAssign,{ids:[Number(button.dataset.id)]})));
  main.querySelectorAll('[data-governance-project]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.governanceProject))));
}
