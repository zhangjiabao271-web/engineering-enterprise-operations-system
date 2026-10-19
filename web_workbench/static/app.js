'use strict';
function minorInput(value) { const cents=BigInt(value || 0); return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`; }

const state = {boot: null, route: 'home', project: '', collection: '', page: 0, selectedAnalysis: null, scope: {contract_id: null, project_id: null}};
const main = document.querySelector('#main');
const editor = document.querySelector('#editor');
const form = document.querySelector('#editor-form');
let submitEditor = null;
let cancelEditor = null;
let editorSession = 0;
let editorOpenVersion = 0;
let editorSubmitting = false;
let cashflowLoadVersion = 0;
let editorSaveLabel = '确认保存';
let editorLookups = new Map();
let toastTimer;
let loadingVersion = 0;
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const labels = {purchase:'采购',labor:'人工',manual:'费用',completed:'已完成',active:'有效',draft:'草稿',void:'已作废',confirmed:'已确认',cancelled:'已取消',planned:'待执行',pending:'待确认',inactive:'已停用',contract:'合同工程',cash:'现金工程',fixed:'固定总价',actual:'据实结算',provisional:'暂定总价',in:'收款',out:'付款',enterprise:'企业',individual:'个人',individual_business:'个体工商户',bank:'公司银行',personal:'个人经营账户',wallet:'微信/支付宝',unclassified:'未分类',manufacturer:'厂家',distributor:'经销商'};
Object.assign(labels,{through_today:'截至今天未锁定工天',future_only:'仅今后工天',custom:'自定义范围',attachment_void:'移出附件（文件保留）',attachment_restore:'恢复附件',photo_category:'修改照片分类',construction:'施工记录',invoice:'销项发票',settlement:'收入确认',receipt:'回款记录'});
const columnLabels = {total_minor:'总成本',purchase_minor:'采购成本',purchase_count:'采购笔数',labor_minor:'人工成本',labor_count:'工天记录',manual_minor:'费用成本',manual_count:'费用笔数',allocated_minor:'合同分配',invoice_applicable_minor:'适用开票收入',pending_receipt_minor:'待核对回款',advance_minor:'预收款',uninvoiced_minor:'待开票',labor_record_count:'工天记录',purchase_record_count:'采购笔数',project_count:'项目数',project_name:'项目',name:'名称',id:'编号',summary:'汇总',confirmed_gross_profit_minor:'已确认毛利',receivable_minor:'未回款',cash_balance_minor:'账面经营净现金',unassigned_cost_minor:'待归集成本',current_month_purchase_minor:'本月采购',pending_inspection_count:'待验收',labor_record_count:'待归集工天',labor_amount_minor:'待归集人工',purchase_record_count:'待归集采购',purchase_amount_minor:'待归集采购金额',missing_customer_count:'客户待完善',missing_site_count:'地点待完善',missing_contract_count:'合同待关联',missing_settlement_count:'收入待确认',settlement_minor:'已确认收入',invoice_minor:'已开票',receipt_minor:'已回款',gross_profit_minor:'已确认毛利',total_cost_minor:'总成本',contract_minor:'合同金额',labor_cost_minor:'人工成本',other_cost_minor:'其他费用',purchase_cost_minor:'采购成本',stage_label:'经营阶段',gap_text:'待完善事项',customer_name:'客户',project_code:'项目编码',status:'状态',invoice_rate_percent:'开票进度',receipt_rate_percent:'回款进度',amount_minor:'金额',cost_date:'日期',category:'成本类别',counterparty_name_snapshot:'对方',source_type:'来源',worker_name:'工人',construction_site:'作业地点',work_date:'日期',work_days:'工天',order_no:'采购单',material_name:'材料',purchase_date:'采购日期',collection_status:'回款状态'};

function money(value) {
  if (value === null || value === undefined || value === '') return '待补充';
  try {
    let cents = BigInt(value);
    const sign = cents < 0n ? '-' : '';
    if (cents < 0n) cents = -cents;
    return sign + '¥' + (cents / 100n).toLocaleString('en-US') + '.' + String(cents % 100n).padStart(2, '0');
  } catch { return String(value); }
}

function display(value, key = '', module = '') {
  if (value === null || value === undefined || value === '') return '—';
  if (key.endsWith('_minor') || key.endsWith('_cents')) return money(value);
  if (key.endsWith('_percent')) return Number(value).toFixed(1) + '%';
  if (key.endsWith('_bps')) return (Number(value)/100).toFixed(1) + '%';
  if (typeof value === 'boolean') return value?'是':'否';
  if (Array.isArray(value)) return value.map(item=>display(item,key,module)).join('、');
  if (typeof value === 'object') {
    return Object.entries(value).filter(([name])=>visibleEvidenceKey(name))
      .map(([name,item])=>`${fieldLabel(name,module)}：${display(item,name,module)}`).join('；');
  }
  if (key.endsWith('_json') && typeof value==='string') {
    try { return display(parseEvidenceJSON(value),'',module); } catch { return value; }
  }
  return valueLabel(value,key,module);
}

function toast(message) {
  const target = document.querySelector('#toast');
  target.textContent = message;
  target.hidden = false;
  WorkbenchMotion.showToast(target);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { target.hidden = true; }, 6500);
}

class SupersededView extends Error {}

function beginEditorRequest() {
  const intent = ++editorOpenVersion;
  const route = loadingVersion;
  const check = () => {
    if (intent!==editorOpenVersion || route!==loadingVersion) throw new SupersededView();
  };
  const read = async path => {
    let result;
    try {result = await api(path);} catch (error) {check();throw error;}
    try {check();} catch (error) {
      if (path.startsWith('write_ticket?')) {
        try {await api('write_discard',{token:result.token});} catch {}
      }
      throw error;
    }
    return result;
  };
  return {read,check};
}

async function api(path, data) {
  const version=loadingVersion;
  const config = {headers: state.boot ? {'X-Workbench-Token':state.boot.csrf} : {}};
  if (data !== undefined) {
    config.method = 'POST';
    config.headers = {'Content-Type':'application/json', 'X-Workbench-Token':state.boot.csrf};
    config.body = JSON.stringify(data);
  }
  const response = await fetch('/api/' + path, config);
  const result = await response.json();
  if (data===undefined && version!==loadingVersion) throw new SupersededView();
  if (!response.ok) throw new Error(result.error || '操作未完成，请核对状态');
  return data === undefined ? result : result.result;
}

function query(values) {
  return new URLSearchParams(Object.entries(values).filter(([,value]) => value !== null && value !== undefined && value !== '')).toString();
}

function header(title, note, actions = '') {
  return `<div class="page-head"><div><span class="eyebrow">工程经营 / ${esc(title)}</span><h1>${esc(title)}</h1><p>${esc(note)}</p></div><div class="head-actions">${actions}</div></div>`;
}

function notice(text, kind = '') {
  return `<div class="notice ${kind}">${esc(text)}</div>`;
}

function metricCards(summary, limit = 4) {
  if (!summary || typeof summary !== 'object') return '';
  return `<section class="metrics">${Object.entries(summary).filter(([,value]) => typeof value !== 'object').slice(0,limit).map(([key,value]) => `<article class="metric"><label>${esc(fieldLabel(key))}</label><strong>${esc(display(value,key))}</strong><small>沿用现有服务计算口径</small></article>`).join('')}</section>`;
}

function table(rows, columns, actions, id = 'records', module = '') {
  if (!rows.length) return `<div class="empty"><strong>暂无${id === 'records' ? '记录' : '待处理记录'}</strong>当前范围没有符合条件的数据，已有事实不会由演示值补齐。</div>`;
  const columnClass = key => key.endsWith('_minor') || key.endsWith('_cents') ? 'amount' : '';
  const headings = columns.map(([key, label]) =>
    `<th data-column="${esc(key)}" class="${columnClass(key)}">${esc(label)}</th>`).join('');
  const body = rows.map(row => {
    const cells = columns.map(([key]) => {
      const text = esc(display(row[key], key, module));
      return `<td data-column="${esc(key)}" title="${text}" class="${columnClass(key)}">${text}</td>`;
    }).join('');
    return `<tr>${cells}${actions ? `<td class="table-actions">${actions(row)}</td>` : ''}</tr>`;
  }).join('');
  return `<div class="table-wrap"><table id="${id}"><thead><tr>${headings}${actions ? '<th class="table-actions">操作</th>' : ''}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function optionList(items, selected = '') {
  return '<option value="">请选择 / 可留空</option>' + items.map(item => `<option value="${esc(item.value)}" ${String(item.value) === String(selected) ? 'selected' : ''}>${esc(item.label)}</option>`).join('');
}

function businessToday() {
  return new Date().toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'});
}

function fieldHTML(field, value, options = state.boot.options, prefix = '') {
  value = value ?? (field.default_today ? businessToday() : field.default) ?? '';
  const name = prefix + field.name;
  const required = field.required ? 'required' : '';
  const label = `${esc(field.label)}${field.required ? '<span class="required">*</span>' : ''}`;
  if (field.kind === 'checkbox') return `<label class="form-field check"><input type="checkbox" name="${esc(name)}" ${value ? 'checked' : ''}>${label}</label>`;
  let input;
  if (field.kind === 'select') {
    const items = field.source ? [...(options[field.source] || [])] : Object.entries(Array.isArray(field.choices) ? Object.fromEntries(field.choices.map(item => [item,item])) : field.choices || {}).map(([val,text]) => ({value:val,label:text}));
    if (value !== '' && (!field.source || field.preserve_current) && !items.some(item=>String(item.value)===String(value))) {
      items.push({value,label:field.source ? `原记录 · 编号${value}（当前不在可选名单）` : `${value}（原记录）`});
    }
    const searchable = ['projects','active_projects','offers','suppliers','customers','workers','active_workers','contracts'].includes(field.source);
    input = `<select name="${esc(name)}" ${searchable ? 'data-search' : ''} ${required}>${optionList(items,value)}</select>`;
  } else if (field.kind === 'textarea') {
    input = `<textarea name="${esc(name)}" autocomplete="off" ${required}>${esc(value)}</textarea>`;
  } else if (field.name === 'work_days') {
    const days = ['0.5','1'];
    if (value !== '' && !days.includes(String(value))) days.push(String(value));
    input = `<span class="workday-picker" role="group" aria-label="选择工天"><input type="hidden" name="${esc(name)}" value="${esc(value)}">${days.map(day=>`<button type="button" data-workday="${esc(day)}" aria-pressed="${String(value)===day}">${esc(day)} 工${days.indexOf(day)>1?'（原记录）':''}</button>`).join('')}</span>`;
  } else {
    const type = field.kind === 'date' ? 'date' : ['money','number','integer'].includes(field.kind) ? 'number' : 'text';
    const step = field.kind === 'integer' ? '1' : 'any';
    input = `<input name="${esc(name)}" type="${type}" autocomplete="off" step="${step}" value="${esc(value)}" ${required}>`;
  }
  return `<label class="form-field ${field.kind === 'textarea' ? 'wide' : ''}"><span class="field-label">${label}</span>${input}</label>`;
}

function openEditor(title, body, submit, options = {}) {
  editorOpenVersion++;
  editorSession++;
  editorLookups = new Map();
  document.querySelector('#dialog-title').textContent = title;
  document.querySelector('#editor-body').innerHTML = body;
  document.querySelector('#form-error').hidden = true;
  editorSaveLabel = options.label || '确认保存';
  document.querySelector('#editor-save').hidden = !submit;
  document.querySelector('#editor-cancel').textContent = options.cancelLabel || '取消';
  editor.querySelector('.dialog-head .eyebrow').textContent = options.note || '核对后保存 · 沿用现有业务规则';
  submitEditor = submit;
  cancelEditor = options.cancel || null;
  enhanceForms(document.querySelector('#editor-body'));
  setEditorSubmitting(editorSubmitting);
  if (!editor.open) editor.showModal();
  WorkbenchMotion.openDialog(editor);
}

async function closeEditor() {
  if (editorSubmitting) {
    toast('正在保存或核对，请等待处理完成后再关闭。');
    return;
  }
  editorOpenVersion++;
  editorSession++;
  editorLookups = new Map();
  const cancelled = cancelEditor;
  cancelEditor = null;
  submitEditor = null;
  await WorkbenchMotion.closeDialog(editor);
  if (cancelled) { try { await cancelled(); } catch (error) { toast(error.message); } }
}

function setEditorSubmitting(submitting) {
  editorSubmitting = submitting;
  form.setAttribute('aria-busy',String(submitting));
  document.querySelector('#editor-body').inert = submitting;
  for (const id of ['editor-save','editor-close','editor-cancel']) document.querySelector('#'+id).disabled = submitting;
  document.querySelector('#editor-save').textContent = submitting ? '正在处理…' : editorSaveLabel;
}

// Each lookup belongs to one dialog and only its latest selection may update it.
function createEditorLookup() {
  const session = editorSession;
  const statuses = editorLookups;
  const key = {};
  let generation = 0;
  const run = async (load, apply) => {
    const current = ++generation;
    const active = () => current === generation && session === editorSession && editor.open;
    statuses.set(key,'loading');
    try {
      const result = await load();
      if (!active()) return;
      apply(result);
      statuses.delete(key);
    } catch (error) {
      if (!active()) return;
      statuses.set(key,'error');
      if (!(error instanceof SupersededView)) toast(error.message);
    }
  };
  const cancel = () => {
    generation++;
    statuses.delete(key);
  };
  return {run,cancel};
}

function bindEditorChoices(triggers, choices, load) {
  const lookup = createEditorLookup();
  const update = () => {
    for (const [select] of choices) {
      select.disabled = true;
      select.innerHTML = '<option value="">正在读取关联选项…</option>';
    }
    return lookup.run(load,options => {
      for (const [select,source] of choices) {
        select.innerHTML = optionList(options[source] || []);
        select.disabled = false;
      }
    });
  };
  if (choices.length) for (const trigger of triggers.filter(Boolean)) trigger.addEventListener('change',update);
  return update;
}

function formValues(fields) {
  const values = {};
  for (const field of fields) {
    const element = form.elements.namedItem(field.name);
    values[field.name] = field.kind === 'checkbox' ? element.checked : element.value;
  }
  return values;
}

function purchaseItem(item = {}, options = state.boot.options) {
  const price = item.unit_price ?? (item.price_basis === 'inclusive' ? minorInput(item.tax_inclusive_unit_price_cents) : minorInput(item.material_unit_price_cents));
  const values = {...item, product_id:item.supplier_offer_id || item.product_id || '', unit_price:price,
    tax_percent:item.tax_percent ?? Number(item.tax_rate_bps || 0)/100, settlement_total:item.settlement_total ?? minorInput(item.settlement_total_cents)};
  const fields = [
    {name:'product_id',label:'已有材料报价',kind:'select',source:'offers'},
    {name:'material_name_snapshot',label:'材料名称',required:true}, {name:'specification_snapshot',label:'规格'},
    {name:'quantity',label:'数量 / 净重',kind:'number',required:true,default:'1'}, {name:'unit_snapshot',label:'单位',required:true},
    {name:'unit_price',label:'单价（元）',kind:'money',required:true,default:'0'},
    {name:'price_basis',label:'计价口径',kind:'select',choices:{exclusive:'未税',inclusive:'含税'},default:'inclusive'},
    {name:'tax_percent',label:'税率（%）',kind:'number',default:'0'},
    {name:'cost_category',label:'成本类别',kind:'select',choices:['材料费','工具和设备','其他'],default:'材料费'},
    {name:'settlement_mode',label:'结算方式',kind:'select',choices:{quantity:'数量',weight:'过磅重量',total:'整批总额'},default:'quantity'},
    {name:'net_weight',label:'过磅净重',kind:'number'}, {name:'weight_unit',label:'过磅单位',kind:'select',choices:['吨','公斤']},
    {name:'weight_unit_price',label:'重量单价（元）',kind:'money'}, {name:'weigh_ticket_no',label:'磅单编号'},
    {name:'settlement_total',label:'整批结算总额（元）',kind:'money'}, {name:'purpose',label:'用途'}];
  const section = document.createElement('section');
  section.className = 'purchase-item';
  section.dataset.itemId = item.item_id || item.id || '';
  section.innerHTML = `<h3>材料明细<button type="button" class="quiet remove-item">移除此行</button></h3><div class="form-grid">${fields.map(field => fieldHTML({...field,preserve_current:Boolean(item.id || item.item_id)},values[field.name],options,'item-')).join('')}</div><output class="item-preview" aria-live="polite">填写后显示本条材料金额</output>`;
  const mode = section.querySelector('[name="item-settlement_mode"]');
  const updateMode = () => {
    for (const name of ['quantity','unit_snapshot','unit_price','net_weight','weight_unit','weight_unit_price','weigh_ticket_no','settlement_total']) {
      const input = section.querySelector(`[name="item-${name}"]`);
      const visible = mode.value === 'quantity' ? ['quantity','unit_snapshot','unit_price'].includes(name) : mode.value === 'weight' ? ['net_weight','weight_unit','weight_unit_price','weigh_ticket_no'].includes(name) : name === 'settlement_total';
      input.closest('.form-field').hidden = !visible;
      input.required = visible && name !== 'weigh_ticket_no';
    }
  };
  mode.onchange = updateMode;
  updateMode();
  enhanceForms(section);
  const quoteLookup = createEditorLookup();
  const quote = section.querySelector('[name="item-product_id"]');
  const quoteHelp = document.createElement('small');
  quoteHelp.className = 'form-help';
  quote.closest('.form-field').append(quoteHelp);
  const supplierId = () => form.elements.namedItem('supplier_id')?.value || '';
  const originalQuoteLabel = quote.selectedOptions?.[0]?.textContent || '原记录报价（仅保留历史关联）';
  const updateOffers = (preserveHistorical = false) => {
    quoteLookup.cancel();
    const selected = quote.value;
    const owner = supplierId();
    const candidates = (options.offers || []).filter(offer => owner && String(offer.supplier_id) === owner);
    const knownOffer = (options.offers || []).find(offer => String(offer.value) === selected);
    const keepHistorical = preserveHistorical && Boolean(item.item_id || item.id) && selected && !knownOffer && owner;
    if (keepHistorical) candidates.push({value:selected,label:originalQuoteLabel});
    const retained = candidates.some(offer => String(offer.value) === selected);
    quote.innerHTML = optionList(candidates, retained ? selected : '');
    quote.value = retained ? selected : '';
    quote.disabled = !owner || !candidates.length;
    if (!owner) quoteHelp.textContent = '请先选择上方供应商；零星采购也可以直接填写材料。';
    else if (selected && !retained) quoteHelp.textContent = '原报价关联已清除，材料和成交价保留；请按当前供应商核对。';
    else if (!candidates.length) quoteHelp.textContent = '该供应商暂无材料报价，可直接填写材料、数量和单价。';
    else quoteHelp.textContent = '仅显示当前供应商的材料报价；也可直接填写材料。';
    quote.dispatchEvent(new Event('choice-sync'));
  };
  section.updateSupplierOffers = () => updateOffers();
  updateOffers(true);
  section.querySelector('.remove-item').onclick = () => {quoteLookup.cancel();section.remove();};
  quote.onchange = event => {
    const offerId = event.target.value;
    return quoteLookup.run(() => offerId ? api('record?' + query({module:'offers',id:offerId})) : null,offer => {
      if (!section.isConnected || !offer || quote.value !== offerId) return;
      if (String(offer.supplier_id) !== supplierId()) throw new Error('报价不属于当前供应商，请重新选择材料。');
      const assignments = {material_name_snapshot:offer.name,specification_snapshot:offer.specification,unit_snapshot:offer.unit,
        unit_price:offer.price,tax_percent:offer.tax_rate_percent,price_basis:offer.price_basis};
      for (const [key,value] of Object.entries(assignments)) section.querySelector(`[name="item-${key}"]`).value = value ?? '';
      quoteHelp.textContent = '已选择当前供应商报价；保存时记录本次成交快照。';
      section.dispatchEvent(new Event('change', {bubbles:true}));
    });
  };
  return section;
}

async function editRecord(module, id, defaults={}) {
  const request = beginEditorRequest();
  const spec = state.boot.schema[module];
  const requestKey=crypto.randomUUID();
  const record = id ? await request.read('record?' + query({module,id})) : {...defaults};
  const options = await request.read('options?' + query({project_id:module==='allocations'?null:record.project_id || state.project}));
  // Support an already-running backend while its page scripts have been refreshed.
  if (module === 'purchases' && options.offers.some(offer => offer.supplier_id === undefined)) {
    const catalog = await request.read('rows?module=offers');
    const suppliers = new Map(catalog.rows.map(offer => [String(offer.id),offer.supplier_id]));
    options.offers = options.offers.map(offer => ({...offer,supplier_id:suppliers.get(String(offer.value))}));
  }
  const ticket=await request.read('write_ticket?'+query({kind:'save',module,id,expected:record._version}));
  let fields = spec.fields;
  if (module==='receipts' && id && record._receipt_income_owned) fields=[...fields,...receiptIncomeFields()];
  if (id && module === 'costs') fields = fields.map(field => {
    if (field.name !== 'category' || field.choices.includes(record.category)) return field;
    return {...field, choices:Object.fromEntries([...field.choices.map(value => [value,value]),
      [record.category,record.category + '（原记录分类）']])};
  });
  if (!id && ['costs','worklogs'].includes(module)) fields = fields.map(field=>field.name==='project_id'?{...field,source:'active_projects'}:field);
  if (id && module==='worklogs') {
    const original = options.workers.find(worker=>String(worker.value)===String(record.worker_id));
    if (original && !options.active_workers.some(worker=>String(worker.value)===String(original.value))) {
      options.active_workers.push({...original,label:original.label+'（已离职 · 原记录）'});
    }
  }
  if (module === 'contracts') fields = fields.map(field => field.name === 'amount' ? {...field,required:false} : field);
  if (id) fields = fields.map(field=>({...field,preserve_current:true}));
  const body = (module==='receipts'?notice('经营主体在此确认，不必另去其他页面维护。收入、发票抵扣和真实资金仍分别核算；已关联到账流水的回款，不能单独改金额、日期或收款方式。'):'') +
    `<div class="form-grid">${fields.map(field => fieldHTML(field,record[field.name] ?? (field.name === 'project_id' ? state.project : undefined),options)).join('')}</div>` +
    (module === 'purchases' ? purchaseAllocationHTML(record,options) + '<div id="purchase-items"></div><button type="button" id="add-item">添加材料明细</button><output id="purchase-preview" class="purchase-preview" aria-live="polite"></output>' : '') +
    (!id && ['purchases','worklogs'].includes(module) ? '<label class="confirm-check"><input name="continue-entry" type="checkbox">保存后继续录入（保留项目、日期等共同条件，金额和材料重新填写）</label>' : '') +
    (module==='receipts' && id && record._receipt_income_owned ? '<section id="receipt-income-impact" aria-live="polite"></section>' : '') +
    '<label class="confirm-check"><input name="confirmed" type="checkbox" required>我已核对日期、金额、项目归属及业务依据，确认保存本笔记录。</label>';
  openEditor((id ? '编辑' : '新增') + spec.title,body,async () => {
    const values = formValues(fields);
    if (module==='receipts' && id && record._receipt_income_owned) {
      const impact=await form._receiptIncomeRefresh();
      if (impact.requires_decision && (!values.receipt_income_action || !values.receipt_income_reason.trim())) {
        throw new Error('请根据影响预览明确选择收入处理方式，并填写更正说明。');
      }
    }
    if (module==='transactions') values.request_key=requestKey;
    if (module === 'contracts' && values.pricing_mode === 'actual') values.amount = '0';
    if (module === 'purchases') {
      values.items = collectPurchaseItems();
      Object.assign(values,collectPurchaseAllocation());
    }
    const continueEntry = form.elements.namedItem('continue-entry')?.checked;
    await api('save',{module,id,expected:record._version,record:values,form_ticket:ticket.token,confirmed:form.elements.confirmed.checked});
    cancelEditor = null;
    editor.close();
    toast('记录已保存，使用现有业务服务重新核对台账。');
    await refreshOptions();
    await renderRoute(false);
    if (continueEntry) {
      if (module === 'purchases') {
        const keep = ['allocation_method','project_ids','project_id','supplier_id','merchant_name_snapshot','purchase_date','purchase_type','purchaser','payment_method','payment_status','invoice_status'];
        await editRecord(module,null,Object.fromEntries(keep.map(name => [name,values[name]])));
        form.elements.namedItem('continue-entry').checked=true;
        return;
      }
      await editRecord(module);
      const keep = ['project_id','construction_site','work_date','work_type'];
      for (const name of keep) {
        const input = form.elements.namedItem(name);
        if (input) {input.value=values[name]; input.dispatchEvent(new Event('choice-sync'));}
      }
      form.elements.project_id.dispatchEvent(new Event('change'));
      form.elements.namedItem('continue-entry').checked=true;
    }
  },{cancel:()=>api('write_discard',{token:ticket.token})});
  if (module==='receipts' && id && record._receipt_income_owned) bindReceiptIncomePreview(record);
  if (module==='offers') bindOfferSupplierTax(record);
  if (module === 'purchases') {
    const host = document.querySelector('#purchase-items');
    (record.items?.length ? record.items : [{}]).forEach(item => host.append(purchaseItem(item,options)));
    document.querySelector('#add-item').onclick = () => host.append(purchaseItem({},options));
    const supplier = form.elements.namedItem('supplier_id');
    const updateOffers = () => host.querySelectorAll('.purchase-item').forEach(section => section.updateSupplierOffers());
    supplier.addEventListener('change',updateOffers);
    supplier.addEventListener('choice-sync',updateOffers);
    supplier.closest('.search-choice').querySelector('input').addEventListener('input',updateOffers);
    bindPurchaseAllocation();
    initializePurchasePreview();
  }
  const project = form.elements.namedItem('project_id');
  if(module==='costs' && id){project.disabled=true;project.closest('label').insertAdjacentHTML('beforeend','<small>项目分摊请使用“成本归集”操作核对后调整。</small>');}
  const linkedChoices = [['contract_id','contracts'],['settlement_id','settlements'],['invoice_id','invoices'],['project_site_id','sites']]
    .filter(([name]) => !(module==='allocations' && name==='contract_id'))
    .map(([name,source]) => [form.elements.namedItem(name),source]).filter(([select]) => select);
  if(['settlements','invoices','receipts'].includes(module))bindFinancialChoices(options,record);
  else if(module==='worklogs')bindWorkdaySite();
  else if(project)bindEditorChoices([project],linkedChoices,() => api('options?' + query({project_id:project.value})));
  if(module==='contracts'){
    bindContractPricing(Boolean(id));
    const target=form.elements.namedItem('project_id');
    if(id){
      target.disabled=true;target.dispatchEvent(new Event('choice-sync'));
      target.closest('label').insertAdjacentHTML('beforeend','<small>已有项目关系通过项目关联／分配维护。</small>');
    }else target.closest('label').insertAdjacentHTML('beforeend','<small>选一个项目，保存时自动全额关联；据实合同建立据实关系。涉及多个项目时可留空，之后分别分配。</small>');
  }
  if(module==='settlements'){
    form.elements.contract_id.closest('label').insertAdjacentHTML('beforeend','<small>已认可价款可先登记，合同签好后使用“补关联合同”；不会重复增加收入。</small>');
    form.elements.business_date.closest('label').insertAdjacentHTML('beforeend','<small>留空时：已签合同按签约日期，合同待补业务按确认日期排序。确认日期仍用于收入统计。</small>');
  }
  if(module==='receipts' && id){
    const scope=form.elements.namedItem('allocation_scope');
    if(scope){scope.disabled=true;scope.dispatchEvent(new Event('choice-sync'));}
    for(const name of ['project_id','contract_id']){
      const choice=form.elements.namedItem(name);
      choice.disabled=true;
      choice.dispatchEvent(new Event('choice-sync'));
      choice.closest('label').insertAdjacentHTML('beforeend','<small>原回款归属，编辑时不可更换；对应收入可通过“重新分配”调整。</small>');
    }
  }
  if (module === 'receipts') {
    bindReceiptAllocationPreview(record);
    const payer = form.elements.payer_name;
    const contract = form.elements.contract_id;
    let automatic = '';
    const payerLookup = createEditorLookup();
    const fillPayer = () => {
      const contractId = contract.value;
      const projectId = project.value;
      return payerLookup.run(() => contractId ? api('record?' + query({module:'contracts',id:contractId})) : projectId ? api('record?' + query({module:'projects',id:projectId})) : {},linked => {
        const customer = linked.customer_name_snapshot || linked.customer_name || '';
        if (!payer.value || payer.value === automatic) {payer.value=customer;automatic=customer;}
      });
    };
    contract.addEventListener('change',fillPayer);
    project.addEventListener('change',fillPayer);
    if (!id) await fillPayer();
  }
}

async function detailRecord(module, id, options = {}) {
  if (module === 'projects') return openProjectLedger(id);
  const request = beginEditorRequest();
  const record = await request.read('record?' + query({module,id}));
  const spec = state.boot.schema[module];
  const fields = Object.entries(record).filter(([key]) => !key.startsWith('_') && !['public_id','organization_id'].includes(key));
  const back = options.back ? `<button type="button" id="record-detail-back">← ${esc(options.backLabel || '返回明细')}</button>` : '';
  openEditor(spec.title + ' · 原始记录', back + `<div class="detail-grid">${fields.map(([key,value]) => `<div class="detail-cell"><small>${esc(fieldLabel(key,module))}</small><p>${esc(display(value,key,module))}</p></div>`).join('')}</div>`,null,
    {cancelLabel: options.back ? '返回台账' : '关闭', note: '原始业务记录 · 只读查看'});
  document.querySelector('#record-detail-back')?.addEventListener('click', () => safe(options.back));
}

function downloadCSV(rows, columns, name) {
  const cell = value => '"' + String(value ?? '').replace(/"/g,'""') + '"';
  const csv = '\ufeff' + [columns.map(([,label]) => cell(label)).join(','),...rows.map(row => columns.map(([key]) => cell(display(row[key],key))).join(','))].join('\r\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  link.download = name + '.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href),1000);
}

const groups = {contracts:['contracts','allocations','settlements'], finance:['invoices','receipts','collection','historical','input_invoices'],
  projects:['projects','profit'], offers:['offers','compare'],
  funds:['accounts','plans','transactions','cashflow'], cost:['cost_ledger','costs'],labor:['worklogs','workers','monthly']};

async function renderCollection(module, parent) {
  if (module === 'projects') return renderProjectLedger();
  if (module === 'contracts') return renderContractLedger();
  state.collection = module;
  const spec = state.boot.schema[module];
  const scoped = spec.scope || module === 'worklogs';
  const context=workbenchRecordScope(module);
  const response = await api('rows?' + query({module,project_id:scoped ? state.project : '',keyword:state.keyword || '',...context.params}));
  const rows = response.rows;
  state.currentRows = rows;
  state.page = Math.max(0,Math.min(state.page,Math.max(0,Math.ceil(rows.length/40)-1)));
  const title = parent ? ({contracts:'合同与结算',finance:'开票与回款',funds:'资金与计划',cost:'成本台账',labor:'人工与工天'}[parent] || spec.title) : spec.title;
  main.innerHTML = header(title, scoped ? '按项目范围查看，金额与状态取自现有业务事实。' : '全公司基础档案；项目筛选不改变本页档案范围。',
    `${module==='worklogs' && !state.boot.read_only ? '<button class="primary" id="batch-worklogs">批量登记工天</button>' : ''}${spec.can_create && !state.boot.read_only ? `<button class="${module==='worklogs'?'':'primary'}" id="new-record">${module==='worklogs'?'单人登记':'新增'+esc(spec.title)}</button>` : ''}<button id="export-csv">导出台账</button>`) +
    (groups[parent] ? tabsHTML(parent,module) : '') + context.html + (spec.limitation ? notice(spec.limitation,'warning') : '') +
    `<div class="filter-bar"><input id="record-search" placeholder="搜索名称、编号或备注" value="${esc(state.keyword || '')}" aria-label="搜索台账"><button id="search-records">查询</button><span class="grow"></span><small>${rows.length} 条记录 · ${scoped && state.project ? '当前项目' : '全公司口径'}</small></div>` +
    table(rows.slice(state.page*40,state.page*40+40),spec.columns,row => recordActions(module,row),'records',module) +
    `<div class="pagination"><span>第 ${state.page+1} 页 / ${Math.max(1,Math.ceil(rows.length/40))} 页</span><button id="prev-page" ${state.page === 0 ? 'disabled' : ''}>上一页</button><button id="next-page" ${(state.page+1)*40 >= rows.length ? 'disabled' : ''}>下一页</button></div>`;
  document.querySelector('#new-record')?.addEventListener('click',() => safe(() => editRecord(module)));
  document.querySelector('#batch-worklogs')?.addEventListener('click',() => safe(() => openWorkdayBatch()));
  document.querySelector('#export-csv').onclick = () => downloadCSV(rows,spec.columns,spec.title);
  document.querySelector('#search-records').onclick = () => { state.keyword = document.querySelector('#record-search').value; state.page=0; safe(() => renderRoute(false)); };
  document.querySelector('#record-search').onkeydown = event => {if (event.key === 'Enter') document.querySelector('#search-records').click();};
  document.querySelector('#prev-page').onclick = () => {state.page--; safe(() => renderRoute(false));};
  document.querySelector('#next-page').onclick = () => {state.page++; safe(() => renderRoute(false));};
  bindTabs(parent);
  bindRecordActions(module);
  bindOperationActions(main);
}

function tabsHTML(parent, selected) {
  const titles = {projects:'经营台账',profit:'核算分析',offers:'材料报价',compare:'报价对比',cashflow:'资料分析',collection:'回款跟进',cost_ledger:'完整成本来源',costs:'其他费用记录',monthly:'月度工天矩阵'};
  return `<div class="tabs" role="tablist" aria-label="业务视图">${groups[parent].map(key => `<button data-tab="${key}" class="${key === selected ? 'active' : ''}" role="tab" aria-selected="${key === selected}">${esc(titles[key] || state.boot.schema[key]?.title)}</button>`).join('')}</div>`;
}

function bindTabs(parent) {
  main.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => {
    const params=new URLSearchParams(location.hash.split('?')[1] || '');
    params.set('tab',button.dataset.tab);
    location.hash = parent+'?'+params.toString();
  });
}

