'use strict';

function operationActions(module, id) {
  const entries=Object.entries(state.boot.operations || {}).filter(([,definition])=>definition.module===module && (definition.read || !state.boot.read_only));
  if (!entries.length) return '';
  return `<details class="operation-menu"><summary>更多业务</summary><div>${entries.map(([name,definition])=>`<button type="button" data-operation="${name}" data-id="${id}">${esc(definition.title)}</button>`).join('')}</div></details>`;
}

function bindOperationActions(host) {
  host.querySelectorAll('[data-operation]').forEach(button=>button.onclick=()=>safe(()=>openOperation(button.dataset.operation,{id:button.dataset.id})));
}

function evidenceHTML(value, title='业务事实', module='', valueKey='') {
  if (value===null || value===undefined) return notice('当前没有对应记录。');
  if (Array.isArray(value)) {
    if (!value.length) return notice('当前没有对应明细。');
    if (typeof value[0]!=='object') return `<p>${esc(display(value,valueKey,module))}</p>`;
    const keys=[...new Set(value.flatMap(row=>Object.keys(row)))].filter(visibleEvidenceKey);
    return table(value,keys.map(key=>[key,evidenceLabel(key,module)]),null,'records',module);
  }
  if (typeof value!=='object') return `<p>${esc(display(value,valueKey,module))}</p>`;
  return `<section class="card"><h3>${esc(title)}</h3>${Object.entries(value).filter(([key])=>visibleEvidenceKey(key)).map(([key,item])=>typeof item==='object' && item!==null ? evidenceHTML(item,evidenceLabel(key,module),module,key) : `<div class="list-note"><strong>${esc(evidenceLabel(key,module))}</strong><span>${esc(display(item,key,module))}</span></div>`).join('')}</section>`;
}

function visibleEvidenceKey(key) { return !key.startsWith('_') && !['fingerprint','public_id','organization_id','created_at','updated_at','request_key'].includes(key); }

function evidenceLabel(key, module='') {
  return fieldLabel(key,module);
}

function reviewHTML(review) {
  const summary=Object.fromEntries(['total_minor','book_minor','actual_minor','difference_minor','settled_minor'].filter(key=>review[key]!==undefined).map(key=>[key,review[key]]));
  if(review.impact)for(const key of ['old_amount_minor','new_amount_minor','delta_minor'])if(review.impact[key]!==undefined)summary[key]=review.impact[key];
  const metrics=Object.entries(summary).map(([key,value])=>`<article class="metric"><label>${esc(evidenceLabel(key))}</label><strong>${esc(money(value))}</strong></article>`).join('');
  const impact=review.impact?evidenceHTML(Object.fromEntries(Object.entries(review.impact).filter(([,value])=>value===null || typeof value!=='object')),'回算影响'):'';
  const wageNotice=review.impact?notice(`回算 ${review.impact.affected_count} 条 · 工资锁定跳过 ${review.impact.skipped_locked_count} 条 · 金额不变 ${review.impact.unchanged_count} 条`):'';
  return (metrics?`<section class="metrics review-metrics">${metrics}</section>`:'')+wageNotice+(review.notice?notice(review.notice):'')+notice('操作依据：'+review.reason)+evidenceHTML(review.changes,'拟执行的内容')+(impact?`<details><summary>查看完整回算影响</summary>${impact}</details>`:'')+(review.rows?.length?evidenceHTML(review.rows,'逐项影响明细'):'')+(review.ownership_projects?.length?evidenceHTML(review.ownership_projects,'关联项目自动归属影响'):'')+(review.before?`<details><summary>查看操作前的完整记录</summary>${evidenceHTML(review.before)}</details>`:'');
}

function excelReviewHTML(review,module) {
  const spec=state.boot.schema[module];
  return notice(`已在隔离副本核对 ${review.row_count} 行，将形成 ${review.record_count} 笔${spec.title}记录。正式台账尚未写入。`)+table(review.rows,spec.columns)+`<details><summary>展开完整字段逐项核对</summary>${evidenceHTML(review.rows)}</details>`;
}

function entityRecordOption(row) {
  const parts = [`#${row.id}`, row.label || '名称待补充'];
  if (row.projects && row.projects !== row.label) parts.push(row.projects);
  if (row.date) parts.push(row.date);
  if (row.amount_minor !== null && row.amount_minor !== undefined) parts.push(money(row.amount_minor));
  if (row.ownership_label) parts.push(`${row.ownership_label} · ${row.entity_name}`);
  return {value: row.id, label: parts.join(' · ')};
}