function recordActions(module,row) {
  const spec = state.boot.schema[module];
  const id = row.id;
  if (!id) return '';
  let html = `<button data-action="detail" data-id="${id}">详情</button>`;
  if (spec.can_update && !state.boot.read_only) html += `<button data-action="edit" data-id="${id}">编辑</button>`;
  if (spec.attachment) html += `<button data-action="attachments" data-id="${id}">附件</button>`;
  if (module === 'contracts' || module === 'projects') html += `<button data-action="cashflow" data-id="${id}">现金流</button>`;
  if(module==='construction') html+=`<button data-action="photos" data-id="${id}">照片分类</button>`;
  if (module === 'construction' && !state.boot.read_only) html += `<button data-action="inspection" data-id="${id}">验收</button>`;
  if (module === 'plans' && !state.boot.read_only && row.status === 'planned') html += `<button data-action="plan_cancel" data-id="${id}">取消计划</button>`;
  if (module === 'projects') html += `<button data-action="sites" data-id="${id}">施工地点</button>`;
  if (module==='input_invoices' && !state.boot.read_only) html+=`<button data-action="input_pdf" data-id="${id}">票据 PDF</button>`;
  html += operationActions(module,id);
  return html;
}

function bindRecordActions(module) {
  main.querySelectorAll('[data-action]').forEach(button => button.onclick = () => safe(async () => {
    const id = Number(button.dataset.id);
    switch (button.dataset.action) {
      case 'detail': return detailRecord(module,id);
      case 'edit': return editRecord(module,id);
      case 'attachments': return attachments(module,id);
      case 'cashflow':
        state.scope = {contract_id:module === 'contracts' ? id : null,project_id:module === 'projects' ? id : state.project ? Number(state.project) : null};
        state.selectedAnalysis = null; location.hash = 'cashflow'; return;
      case 'inspection': return inspection(id);
      case 'plan_cancel': return actionForm('取消收付计划','plan_cancel',id,[{name:'reason',label:'取消依据',kind:'textarea',required:true}]);
      case 'sites': return sites(id);
      case 'input_pdf':return inputPDF(id);
      case 'photos':return constructionPhotos(id);
    }
  }));
}

async function renderGroup(parent,module) {
  state.collection=module;
  if (module==='profit') return renderDashboard('profit');
  if (module==='compare') return renderCompare();
  if (module==='cashflow') return renderCashflow();
  if(module==='monthly') return renderMonthlyView();
  if (module === 'cost_ledger') {
    return renderCostLedger(parent,module);
  }
  if (module === 'collection') {
    const data = await api('dashboard?' + query({view:'collection',project_id:state.project}));
    const columns = [['project_name','项目'],['receivable_minor','未回款'],['due_date','约定时间'],['owner_name','跟进人'],['next_action','下一步'],['next_followup_date','下次跟进']];
    main.innerHTML = header('开票与回款','跟进记录与实际到账独立保存。') + tabsHTML(parent,module) + table(data.rows,columns,row => (!state.boot.read_only ? `<button data-followup="${row.project_id}">更新跟进</button>` : '')+operationActions('collection',row.project_id));
    bindTabs(parent);
    bindOperationActions(main);
    main.querySelectorAll('[data-followup]').forEach(button=>button.onclick=()=>safe(()=>openOperation('collection_followup',{id:button.dataset.followup})));
    return;
  }
  await renderCollection(module,parent);
}

async function renderDashboard(view) {
  const context=view==='profit'?workbenchRecordScope('projects'):{params:{},html:''};
  const data = await api('dashboard?' + query({view,project_id:state.project,...context.params}));
  if (view === 'home') return renderCockpit(data);
  const titles = {profit:'项目台账',governance:'待办与核对'};
  let summary = data.summary;
  if (view === 'profit') {
    const totals = data.accounting_summary;
    summary = {settlement_minor:totals.settlement_minor,total_cost_minor:totals.total_cost_minor,
      gross_profit_minor:totals.gross_profit_minor,receivable_minor:totals.receivable_minor};
  }
  main.innerHTML = header(titles[view],'同一项目、同一数据来源；收入、开票、到账和成本保持各自口径。') +
    (view==='profit'?tabsHTML('projects','profit'):'') + context.html + metricCards(summary);
  if (view==='profit') {
    const totals=data.accounting_summary;
    main.insertAdjacentHTML('beforeend',notice(`账面毛利＝全部已确认收入－全部成本，包含待完善项目。已可核算 ${totals.accountable_count} 个项目，已确认毛利 ${money(totals.confirmed_gross_profit_minor)}；待完善 ${totals.pending_count} 个项目，账面差额 ${money(totals.pending_gross_profit_minor)}。成本不会因资料不全被排除。`));
    bindTabs('projects');
    main.insertAdjacentHTML('beforeend',`<section class="card"><div class="section-head"><h2>项目经营事实</h2><small>独立核算 · 点击金额查看原始明细</small></div>${projectAccountingTable(data.projects || [])}</section>`);
    main.querySelectorAll('[data-ledger-id]').forEach(button=>button.onclick=()=>safe(()=>openProjectLedger(Number(button.dataset.ledgerId),button.dataset.ledgerKind,{backLabel:'返回核算分析'})));
    main.insertAdjacentHTML('beforeend',notice('账面经营净现金沿用项目经营收支口径，不代表真实银行余额；实际账户余额请到资金与计划查看。'));
  }
  if (view === 'governance') {
    renderGovernance(data);
  }
}