async function operationOptions(definition, context, currentOptions=state.boot.options) {
  const options={...currentOptions};
  for (const source of new Set(definition.fields.map(field=>field.source).filter(Boolean))) {
    if (source==='sites') {
      const rows=await api('operation_read?'+query({name:'sites',project_id:context.project_id || context.id || state.project}));
      options.sites=rows.map(row=>({value:row.id,label:`${row.site_code} · ${row.site_name}`}));
    } else if (source==='fund_sources') {
      const rows=await api('operation_read?'+query({name:source,kind:context.kind || 'purchase'}));
      options[source]=rows.map(row=>({value:row.id,label:`${row.label || row.source_name || row.id} · ${row.period || ''} · ${money(row.remaining_minor)}`}));
    } else if (source==='entity_records') {
      const rows=await api('operation_read?'+query({name:source,kind:context.kind || 'contracts',assignable:'1'}));
      options[source]=rows.map(entityRecordOption);
    } else if (source.startsWith('unassigned_')) {
      const rows=await api('operation_read?'+query({name:source,kind:context.kind || 'projects'}));
      options[source]=rows.map(row=>({value:row.id,label:`${row.name || row.project_name || row.worker_name || row.order_no || row.id} · ${row.work_date || ''}`}));
    }
  }
  if (definition.module==='worklogs') {
    const response=await api('rows?module=worklogs');
    options.ids=response.rows.map(row=>({value:row.id,label:`${row.work_date} · ${row.worker_name} · ${row.construction_site}`}));
  }
  return options;
}

function operationField(field,value,options) {
  if (field.kind==='multi') {
    const selected=new Set((value || []).map(String));
    return `<label class="form-field wide">${esc(field.label)}<select name="${field.name}" multiple size="5" ${field.required?'required':''}>${(options[field.source || 'ids'] || []).map(item=>`<option value="${esc(item.value)}" ${selected.has(String(item.value))?'selected':''}>${esc(item.label)}</option>`).join('')}</select><small>可使用 Ctrl / ⌘ 选择多条；只操作明确选中的记录。</small></label>`;
  }
  if (['allocation_rows','settlement_rows','invoice_links','worklog_rows'].includes(field.kind)) {
    return `<section class="card wide" data-row-field="${field.name}" data-row-kind="${field.kind}"><h3>${esc(field.label)}</h3><div class="operation-rows"></div><button type="button" class="add-operation-row">添加明细</button></section>`;
  }
  return fieldHTML(field,value,options);
}

function operationRowFields(kind) {
  const amount={name:'amount',label:'分摊 / 关联金额（元）',kind:'money',required:true};
  if(kind==='allocation_rows') return [{name:'project_id',label:'承担项目',kind:'select',source:'projects',required:true},amount];
  if(kind==='settlement_rows') return [{name:'settlement_id',label:'收入确认',kind:'select',source:'settlements',required:true},amount];
  if(kind==='invoice_links') return [{name:'kind',label:'成本来源',kind:'select',choices:{purchase_orders:'采购单',cost_entries:'费用'},default:'purchase_orders',required:true},{name:'record_id',label:'符合主体与供应商的成本记录',kind:'select',source:'cost_candidates',required:true},amount];
  return [{name:'worker_id',label:'工人',kind:'select',source:'workers',required:true},{name:'project_id',label:'核算项目',kind:'select',source:'active_projects',required:true},{name:'construction_site',label:'作业地点',required:true},{name:'work_date',label:'日期',kind:'date',required:true},{name:'work_days',label:'工天',kind:'number',default:'1',required:true},{name:'daily_rate',label:'日工资（留空按日期取工资版本）',kind:'money'},{name:'notes',label:'备注'}];
}

function initializeOperationRows(definition,options,context) {
  for (const host of editor.querySelectorAll('[data-row-field]')) {
    const kind=host.dataset.rowKind;
    const add=(initial={})=>{
      const row=document.createElement('div');row.className='form-grid operation-row';
      const fields=operationRowFields(kind);
      row.innerHTML=fields.map(field=>fieldHTML(field,initial[field.name],options)).join('')+`<button type="button" class="remove-operation-row">移除本行</button>`;
      if(kind==='invoice_links') {
        const update=()=>{row.querySelector('[name=record_id]').innerHTML=optionList((options.cost_candidates || []).filter(item=>item.kind===row.querySelector('[name=kind]').value),initial.record_id);};
        row.querySelector('[name=kind]').onchange=update;update();
      }
      row.querySelector('.remove-operation-row').onclick=()=>row.remove();host.querySelector('.operation-rows').append(row);
      enhanceForms(row);
    };
    host.querySelector('.add-operation-row').onclick=()=>add();
    for (const row of context[host.dataset.rowField] || []) add(row);
  }
}