async function actionForm(title, action, id, fields, transform = value => value) {
  const request = beginEditorRequest();
  const options=await request.read('options?'+query({project_id:state.project}));
  const ticket=await request.read('write_ticket?'+query({kind:'action',action,id}));
  openEditor(title,`<div class="form-grid">${fields.map(field => fieldHTML(field,undefined,options)).join('')}</div><label class="confirm-check"><input name="confirmed" type="checkbox" required>我已核对并确认此业务操作。</label>`,async () => {
    await api('action',{action,record:transform({id,...formValues(fields)}),form_ticket:ticket.token,confirmed:form.elements.confirmed.checked});
    cancelEditor=null;editor.close();toast('操作已保存。');await renderRoute(false);
  },{cancel:()=>api('write_discard',{token:ticket.token})});
}

function inspection(id) {
  return actionForm('更新施工验收','inspection',id,[{name:'inspection_status',label:'验收结论',kind:'select',choices:['待验收','已验收','需整改'],required:true},
    {name:'inspector',label:'验收人',required:true},{name:'inspection_date',label:'验收日期',kind:'date',required:true,default:new Date().toLocaleDateString('sv-SE')},
    {name:'inspection_notes',label:'验收依据 / 整改事项',kind:'textarea',required:true}]);
}

async function sites(id) {
  const request = beginEditorRequest();
  const rows = await request.read('sites?' + query({project_id:id}));
  openEditor('项目施工地点',table(rows,[['site_code','地点编号'],['name','地点'],['address','地址']]) + (!state.boot.read_only ? '<button id="add-site" type="button">新增施工地点</button>' : ''),null);
  document.querySelector('#add-site')?.addEventListener('click',() => actionForm('新增施工地点','project_site',id,[{name:'site_name',label:'地点名称',required:true},{name:'address',label:'地址'}],record => ({...record,project_id:id})));
}

async function fileData(file) {
  if (!file || file.size>20*1024*1024) throw new Error('请选择不超过20MB的本地文件');
  return new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name:file.name,content:reader.result.split(',')[1]});
    reader.onerror = () => reject(new Error('本地文件读取未完成'));
    reader.readAsDataURL(file);
  });
}

async function renderTools() {
  main.innerHTML = header('数据导入导出','台账导出与完整备份分开；恢复必须先核对覆盖范围。') +
    `<div class="tool-grid"><section class="card"><h2>导出台账</h2><p class="page-note">导出CSV带中文表头与金额口径，不修改原记录。</p><label class="form-field">选择台账<select id="export-module">${Object.entries(state.boot.schema).map(([key,spec]) => `<option value="${key}">${esc(spec.title)}</option>`).join('')}</select></label><button id="tools-export" class="primary">导出本地 CSV</button></section>
    <section class="card"><h2>基础资料导入</h2><p class="page-note">CSV 整批预览与明确确认；任一行失败整批不写入，不截断资料。合同和资金资料走各自业务入口。</p><label class="form-field">资料类型<select id="import-module">${['projects','suppliers','customers','offers'].map(key => `<option value="${key}">${state.boot.schema[key].title}</option>`).join('')}</select></label><label class="form-field">UTF-8 CSV（字段名或中文表单标签）<input id="import-file" type="file" accept=".csv"></label><button id="tools-import" ${state.boot.read_only?'disabled':''}>预览导入资料</button></section></div>` +
    `<section class="card"><h2>数据维护说明</h2><p>批量导入、分摊、调薪和回款更正仍由原业务服务处理。恢复不自动升级数据库版本；所有明确确认均保留追溯，真实录入只使用一个窗口。</p></section>`;
  document.querySelector('#tools-export').onclick = () => safe(async () => {
    const module = document.querySelector('#export-module').value;
    const response = await api('rows?' + query({module}));
    downloadCSV(response.rows,state.boot.schema[module].columns,state.boot.schema[module].title);
  });
  installCSVControls();
  installBackupControls();
}

function sourceHTML(source) {
  return `<div class="source"><strong>${esc(source.name)} · v${source.version} · ${esc(source.locator)}</strong><p>${esc(source.excerpt || source.text || '')}</p><small>SHA-256 ${esc(source.sha256)}</small></div>`;
}