function operationValues(definition,context) {
  const values={...context};
  for (const field of definition.fields) {
    const input=form.elements.namedItem(field.name);
    if (field.kind==='multi') values[field.name]=[...input.selectedOptions].map(option=>option.value);
    else if (['allocation_rows','settlement_rows','invoice_links','worklog_rows'].includes(field.kind)) {
      const host=editor.querySelector(`[data-row-field="${field.name}"]`);
      values[field.name]=[...host.querySelectorAll('.operation-row')].map(row=>Object.fromEntries([...row.querySelectorAll('input[name],select[name]')].map(element=>[element.name,element.value])));
    } else values[field.name]=field.kind==='checkbox'?input.checked:input.value;
  }
  return values;
}

async function openOperation(name,context={},viewOptions={}) {
  const request=beginEditorRequest();
  const definition=state.boot.operations[name];
  if (!definition) throw new Error('业务操作未开放');
  if (definition.read) {
    const result=await request.read('operation_read?'+query({name:definition.read,...context}));
    if (name==='project_cost_details') { openProjectCostDetails(result,viewOptions);return; }
    openEditor(definition.title,evidenceHTML(result,'业务事实',definition.module),null);return;
  }
  if (state.boot.read_only) throw new Error('当前只读');
  if (name==='labor_batch') return openWorkdayBatch(context);
  if (definition.module==='workers') context.worker_id=context.id;
  if (definition.module==='accounts') context.account_id=context.id;
  if (definition.module==='worklogs') context.ids=[context.id];
  if (name==='collection_followup') context={...await api('operation_read?'+query({name:'collection_case',id:context.id})),...context};
  if(name==='receipt_allocation') {
    const receipt=await api('record?'+query({module:'receipts',id:context.id}));
    context.project_id=receipt.project_id;
    context.contract_id=receipt.contract_id;
    context.allocations=receipt.allocations.filter(row=>row.settlement_id && row.status==='active').map(row=>({settlement_id:row.settlement_id,amount:minorInput(row.allocated_amount_minor)}));
    context.allow_advance=BigInt(receipt.advance_minor || 0)>0n;
  }
  if(name==='allocation_update') context.amount=(await api('record?'+query({module:'allocations',id:context.id}))).amount;
  if(name==='cost_allocate') {
    const current=await api('operation_read?'+query({name:'cost_allocations',id:context.id}));context.method=current.method;context.project_ids=current.lines.map(row=>row.project_id);
    if(current.method==='manual') context.allocations=current.lines.map(row=>({project_id:row.project_id,amount:minorInput(row.amount_minor)}));
  }
  const currentOptions=await request.read('options?'+query({project_id:context.project_id || state.project}));
  const options=await operationOptions(definition,context,currentOptions);
  if(name==='receipt_allocation')options.settlements=options.settlements.filter(row=>
    row.project_id===context.project_id&&row.contract_id===context.contract_id);
  if(name==='input_links') {
    const invoice=await api('record?'+query({module:'input_invoices',id:context.id}));
    context.links=invoice.links.map(row=>({kind:row.purchase_order_id?'purchase_orders':'cost_entries',record_id:row.purchase_order_id || row.cost_entry_id,amount:minorInput(row.amount_minor)}));
    options.cost_candidates=[];
    for(const kind of ['purchase_orders','cost_entries']) {
      const rows=await api('operation_read?'+query({name:'input_candidates',entity_id:invoice.entity_id,supplier_id:invoice.supplier_id,kind}));
      options.cost_candidates.push(...rows.map(row=>({kind,value:row.id,label:`${row.label} · ${row.counterparty} · 总额 ${money(row.amount_minor)} · 已关联 ${money(row.linked_minor)}`})));
    }
  }
  request.check();
  openEditor(definition.title,notice('填写操作依据并预览。预览和取消均不改动经营记录。')+`<div class="form-grid">${definition.fields.map(field=>operationField(field,context[field.name],options)).join('')}</div>`,async()=>{
    const values=operationValues(definition,context);
    const result=await api('operation_preview',{name,record:values});
    openEditor('确认预览 · '+definition.title,reviewHTML(result.review)+`<label class="confirm-check"><input name="operation-confirmed" type="checkbox" required>我已核对来源、金额、日期、影响范围和操作依据，确认执行。</label>`,async()=>{
      await api('operation_apply',{token:result.token,confirmed:form.elements.namedItem('operation-confirmed').checked});
      cancelEditor=null;editor.close();toast('操作已由原有业务服务完成。');await refreshOptions();await renderRoute(false);
    },{label:'确认执行',cancel:()=>api('operation_discard',{token:result.token})});
  },{label:'预览影响'});
  initializeOperationRows(definition,options,context);
  const choices=definition.fields.filter(field=>['fund_sources','entity_records','sites'].includes(field.source))
    .map(field=>[form.elements.namedItem(field.name),field.source]).filter(([select])=>select);
  bindEditorChoices(['kind','project_id'].map(name=>form.elements.namedItem(name)),choices,
    ()=>operationOptions(definition,operationValues(definition,context),currentOptions));
}