async function renderCashflow() {
  state.collection='cashflow';
  const scope = state.scope;
  const choices = state.boot.options;
  main.innerHTML = header('资金与计划','从资料提取付款条件和资金节奏；分析草稿不等于实际收付款。') + tabsHTML('funds','cashflow') +
    `<section class="card"><div class="flow-controls"><label>合同范围<select id="flow-contract">${optionList(choices.contracts,scope.contract_id)}</select></label><label>具体项目（可只分析整份合同）<select id="flow-project">${optionList(choices.projects,scope.project_id)}</select></label><button id="load-flow" class="primary">载入资料与分析</button></div><p class="page-note">整份合同和具体项目范围独立保存，避免把多项目合同总额误当作单一项目现金流。</p></section><div id="flow-content"></div>`;
  bindTabs('funds');
  document.querySelector('#load-flow').onclick = () => safe(async () => {
    state.scope={contract_id:Number(document.querySelector('#flow-contract').value)||null,project_id:Number(document.querySelector('#flow-project').value)||null};
    state.selectedAnalysis=null;await loadCashflow();
  });
  if (scope.contract_id || scope.project_id) await loadCashflow();
}

async function loadCashflow() {
  const scope = {...state.scope};
  const version = ++cashflowLoadVersion;
  if (!scope.contract_id && !scope.project_id) throw new Error('请选择现有合同或项目');
  const [documents,analyses,attachments] = await Promise.all([
    api('cashflow/documents?' + query(scope)),api('cashflow/analyses?' + query(scope)),
    api('cashflow/attachments?' + query(scope))]);
  const host = document.querySelector('#flow-content');
  if (version !== cashflowLoadVersion || !host) return;
  if (!state.selectedAnalysis && analyses.length) state.selectedAnalysis=analyses[0].id;
  const attachmentHTML = attachments.map(row => `<div class="list-note"><strong>${esc(row.original_name)}</strong><p>${esc(row.category)} · ${row.file_exists?(row.already_linked?'已加入当前范围分析':'已上传 · 尚未加入分析'):'原文件缺失，请核对'}</p><div class="stack-actions">${row.file_exists?`<a href="/api/attachment?id=${row.id}" target="_blank" rel="noopener">查看原附件</a><button data-read-attachment="${row.id}">读取并预览</button>`:''}</div></div>`).join('');
  host.innerHTML = `<div class="flow-grid"><section class="card"><div class="section-head"><h2>关联资料</h2><small>已有附件 ${attachments.length} 份 · 已加入分析 ${documents.length} 份</small></div><div class="stack-actions"><button id="upload-source">本地文件</button><button id="manual-source" ${state.boot.read_only?'disabled':''}>人工摘录</button><button id="link-source" ${attachments.some(row=>row.file_exists)?'':'disabled'}>已有附件</button><button id="source-history">资料历史</button></div><h3>已上传的业务附件</h3>${attachmentHTML || '<p class="page-note">当前范围暂无已上传附件，可选择本地文件。</p>'}<h3>已加入分析的资料</h3>${documents.map(doc => `<div class="list-note"><strong>${esc(doc.name)}</strong><p>${esc(doc.kind)} · v${doc.version} · ${esc(doc.sha256.slice(0,16))}</p><button data-source="${doc.id}">查看原文</button>${!state.boot.read_only?`<button data-retire="${doc.id}">移出分析</button>`:''}</div>`).join('') || '<p class="page-note">尚未加入分析资料。选择上方附件后直接理解内容，核对后确认关联；不会自动记账。</p>'}<details><summary>资料理解与发送范围</summary><p class="page-note">首次读取就使用当前配置的模型：PDF全部原始页面或图片直接交给视觉模型，Word、Excel和文本保留完整来源位置。仅发送本次选择的资料与最小项目、合同和我方主体名称，不发送其他账目；可能产生服务商费用。PDF一次最多20页，不会悄悄截断。模型理解收付方向、分期比例、相对期限和付款条件，金额由系统复算；不新增重新识别步骤。已有历史摘录保留，未自动重新发送。</p></details></section>
      <section><div class="card"><h2>生成 / 查看草稿</h2><div class="flow-controls"><label>可用启动资金（元）<input id="flow-opening" type="number" value="0" step="0.01"></label><label>回款延后天数<input id="flow-delay" type="number" value="30" step="1"></label><label>支出增加（%）<input id="flow-increase" type="number" value="10" step="1"></label><button id="analyze-flow" class="primary" ${state.boot.read_only?'disabled':''}>生成分析草稿</button></div><label class="form-field">历史分析<select id="analysis-history">${optionList(analyses.map(row=>({value:row.id,label:`#${row.id} · ${labels[row.status]} · ${row.created_at}`})),state.selectedAnalysis)}</select></label></div><div id="analysis-result"></div></section></div>`;
  document.querySelector('#upload-source').onclick=()=>safe(uploadSource);
  document.querySelector('#manual-source').onclick=()=>manualSource();
  document.querySelector('#link-source').onclick=()=>linkSource(attachments,null,scope);
  host.querySelectorAll('[data-read-attachment]').forEach(button=>button.onclick=()=>linkSource(attachments,Number(button.dataset.readAttachment),scope));
  document.querySelector('#source-history').onclick=()=>safe(async()=>{
    const history=await api('cashflow/documents?'+query({...scope,history:1}));
    openEditor('资料版本与原文历史',history.map(doc=>`<div class="source"><strong>${esc(doc.name)} · v${doc.version} · ${doc.active?'在用':'历史'}</strong><p>${esc(doc.change_reason)}</p><small>SHA-256 ${esc(doc.sha256)}</small><p>${esc(JSON.parse(doc.fragments_json).map(f=>f.locator+'：'+f.text).join('\n'))}</p></div>`).join(''),null);
  });
  host.querySelectorAll('[data-source]').forEach(button=>button.onclick=()=>{
    const doc=documents.find(row=>row.id===Number(button.dataset.source));
    openEditor(doc.name+' · v'+doc.version,JSON.parse(doc.fragments_json).map(fragment=>sourceHTML({...doc,...fragment})).join(''),null);
  });
  host.querySelectorAll('[data-retire]').forEach(button=>button.onclick=()=>safe(async()=>{
    const id=Number(button.dataset.retire);
    openEditor('移出资料分析',fieldHTML({name:'reason',label:'移出依据（旧版本保留，旧草稿会失效）',kind:'textarea',required:true}),async()=>{
      await api('cashflow/retire',{id,reason:form.elements.reason.value});editor.close();await loadCashflow();
    });
  }));
  document.querySelector('#analyze-flow').onclick=()=>safe(async()=>{
    state.selectedAnalysis=await api('cashflow/analyze',{...scope,opening:document.querySelector('#flow-opening').value,delay:document.querySelector('#flow-delay').value,increase:document.querySelector('#flow-increase').value});
    await loadCashflow();toast('草稿已生成。待补项和冲突未计入确定计划。');
  });
  document.querySelector('#analysis-history').onchange=event=>{state.selectedAnalysis=Number(event.target.value)||null;safe(renderAnalysis);};
  if (state.selectedAnalysis) await renderAnalysis();
}

async function uploadSource() {
  const scope = {...state.scope};
  openEditor('选择资料 · 理解并预览','<label class="form-field">选择资料<input type="file" name="file" accept=".txt,.md,.csv,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp" required></label><p class="page-note">读取时将本次资料及最小项目/合同上下文发送给你配置的模型。PDF与图片直接理解原始页面；可能产生模型费用。预览不改账目，核对后才关联。</p>',async()=>{
    await requestSourcePreview('cashflow/preview',fileData(form.elements.file.files[0]),scope,'合同正文','cashflow/save_document');
  },{label:'读取并预览'});
}

async function requestSourcePreview(path,data,scope,kind,endpoint) {
  const session=editorSession, view=loadingVersion;
  const current=()=>session===editorSession && view===loadingVersion && editor.open;
  let preview;
  try {
    const upload=await data;
    if (!current()) throw new SupersededView();
    preview=await api(path,{...scope,...upload,kind,use_model:true});
  } catch(error) {
    if (!current()) throw new SupersededView();
    throw error;
  }
  if (!current()) {
    try {await api('cashflow/discard',{token:preview.token});} catch {}
    throw new SupersededView();
  }
  if (!preview.understanding) {
    try {await api('cashflow/discard',{token:preview.token});} catch {}
    throw new Error('资料理解服务尚未更新，请重启工作台后读取；本次未保存资料。');
  }
  showSourcePreview(preview,scope,kind,endpoint);
}

function understandingHTML(understanding) {
  if (!understanding) return '';
  const nodes=understanding.nodes.map(node=>`<article class="understood-node"><div class="section-head"><h3>${esc(labels[node.direction] || '方向待核对')} · ${esc(node.title)}</h3><span class="status">${node.issues.length?'待补条件':'待核对'}</span></div><strong class="understood-amount">${money(node.amount_minor)}</strong><p>${esc(node.due_date || node.timing || '付款日期待明确')}</p><p>付款条件：${esc(node.condition)}</p>${node.counterparty?`<p>对方：${esc(node.counterparty)}</p>`:''}${node.calculation?`<small>系统复算：${esc(node.calculation)}</small>`:''}${node.issues.length?`<p class="page-note">${esc(node.issues.join('；'))}</p>`:''}<details><summary>原文依据</summary>${node.evidence.map(proof=>`<blockquote><small>${esc(proof.locator)}</small><p>${esc(proof.excerpt)}</p></blockquote>`).join('')}</details></article>`).join('');
  return `<section class="understanding-summary"><small>资料理解 · ${esc(understanding.model)}</small><h3>这份资料对资金安排意味着什么</h3><p>${esc(understanding.summary)}</p></section><section class="understood-nodes"><h3>收付款节点 · ${understanding.nodes.length} 项</h3>${nodes || '<p class="page-note">未发现有原文依据的收付款节点；不把材料或预算金额直接当作付款。</p>'}</section>${understanding.risks.length?`<details class="understanding-risks"><summary>经营风险与待核对事项 · ${understanding.risks.length} 项</summary>${understanding.risks.map(risk=>`<div class="list-note"><p>${esc(risk.text)}</p>${risk.evidence.map(proof=>`<small>${esc(proof.locator)}：${esc(proof.excerpt)}</small>`).join('')}</div>`).join('')}</details>`:''}`;
}

function showSourcePreview(preview,scope,kind,endpoint) {
  const fields=[{name:'kind',label:'资料类型',kind:'select',choices:['合同正文','补充协议/附件','材料清单','预算','采购付款安排','施工计划','人工补充'],default:preview.understanding?.kind || kind,required:true},{name:'change_reason',label:'同名资料换版原因',kind:'textarea'}];
  const recognition=preview.recognition || {ocr_pages:[],warnings:[]};
  const warnings=recognition.warnings.length?notice(recognition.warnings.join(' '),'warning'):'';
  const original=`<div class="source"><strong>来源 ${preview.fragments.length} 段${recognition.page_count?` · 原始页面 ${recognition.page_count} 页`:''}</strong><p class="page-note">文件指纹 ${esc(preview.sha256.slice(0,20))}</p><p>${esc(preview.fragments.map(f=>f.locator+'：'+f.text).join('\n'))}</p></div>`;
  const body=`${understandingHTML(preview.understanding)}${warnings}<div class="form-grid">${fields.map(field=>fieldHTML(field)).join('')}</div>${preview.understanding?`<details class="understanding-original"><summary>查看来源文字与文件指纹</summary>${original}</details>`:original}`;
  const acknowledgement=state.boot.read_only?notice('只读预览，不会关联资料或生成账目。'):'<label class="confirm-check"><input name="acknowledged" type="checkbox" required>我已对照原附件核对业务理解、金额、付款条件和项目归属，确认加入当前范围分析；不生成账目。</label>';
  openEditor('核对资料理解 · '+preview.name,body+acknowledgement,state.boot.read_only?null:async()=>{
    const result=await api(endpoint,{...scope,...formValues(fields),token:preview.token,acknowledged:form.elements.acknowledged.checked});
    cancelEditor=null;editor.close();toast(result.duplicate?'内容相同，复用原版本，未重复导入。':'核对后的资料已加入分析，未生成账目。');await loadCashflow();
  },{label:'确认关联资料',cancel:()=>api('cashflow/discard',{token:preview.token})});
}

function manualSource() {
  const fields=[{name:'name',label:'资料名称',required:true},{name:'kind',label:'类型',kind:'select',choices:['合同正文','补充协议/附件','材料清单','预算','采购付款安排','施工计划','人工补充'],default:'人工补充'},
    {name:'locator',label:'来源条款 / 页码 / 人工补充依据',required:true},{name:'text',label:'摘录原文或已核对的补充信息',kind:'textarea',required:true},{name:'change_reason',label:'同名资料换版原因',kind:'textarea'}];
  openEditor('人工摘录 / 补充资料',`<div class="form-grid">${fields.map(field=>fieldHTML(field)).join('')}</div>`,async()=>{
    const values=formValues(fields);
    const fragments=values.text.split('\n').filter(line=>line.trim()).map((text,index)=>({locator:values.locator+' / 摘录第'+(index+1)+'行',text}));
    await api('cashflow/save_document',{...state.scope,name:values.name,kind:values.kind,change_reason:values.change_reason,fragments});
    editor.close();await loadCashflow();
  });
}

function linkSource(attachments,selected,scope) {
  const choices=attachments.filter(row=>row.file_exists);
  const field={name:'attachment_id',label:'原业务附件',kind:'select',choices:Object.fromEntries(choices.map(row=>[row.id,row.original_name])),required:true};
  openEditor('读取已有业务附件',fieldHTML(field,selected || '')+'<p class="page-note">本次所选附件和最小项目/合同上下文将发送给你配置的模型，可能产生费用。PDF与图片直接理解原始页面；不改原件，下一步核对后才加入分析。</p>',async()=>{
    const id=Number(form.elements.attachment_id.value);
    const attachment=choices.find(row=>row.id===id);
    await requestSourcePreview('cashflow/attachment_preview',{attachment_id:id},scope,attachment.category==='合同附件'?'合同正文':'补充协议/附件','cashflow/link_attachment');
  },{label:'读取并预览'});
}

async function renderAnalysis() {
  if (!state.selectedAnalysis) return;
  const analysis=await api('cashflow/analysis?'+query({id:state.selectedAnalysis}));
  state.analysis=analysis;
  const payload=analysis.payload;
  const host=document.querySelector('#analysis-result');
  host.innerHTML=notice(`分析 #${analysis.id} · ${labels[analysis.status]} · ${analysis.stale_reason || '来源版本未变化，仍须人工核对条款及前置条件。'}`,analysis.stale_reason?'warning':'') +
    (payload.interpretations || []).map(item=>`<section class="card understanding-summary"><small>${esc(item.source.name)} · ${esc(item.model)}</small><h3>资料业务理解</h3><p>${esc(item.summary)}</p>${item.unreadable_sources.length?notice('未读清来源：'+item.unreadable_sources.join('、'),'warning'):''}</section>`).join('') +
    `<section class="metrics"><article class="metric"><label>资料日期 · 日末垫资缺口</label><strong>${money(payload.base.gap_minor)}</strong><small>同日先支后收 ${money(payload.base.outflow_first_gap_minor)}</small></article><article class="metric"><label>设定情景 · 垫资缺口</label><strong>${money(payload.scenario.gap_minor)}</strong><small>回款延后 ${payload.scenario.assumptions.income_delay_days} 天 · 支出增加 ${payload.scenario.assumptions.expense_increase_percent}%</small></article><article class="metric"><label>待补充 / 冲突</label><strong>${payload.base.excluded_keys.length} 项</strong><small>未计入时间表</small></article></section>` +
    `<section class="card"><div class="section-head"><h2>收支时间表</h2><div class="tabs"><button data-schedule="base" class="active">资料日期</button><button data-schedule="scenario">设定情景</button></div></div><div id="cashflow-schedule"></div><p class="page-note">${esc(payload.base.basis)} 未识别条款不代表零金额，缺口不是完整资金需求。</p></section>` +
    `<div class="section-head"><h2>逐项节点与原文</h2><a href="/api/cashflow/report?id=${analysis.id}" target="_blank" rel="noopener">查看 / 打印本地报告</a></div>` +
    payload.rows.map(row=>`<article class="flow-row"><div class="flow-row-head"><h3>${esc(labels[row.direction] || '方向待核对')} · ${esc(row.title)}</h3><span class="status ${row.conflict?'conflict':''}">${row.conflict?'资料冲突':row.issues.length?'待补充':'待人工确认'}</span></div><div class="number">${money(row.amount_minor)} <small>/ ${esc(row.due_date || row.timing || '日期待补充')}</small></div><p class="condition">前置条件：${esc(row.condition)}</p>${row.calculation?`<p class="page-note">系统复算：${esc(row.calculation)}</p>`:''}${row.issues.length?`<p class="page-note">${esc(row.issues.join('；'))}</p>`:''}<details><summary>查看 ${row.sources.length} 处条款与版本依据</summary>${row.sources.map(sourceHTML).join('')}</details></article>`).join('') +
    `<section class="card"><h2>注意事项 / 协商依据</h2>${payload.notes.map(note=>`<div class="list-note"><p>${esc(note.text)}</p>${sourceHTML(note.source)}</div>`).join('') || '<p class="page-note">未提取更多规则提示，请核对完整资料。</p>'}</section>` +
    `<div class="stack-actions"><button id="confirm-flow" class="primary" ${analysis.status!=='draft'||analysis.stale_reason||state.boot.read_only?'disabled':''}>逐项核对并转入收付计划</button><button id="cancel-flow" ${analysis.status!=='draft'||state.boot.read_only?'disabled':''}>取消草稿</button></div>` +
    (analysis.plan_ids.length?notice('已生成正式计划：'+analysis.plan_ids.join('、')+'。实际到账或付款仍需独立登记。'):'');
  const schedule=key=>{
    document.querySelector('#cashflow-schedule').innerHTML=table(payload[key].schedule,[['date','日期'],['in_minor','计划收款'],['out_minor','计划付款'],['balance_minor','预计资金余额']]);
    host.querySelectorAll('[data-schedule]').forEach(button=>button.classList.toggle('active',button.dataset.schedule===key));
  };
  host.querySelectorAll('[data-schedule]').forEach(button=>button.onclick=()=>schedule(button.dataset.schedule));schedule('base');
  document.querySelector('#confirm-flow').onclick=()=>{
    const eligible=payload.rows.filter(row=>!row.issues.length&&!row.conflict);
    openEditor('逐项确认正式计划',notice('只选择已核对的节点。金额使用原始节点，情景调整不进入计划；不产生实际收付款、收入或成本。') +
      eligible.map(row=>`<label class="confirm-check"><input name="row-${row.key}" type="checkbox" value="${row.key}">${esc(labels[row.direction]+' · '+row.title)} · ${money(row.amount_minor)} · ${row.due_date} · ${esc(row.condition)}</label>`).join('') +
      fieldHTML({name:'reason',label:'确认依据 / 经办说明',kind:'textarea',required:true}) +
      '<label class="confirm-check"><input name="acknowledged" type="checkbox" required>我已核对来源、金额、日期、前置条件及已有计划，确认所选节点可作为计划依据。</label>',async()=>{
        const keys=[...form.querySelectorAll('[name^="row-"]:checked')].map(input=>input.value);
        const ids=await api('cashflow/confirm',{id:analysis.id,keys,acknowledged:form.elements.acknowledged.checked,reason:form.elements.reason.value});
        editor.close();toast('正式计划已生成：'+ids.join('、'));await loadCashflow();
      },{label:'确认所选节点并生成计划'});
  };
  document.querySelector('#cancel-flow').onclick=()=>openEditor('取消分析草稿',notice('取消后不能再生成计划，来源和分析历史仍保留。'),async()=>{
    await api('cashflow/cancel',{id:analysis.id});editor.close();await loadCashflow();
  },{label:'确认取消草稿'});
}