function projectPurchasePrice(row) {
  if (row.settlement_mode==='freight') return '—';
  if (row.settlement_mode==='total') return '整批结算';
  let price = money(row.unit_price_cents);
  if (row.settlement_mode==='weight') {
    // Weight prices can have sub-cent precision; never round them for display.
    if (row.unit_price_yuan===null || row.unit_price_yuan===undefined) return '待补充';
    const [integer,fraction=''] = String(row.unit_price_yuan).split('.');
    price = '¥' + BigInt(integer).toLocaleString('en-US') + '.' + fraction.padEnd(2,'0');
  }
  return price + (row.unit ? '/' + row.unit : '');
}

function projectPurchaseTable(rows) {
  if (!rows.length) return table([],[],null,'project-cost-purchases');
  const headings = ['材料 / 规格','数量 / 重量','单价','本条金额（含税）','本项目成本'];
  const body = rows.map(row => {
    const freight = row.settlement_mode==='freight';
    const quantity = row.quantity===null || row.quantity===undefined ? '—' : `${row.quantity} ${row.unit || ''}`.trim();
    const basis = row.price_basis==='inclusive' ? '含税' : '未税';
    const priceNote = freight ? '单独计费' : `${basis} · 税率 ${Number(row.tax_rate_bps || 0)/100}%`;
    const facts = [['采购单号',row.source_no],['付款状态',row.payment_status]];
    if (!freight) facts.push(['未税金额',money(row.line_material_minor)],['税额',money(row.line_tax_minor)]);
    if (row.purpose) facts.push(['用途',row.purpose]);
    if (row.weigh_ticket_no) facts.push(['磅单号',row.weigh_ticket_no]);
    const material = `<strong>${esc(row.description)}</strong>${row.specification ? `<span class="purchase-cost-spec">${esc(row.specification)}</span>` : ''}` +
      `<small>${esc(row.counterparty || '供应商待补充')} · <span class="purchase-cost-date">${esc(row.date)}</span></small>` +
      `<details class="purchase-cost-facts"><summary>查看明细</summary><dl>${facts.map(([label,value])=>`<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')}</dl></details>`;
    const cells = [material,`<strong>${esc(quantity)}</strong>${row.settlement_mode==='weight' ? '<small>按过磅重量</small>' : ''}`,
      `<strong>${esc(projectPurchasePrice(row))}</strong><small>${esc(priceNote)}</small>`,
      `<strong>${esc(money(row.line_amount_minor))}</strong><small>${freight ? '整单运费' : '不含另列运费'}</small>`,
      `<strong>${esc(money(row.amount_minor))}</strong><small>${row.allocation_method==='equal' ? '多项目分摊' : '直接归集'}</small>`];
    return '<tr>' + cells.map((cell,index)=>`<td data-label="${headings[index]}" class="${index>0 ? 'purchase-cost-number' : 'purchase-cost-material'}">${cell}</td>`).join('') + '</tr>';
  }).join('');
  return `<div class="table-wrap project-cost-purchases"><table id="project-cost-purchases"><caption class="purchase-cost-caption">每条采购的数量、计价单价、含税金额与本项目承担成本</caption><thead><tr>${headings.map(label=>`<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function openProjectCostDetails(data,options={}) {
  const sections = {
    purchase: {title:'采购材料'},
    labor: {title:'人工工天', columns:[['date','出勤日期'],['source_no','工天编号'],['worker','工人'],['days','工天'],['is_overtime','是否加班'],['site','作业地点'],['amount_minor','人工成本'],['notes','备注']]},
    other: {title:'其他费用', columns:[['date','发生日期'],['source_no','费用单号'],['category','费用分类'],['counterparty','往来单位 / 人员'],['amount_minor','项目承担金额'],['notes','备注']]},
  };
  const metrics = {total_cost_minor:data.total_cost_minor,purchase_minor:data.totals.purchase,labor_minor:data.totals.labor,other_cost_minor:data.totals.other};
  openEditor('项目成本明细 · ' + data.project.name,
    `${options.back ? `<button type="button" id="project-cost-back">← ${esc(options.backLabel || '返回明细')}</button>` : ''}<p class="page-note">${esc(data.project.project_code || '')} · 全周期有效记录，不受月度筛选限制</p><div class="project-cost-summary">${metricCards(metrics)}</div>` +
    `<div class="tabs" role="tablist" aria-label="成本分类">${Object.entries(sections).map(([key,section]) => `<button type="button" role="tab" aria-selected="false" data-cost-tab="${key}">${section.title} · ${data[key].length} 条</button>`).join('')}</div>` +
    '<div id="project-cost-rows"></div>' + notice('本条金额是整条材料的含税金额，运费另列；本项目成本是当前项目承担的部分。多项目材料拆分仅供查看，以采购单的项目归集金额为准。成本归集不等于实际付款。'), null,
    {note:'项目全周期成本 · 只读明细',cancelLabel:'关闭'});
  editor.querySelector('#project-cost-back')?.addEventListener('click',()=>safe(options.back));
  const select = key => {
    const section = sections[key];
    const rows = key==='purchase' ? projectPurchaseTable(data[key]) : table(data[key],section.columns,null,'project-cost-table');
    document.querySelector('#project-cost-rows').innerHTML = `<div class="section-head"><h3>${section.title}</h3><strong class="amount">${money(data.totals[key])}</strong></div>` + rows;
    editor.querySelectorAll('[data-cost-tab]').forEach(button => {
      const active = button.dataset.costTab===key;
      button.classList.toggle('active',active);
      button.setAttribute('aria-selected',String(active));
    });
  };
  editor.querySelectorAll('[data-cost-tab]').forEach(button => button.onclick = () => select(button.dataset.costTab));
  select('purchase');
}

function installAdvancedControls() {
  const route=state.route;
  if (route==='finance') {
    main.insertAdjacentHTML('beforeend','<section class="card"><button id="void-receipt-history">查看已作废回款记录</button><small>记录、金额、日期及作废原因仍保留，不参与有效回款合计。</small></section>');
    document.querySelector('#void-receipt-history').onclick=()=>safe(()=>openOperation('receipt_history'));
  }
  const modules={funds:['funds','accounts','entities'],governance:['governance','entities'],labor:['workers','worklogs'],tools:['entities']}[route] || [];
  if (modules.length) {
    const actions=Object.entries(state.boot.operations || {}).filter(([name,definition])=>
      modules.includes(definition.module) && !definition.read && !state.boot.read_only &&
      !['fund_archive','worker_delete'].includes(name) && !(name==='labor_batch' && state.collection==='worklogs'));
    main.insertAdjacentHTML('beforeend',`<section class="card"><h2>经营操作 · 核对后执行</h2><div class="head-actions">${actions.map(([name,definition])=>`<button data-operation="${name}">${esc(definition.title)}</button>`).join('')}</div><button id="read-business">查看业务明细与历史</button></section>`);
    bindOperationActions(main);
    document.querySelector('#read-business').onclick=()=>safe(openReadConsole);
  }
  if (route==='tools') installExcelControls();
  if(route==='finance') installPDFControls();
}

function installExcelControls() {
  main.insertAdjacentHTML('afterbegin',`<section class="card"><h2>Excel 模板 · 批量核对</h2><p>沿用桌面供应商、报价和采购模板。预览在隔离副本完成；任一行失败，整批不提交。采购单重复、报价重复及未计算公式会明确拒绝。</p><label class="form-field">模板类型<select id="excel-module"><option value="suppliers">供应商</option><option value="offers">材料报价</option><option value="purchases">采购单（含多明细）</option></select></label><label class="form-field">本机 Excel 文件<input id="excel-file" type="file" accept=".xlsx"></label><div class="head-actions"><button id="excel-preview" ${state.boot.read_only?'disabled':''}>预览整批导入</button><button id="excel-template">导出对应模板与台账</button><a class="button" href="/api/excel_export?module=operating" download>导出经营工作簿</a></div><small>导出附带完整字段和整数分文本，保留大额金额精度。</small></section>`);
  document.querySelector('#excel-template').onclick=()=>{location.href='/api/excel_export?'+query({module:document.querySelector('#excel-module').value});};
  document.querySelector('#excel-preview').onclick=()=>safe(async()=>{
    const file=document.querySelector('#excel-file').files[0];if(!file || file.size>20*1024*1024)throw new Error('请选择不超过20MB的 .xlsx 文件');
    const module=document.querySelector('#excel-module').value;
    const result=await api('excel_preview',{module,...await fileData(file)});
    openEditor('Excel 整批导入预览',excelReviewHTML(result.review,module)+`<label class="confirm-check"><input name="excel-confirmed" type="checkbox" required>已逐行核对项目、供应商、日期和金额，确认整批导入。</label>`,async()=>{
      await api('excel_apply',{token:result.token,confirmed:form.elements.namedItem('excel-confirmed').checked});cancelEditor=null;editor.close();toast('Excel 整批导入完成。');await refreshOptions();await renderRoute(false);
    },{label:'确认整批导入',cancel:()=>api('excel_discard',{token:result.token})});
  });
}

async function openReadConsole() {
  const request=beginEditorRequest();
  const options=await request.read('options?'+query({project_id:state.project}));
  const reads={fund_sources:'业务付款核实来源',reconciliations:'账户余额核对历史',entities:'经营主体',entity_records:'业务主体归属',labor_month:'月度工资与工天',worker_month:'工人月度明细',customer_business:'客户业务明细',input_candidates:'进项票候选成本',input_links:'进项票关联记录',historical_receipts:'历史回款',collection_history:'催收跟进历史',fulfillment_gaps:'履约缺口',sites:'施工地点'};
  const fields=[{name:'name',label:'查询内容',kind:'select',choices:reads},{name:'id',label:'对应记录 / 工人 / 客户编号',kind:'integer'},{name:'kind',label:'来源类型（purchase/cost/wage 或主体记录类型）'},{name:'entity_id',label:'经营主体',kind:'select',source:'entities'},{name:'supplier_id',label:'供应商',kind:'select',source:'suppliers'},{name:'month',label:'月份 YYYY-MM'},{name:'start',label:'期间开始',kind:'date'},{name:'end',label:'期间结束',kind:'date'},{name:'year',label:'年份',kind:'integer'}];
  openEditor('业务明细与历史',`<div class="form-grid">${fields.map(field=>fieldHTML(field,undefined,options)).join('')}</div><div id="read-result"></div>`,async()=>{const result=await api('operation_read?'+query(formValues(fields)));document.querySelector('#read-result').innerHTML=evidenceHTML(result);},{label:'查询'});
}

async function inputPDF(id) {
  const request=beginEditorRequest();
  const record=await request.read('record?'+query({module:'input_invoices',id}));
  openEditor('进项票 PDF · 本机规则识别',notice('仅提取文字型 PDF。扫描页、缺失金额和冲突字段必须人工补充；识别结果不会改变现有发票金额或抵扣状态。')+evidenceHTML({invoice_no:record.invoice_no,attachment_status:record.attachment_status})+`<label class="form-field">本机 PDF<input name="pdf" type="file" accept=".pdf" required></label>`,async()=>{
    const result=await api('input_pdf_preview',await fileData(form.elements.pdf.files[0]));
    openEditor('核对 PDF 来源',notice('以下是规则识别参考。确认只关联原文件；发票日期、金额和购销双方字段请在发票编辑入口核对。')+evidenceHTML(result.recognition)+`<label class="confirm-check"><input name="pdf-confirmed" type="checkbox" required>我已核对文件与本张进项票一致，确认关联原文件。</label>`,async()=>{
      await api('input_pdf_attach',{token:result.token,id,expected:record._version,confirmed:form.elements.namedItem('pdf-confirmed').checked});cancelEditor=null;editor.close();toast('原 PDF 已由现有进项票服务关联。');await renderRoute(false);
    },{label:'确认关联 PDF',cancel:()=>api('cashflow/discard',{token:result.token})});
  },{label:'提取文字预览'});
}