async function refreshOptions() {
  state.boot.options=await api('options');
  document.querySelector('#project-scope').innerHTML='<option value="">全部项目</option>'+state.boot.options.projects.map(item=>`<option value="${item.value}" ${String(item.value)===state.project?'selected':''}>${esc(item.label)}</option>`).join('');
}

async function renderRoute(reset = true) {
  const version=++loadingVersion;
  const canonical=canonicalWorkbenchHash(location.hash);
  if (canonical!==location.hash) history.replaceState(history.state,'',canonical);
  const navigationContext = WorkbenchNavigation.prepare(reset);
  state.route=location.hash.slice(1).split('?')[0]||'home';
  document.body.classList.remove('nav-open');
  main.querySelectorAll('input').forEach(input=>input.blur());
  if (reset && !navigationContext.restored) {
    state.page=0;state.keyword='';state.collection='';
    const requestedTab = new URLSearchParams(location.hash.split('?')[1] || '').get('tab');
    if (groups[state.route]?.includes(requestedTab)) state.collection=requestedTab;
  }
  document.querySelectorAll('.nav-link').forEach(link=>link.classList.toggle('active',link.dataset.route===state.route));
  WorkbenchMotion.beginRoute(main);
  main.innerHTML='<div class="loading">正在读取现有业务事实…</div>';
  await suspendAssistant();
  if (version!==loadingVersion) return;
  try {
    if (state.route==='assistant') await renderAssistant();
    else if (['home','governance'].includes(state.route)) await renderDashboard(state.route);
    else if (groups[state.route]) await renderGroup(state.route,state.collection||groups[state.route][0]);
    else if (state.route==='entities') await renderEntities();
    else if (state.route==='cashflow') await renderCashflow();
    else if (state.route==='compare') await renderCompare();
    else if (state.route==='tools') await renderTools();
    else if (['customers','suppliers'].includes(state.route)) await renderPartnerOverview(state.route);
    else if (state.boot.schema[state.route]) await renderCollection(state.route);
    else main.innerHTML=notice('未知页面，请从左侧选择业务模块。');
    if (version!==loadingVersion) return;
    installAdvancedControls();
    if (state.boot.demo) main.insertAdjacentHTML('afterbegin',notice('合成演示数据 · 所有记录均为本机生成的测试资料，不代表真实项目或模型分析。','demo'));
    else if (state.boot.rehearsal) main.insertAdjacentHTML('afterbegin',notice('隔离演练副本 · 本窗口的录入和恢复只影响验收目录，不影响正式账本。','demo'));
    if (state.boot.read_only) main.insertAdjacentHTML('afterbegin',notice(`所选账本只读 · ${state.boot.database_label} · 可查账和导出，不会保存业务变更。`,'warning'));
  } catch(error) {if (version!==loadingVersion || error instanceof SupersededView) return; main.innerHTML=header('读取未完成','现有业务记录未被此操作修改。')+notice(error.message,'warning');}
  if (version===loadingVersion) {
    WorkbenchMotion.finishRoute(main);
    WorkbenchNavigation.finish(navigationContext);
  }
}

async function safe(action) {try {await action();} catch(error) {if (!(error instanceof SupersededView)) toast(error.message);}}

form.addEventListener('submit',async event=>{
  event.preventDefault();
  if (editorSubmitting || !submitEditor) return;
  if (editorLookups.size) {
    const failed = [...editorLookups.values()].includes('error');
    toast(failed ? '关联信息读取失败，请重新选择项目或材料后再保存。' : '关联信息正在读取，请稍后核对并保存。');
    return;
  }
  if (!form.reportValidity()) return;
  const submit = submitEditor;
  const session = editorSession;
  setEditorSubmitting(true);
  document.querySelector('#form-error').hidden=true;
  try {await submit();} catch(error) {
    if (session===editorSession && editor.open) {
      document.querySelector('#form-error').textContent=error.message;
      document.querySelector('#form-error').hidden=false;
    } else if (!(error instanceof SupersededView)) toast(error.message);
  } finally {setEditorSubmitting(false);}
});
document.querySelector('#editor-close').onclick=closeEditor;
document.querySelector('#editor-cancel').onclick=closeEditor;
editor.addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
document.querySelector('#menu-toggle').onclick=()=>document.body.classList.toggle('nav-open');
document.querySelector('#project-scope').onchange=event=>{state.project=event.target.value;safe(()=>renderRoute(false));};
window.addEventListener('hashchange',()=>safe(()=>renderRoute()));

async function start() {
  state.boot=await api('bootstrap');
  const icons={home:'M3 10l9-7 9 7M5 9v12h14V9M9 21v-7h6v7',profit:'M4 20V4m0 16h16M8 15l4-4 4 2 5-7',governance:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',contracts:'M6 3h9l4 4v14H6zM14 3v5h5M9 12h7m-7 4h7',cashflow:'M3 8h16l-4-4M21 16H5l4 4',finance:'M3 8h16l-4-4M21 16H5l4 4',funds:'M3 6h18v14H3zM3 10h18m-6 4h3',cost:'M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6m-6 4h6',projects:'M3 7h7l2 2h9v11H3zM3 7V4h7l2 3',construction:'M3 20h18M5 20V9h14v11M8 9V4h8v5M8 13h2m4 0h2',purchases:'M3 4h3l3 12h10l3-8H7M10 21h.01M18 21h.01',labor:'M4 5h16v16H4zM8 3v4m8-4v4M4 10h16m-13 4h2m4 0h2m-8 4h2',suppliers:'M3 21V9l6 3V7l6 3V3h6v18zM6 16h2m3 0h2m4 0h2',customers:'M16 21v-4c0-4-12-4-12 0v4M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M17 4a4 4 0 0 1 0 7m1 3c3 0 3 2 3 6',offers:'M3 3h9l9 9-9 9-9-9zM7 7h.01',compare:'M4 5h6v14H4zM14 3h6v18h-6',assistant:'M4 4h16v13H9l-5 4zM8 9h8m-8 4h5',tools:'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4',entities:'M4 21V5h16v16M8 9h2m4 0h2m-8 4h2m4 0h2m-7 8v-4h6v4'};
  document.querySelector('#navigation').innerHTML=state.boot.navigation.map(([group,links])=>`<section class="nav-group"><h2>${esc(group)}</h2>${links.map(([route,title])=>`<a class="nav-link" href="#${route}" data-route="${route}"><span class="nav-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[route]||icons.projects}"/></svg></span>${esc(title)}</a>`).join('')}</section>`).join('');
  WorkbenchMotion.init();
  WorkbenchNavigation.init();
  WorkbenchTableScrollbar.init();
  document.querySelector('#navigation').addEventListener('click', event => {
    if (event.target.closest('.nav-link')) document.body.classList.remove('nav-open');
  });
  document.querySelector('#mode').textContent=state.boot.demo?'隔离合成演示库':state.boot.rehearsal?'隔离演练账本':state.boot.read_only?'现有数据库 · 只读':'现有数据库 · 允许业务录入';
  await refreshOptions();
  enhanceForms(document.querySelector('.topbar'));
  await renderRoute();
}

document.addEventListener('DOMContentLoaded',()=>start().catch(error=>{main.innerHTML=notice('本机连接未完成：'+error.message,'warning');}),{once:true});
