'use strict';

const explicitConfirmation = (name,text) => `<label class="confirm-check"><input name="${name}" type="checkbox" required>${esc(text)}</label>`;

async function evidenceOperation(values, after) {
  openEditor('核对资料变更依据',notice('移出保留原记录和文件，可从历史列表恢复；分类变更保留原说明。')+fieldHTML({name:'reason',label:'变更依据',kind:'textarea',required:true}),async()=>{
    const result=await api('evidence_preview',{...values,reason:form.elements.reason.value});
    openEditor('确认资料变更',evidenceHTML(result.review.record)+explicitConfirmation('evidence-confirmed','已核对当前业务记录和资料，确认执行。'),async()=>{
      await api('evidence_apply',{token:result.token,confirmed:form.elements.namedItem('evidence-confirmed').checked});cancelEditor=null;await after();
    },{label:'确认执行',cancel:()=>api('evidence_discard',{token:result.token})});
  },{label:'预览变更'});
}

async function attachments(module,id,history=false) {
  const request=beginEditorRequest();
  const entity=state.boot.schema[module].attachment;
  const rows=await request.read('attachments?'+query({entity,id,history:history?'1':''}));
  const ticket=state.boot.read_only?null:await request.read('write_ticket?'+query({kind:'upload_attachment',entity,id}));
  const content=rows.map(row=>`<article class="list-note"><strong>${esc(row.original_name)}</strong><p>${esc(row.category)} · ${row.status==='void'?'已移出，原文件保留':row.file_exists?'文件在本机':'文件缺失'}</p><p class="source-note">${esc(row.description)}</p><div class="attachment-actions">${attachmentPreviewButton(row)}${row.file_exists?`<a href="/api/attachment?${query({id:row.id,history:row.status==='void'?'1':''})}" target="_blank" rel="noopener">下载原附件</a>`:''}${!state.boot.read_only?`<button type="button" data-evidence-id="${row.id}" data-evidence-action="${row.status==='void'?'attachment_restore':'attachment_void'}" ${!row.file_exists && row.status==='void'?'disabled':''}>${row.status==='void'?'恢复到附件列表':'移出附件'}</button>`:''}</div>${row.file_exists && !row.preview_kind?'<small>此格式暂不支持预览，可下载原附件查看。</small>':''}</article>`).join('') || '<p class="muted">暂无业务附件</p>';
  openEditor('业务附件 · 文件保留',`<button id="attachment-history" type="button">${history?'仅看有效附件':'查看已移出附件'}</button>`+content+(!state.boot.read_only?`<label class="form-field">添加本地附件<input type="file" name="attachment" required></label>${fieldHTML({name:'category',label:'资料分类',default:'业务附件'})}${fieldHTML({name:'description',label:'说明'})}`:''),state.boot.read_only?null:async()=>{
    await api('upload_attachment',{entity,id,form_ticket:ticket.token,...await fileData(form.elements.attachment.files[0]),category:form.elements.category.value,description:form.elements.description.value,confirmed:true});cancelEditor=null;await attachments(module,id,history);toast('原附件保存在本机。');
  },{label:'保存附件',cancel:()=>ticket&&api('write_discard',{token:ticket.token})});
  document.querySelector('#attachment-history').onclick=()=>safe(()=>attachments(module,id,!history));
  editor.querySelectorAll('[data-evidence-id]').forEach(button=>button.onclick=()=>safe(()=>evidenceOperation({action:button.dataset.evidenceAction,entity,id,attachment_id:Number(button.dataset.evidenceId)},()=>attachments(module,id,true))));
}

async function constructionPhotos(id,filter='全部',history=false) {
  const request=beginEditorRequest();
  const record=await request.read('record?'+query({module:'construction',id}));
  const rows=await request.read('photos?'+query({id}));
  const ticket=state.boot.read_only?null:await request.read('write_ticket?'+query({kind:'photos_upload',id,expected:record._version}));
  const selected=rows.filter(row=>(filter==='全部' || row.photo_type===filter) && (history || row.attachment_status!=='void'));
  openEditor('施工照片 · 分类管理',notice('照片归属于当前施工记录。移出保留照片记录和文件，历史列表可恢复。')+`<div class="filter-bar"><label>照片分类<select id="photo-filter">${['全部','施工现场','验收照片'].map(type=>`<option ${type===filter?'selected':''}>${type}</option>`).join('')}</select></label><button type="button" id="photo-history">${history?'仅看有效照片':'查看移出历史'}</button></div><div class="photo-grid">${selected.map(row=>`<article class="card"><a href="/api/attachment?${query({id:row.attachment_id,history:row.attachment_status==='void'?'1':''})}" target="_blank" rel="noopener"><img src="/api/attachment?${query({id:row.attachment_id,view:'1',history:row.attachment_status==='void'?'1':''})}" alt="${esc(row.original_name)}" loading="lazy"></a><strong>${esc(row.original_name)}</strong><p>${esc(row.photo_type)} · ${row.attachment_status==='void'?'已移出':'有效'}</p><p class="source-note">${esc(row.notes)}</p>${!state.boot.read_only?`<div class="head-actions"><button type="button" data-photo-category="${row.id}" data-current-category="${esc(row.photo_type)}">修改分类</button><button type="button" data-photo-evidence="${row.attachment_id}" data-photo-state="${row.attachment_status==='void'?'attachment_restore':'attachment_void'}">${row.attachment_status==='void'?'恢复照片':'移出照片'}</button></div>`:''}</article>`).join('') || '<p class="muted">此分类暂无照片</p>'}</div>`+(!state.boot.read_only?`<div class="form-grid">${fieldHTML({name:'category',label:'上传分类',kind:'select',choices:['施工现场','验收照片'],default:'施工现场',required:true})}<label class="form-field">本机照片（最多20张）<input name="photos" type="file" accept=".png,.jpg,.jpeg,.webp" multiple required></label>${fieldHTML({name:'notes',label:'照片说明',kind:'textarea'})}</div>`+explicitConfirmation('photo-confirmed','确认这些照片属于当前施工记录，分类和说明已核对。'):''),state.boot.read_only?null:async()=>{
    const files=[...form.elements.photos.files];if(!files.length || files.length>20 || files.reduce((total,file)=>total+file.size,0)>19*1024*1024)throw new Error('每次1至20张，合计不超过19MB');
    await api('photos_upload',{id,expected:record._version,form_ticket:ticket.token,category:form.elements.category.value,notes:form.elements.notes.value,files:await Promise.all(files.map(fileData)),confirmed:form.elements.namedItem('photo-confirmed').checked});cancelEditor=null;await constructionPhotos(id,filter,history);toast('照片与原施工记录已关联。');
  },{label:'确认保存照片',cancel:()=>ticket&&api('write_discard',{token:ticket.token})});
  document.querySelector('#photo-filter').onchange=event=>safe(()=>constructionPhotos(id,event.target.value,history));
  document.querySelector('#photo-history').onclick=()=>safe(()=>constructionPhotos(id,filter,!history));
  editor.querySelectorAll('[data-photo-evidence]').forEach(button=>button.onclick=()=>safe(()=>evidenceOperation({action:button.dataset.photoState,entity:'construction',id,attachment_id:Number(button.dataset.photoEvidence)},()=>constructionPhotos(id,filter,true))));
  editor.querySelectorAll('[data-photo-category]').forEach(button=>button.onclick=()=>{
    openEditor('修改照片分类',fieldHTML({name:'category',label:'新分类',kind:'select',choices:['施工现场','验收照片'],default:button.dataset.currentCategory,required:true}),async()=>evidenceOperation({action:'photo_category',id,photo_id:Number(button.dataset.photoCategory),category:form.elements.category.value},()=>constructionPhotos(id,'全部',history)),{label:'填写变更依据'});
  });
}

async function renderMonthlyView() {
  const today=state.boot.schema.worklogs.fields.find(field=>field.name==='work_date').default;
  main.innerHTML=header('人工与工天','工天矩阵按独立日期区间汇总全公司有效记录，加班计入总工天。')+tabsHTML('labor','monthly')+`<section class="card"><div class="filter-bar"><label>开始日期<input id="matrix-start" type="date" value="${today.slice(0,4)}-01-01"></label><label>结束日期<input id="matrix-end" type="date" value="${today}"></label><button id="matrix-query" class="primary">查询月度工天</button></div><p class="page-note">与右上角项目范围独立；点击月份数字查看逐日出勤，不展示工资金额。</p><div id="matrix-result"></div></section>`;
  bindTabs('labor');document.querySelector('#matrix-query').onclick=()=>safe(loadMonthlyMatrix);await loadMonthlyMatrix();
}

async function loadMonthlyMatrix() {
  const period={start:document.querySelector('#matrix-start').value,end:document.querySelector('#matrix-end').value};
  const result=await api('monthly_workdays?'+query(period));
  const counts=new Map();result.workers.forEach(row=>counts.set(row.worker_name,(counts.get(row.worker_name)||0)+1));
  document.querySelector('#matrix-result').innerHTML=notice(`${result.start_date} 至 ${result.end_date} · ${result.worker_count}名工人 · ${result.work_days}工天（加班 ${result.overtime_days}）`)+`<div class="table-wrap"><table id="monthly-matrix"><thead><tr><th>工人</th><th>状态</th><th>合计工天</th>${result.months.map(month=>`<th>${esc(month)}</th>`).join('')}</tr></thead><tbody>${result.workers.map(row=>`<tr><td>${esc(row.worker_name)}${counts.get(row.worker_name)>1?`（档案${row.worker_id}）`:''}</td><td>${esc(row.worker_status)}</td><td>${row.work_days}</td>${result.months.map(month=>`<td>${Object.hasOwn(row.months,month)?`<button data-matrix-worker="${row.worker_id}" data-matrix-month="${month}">${row.months[month]}</button>`:''}</td>`).join('')}</tr>`).join('')}</tbody><tfoot><tr><th colspan="3">月份合计</th>${result.months.map(month=>`<td>${result.month_totals[month]}</td>`).join('')}</tr></tfoot></table></div>`;
  main.querySelectorAll('[data-matrix-worker]').forEach(button=>button.onclick=()=>safe(async()=>{
    const detail=await api('operation_read?'+query({name:'worker_month',id:button.dataset.matrixWorker,month:button.dataset.matrixMonth,...period}));
    openEditor('逐日出勤 · '+button.dataset.matrixMonth,notice(`${detail.record_count}条出勤 · ${detail.work_days}工天 · 加班${detail.overtime_days}`)+table(detail.details.map(row=>({...row,attendance:row.is_overtime?'加班':'正常'})),[['work_date','日期'],['project_name','项目'],['construction_site','施工地点'],['work_type','工作内容'],['attendance','出勤类型'],['work_days','工天']]),null);
  }));
}

async function renderCompare() {
  const {rows}=await api('rows?module=offers');
  main.innerHTML=header('报价对比','复用原报价与客商评价；最低含税价仅在材料、规格、单位一致的组内比较。')+`<div class="filter-bar"><input id="compare-filter" placeholder="材料名称" aria-label="材料名称"><input id="compare-spec" placeholder="规格" aria-label="规格"><select id="compare-unit" aria-label="单位"><option value="">全部单位（分别比较）</option>${[...new Set(rows.map(row=>row.unit))].map(unit=>`<option>${esc(unit)}</option>`).join('')}</select></div><div id="comparison"></div>`;
  const update=()=>{
    const name=document.querySelector('#compare-filter').value.trim().toLowerCase(),spec=document.querySelector('#compare-spec').value.trim().toLowerCase(),unit=document.querySelector('#compare-unit').value;
    const selected=rows.filter(row=>row.name.toLowerCase().includes(name) && row.specification.toLowerCase().includes(spec) && (!unit || row.unit===unit)).sort((a,b)=>BigInt(a.tax_inclusive_price_minor)<BigInt(b.tax_inclusive_price_minor)?-1:BigInt(a.tax_inclusive_price_minor)>BigInt(b.tax_inclusive_price_minor)?1:0);
    const groups=new Map();selected.forEach(row=>{const key=JSON.stringify([row.name,row.specification,row.unit]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);});
    const comparison=selected.map(row=>({...row,recommendation:BigInt(row.tax_inclusive_price_minor)===BigInt(groups.get(JSON.stringify([row.name,row.specification,row.unit]))[0].tax_inclusive_price_minor)?'组内最低含税价':'',tax_rate_label:row.tax_rate_percent+'%'}));
    document.querySelector('#comparison').innerHTML=[...groups.values()].map(group=>{
      const max=group.reduce((value,row)=>BigInt(row.tax_inclusive_price_minor)>value?BigInt(row.tax_inclusive_price_minor):value,1n);
      return `<section class="card quote-chart"><h2>${esc(group[0].name)} · ${esc(group[0].specification)} · ${esc(group[0].unit)}</h2><p class="page-note">相同计量口径 · 蓝色含税价 / 灰色未税价；供应商评价取自档案，未计算质量评分。</p>${group.map(row=>`<div class="quote-bar-row"><strong>${esc(row.supplier_name)}</strong><svg viewBox="0 0 500 44" role="img" aria-label="${esc(row.supplier_name+' 含税'+money(row.tax_inclusive_price_minor)+' 未税'+money(row.price_minor))}"><rect x="0" y="1" width="${Number(BigInt(row.tax_inclusive_price_minor)*480n/max)}" height="18" class="gross-bar"/><rect x="0" y="23" width="${Number(BigInt(row.price_minor)*480n/max)}" height="15" class="net-bar"/></svg><span>含税 ${money(row.tax_inclusive_price_minor)}<small>未税 ${money(row.price_minor)}</small></span></div>`).join('')}</section>`;
    }).join('')+table(comparison,[['recommendation','最低价标记'],['supplier_name','供应商'],['category','类别'],['name','材料'],['specification','规格'],['price_minor','未税价'],['tax_rate_label','税率'],['tax_inclusive_price_minor','含税价'],['unit','单位'],['quality','质量'],['price_level','价格评价'],['export','出口能力'],['notes','备注'],['updated_at','更新时间']]);
  };
  for(const id of ['compare-filter','compare-spec','compare-unit'])document.querySelector('#'+id).oninput=update;update();
}

function installCSVControls() {
  document.querySelector('#tools-import').insertAdjacentHTML('afterend','<button id="csv-template">下载 CSV 模板</button>');
  document.querySelector('#csv-template').onclick=()=>{location.href='/api/csv_template?'+query({module:document.querySelector('#import-module').value});};
  document.querySelector('#tools-import').onclick=()=>safe(async()=>{
    const file=document.querySelector('#import-file').files[0];if(!file || file.size>2*1024*1024)throw new Error('请选择不超过2MB的 UTF-8 CSV');
    const module=document.querySelector('#import-module').value,result=await api('csv_preview',{module,...await fileData(file)});
    openEditor('CSV 整批导入预览',notice(`已在隔离副本核对全部 ${result.review.count} 条资料。任何一行失败整批不写入；重复资料须回原档案编辑。`)+`<div id="csv-review"></div><div class="pagination"><button id="csv-prev" type="button">上一页</button><span id="csv-page"></span><button id="csv-next" type="button">下一页</button></div>`+explicitConfirmation('csv-confirmed','已逐项核对这批资料的全部记录，确认整批导入。'),async()=>{
      await api('csv_apply',{token:result.token,confirmed:form.elements.namedItem('csv-confirmed').checked});cancelEditor=null;editor.close();toast('CSV 整批导入完成。');await refreshOptions();await renderRoute(false);
    },{label:'确认整批导入',cancel:()=>api('csv_discard',{token:result.token})});
    let page=0;const fields=state.boot.schema[module].fields;
    const update=()=>{
      document.querySelector('#csv-review').innerHTML=table(result.review.rows.slice(page*40,page*40+40).map(row=>({line:row.line,...row.values})),[['line','CSV行'],...fields.map(field=>[field.name,field.label])]);
      document.querySelector('#csv-page').textContent=`${page+1} / ${Math.ceil(result.review.count/40)} 页 · 共 ${result.review.count} 条`;
      document.querySelector('#csv-prev').disabled=page===0;document.querySelector('#csv-next').disabled=(page+1)*40>=result.review.count;
    };
    document.querySelector('#csv-prev').onclick=()=>{page--;update();};document.querySelector('#csv-next').onclick=()=>{page++;update();};update();
  });
}

function installPDFControls() {
  if(state.boot.read_only || !['invoices','input_invoices'].includes(state.collection))return;
  const module=state.collection;
  main.querySelector('.page-head .head-actions').insertAdjacentHTML('afterbegin','<button id="invoice-pdf-import">从文字型 PDF 登记</button>');
  document.querySelector('#invoice-pdf-import').onclick=()=>invoicePDFImport(module);
}

function invoicePDFImport(module) {
  openEditor('发票 PDF · 本机规则提取',notice('沿用原本地文字解析，最多10页。此发票入口暂不支持扫描识别，扫描PDF需要人工登记。上传和预览不入账。')+'<label class="form-field">本机 PDF<input name="pdf" type="file" accept=".pdf" required></label>',async()=>{
    const result=await api('pdf_preview',{module,...await fileData(form.elements.pdf.files[0])});
    const recognized=result.recognition,match=result.match;
    const fields=state.boot.schema[module].fields.filter(field=>!['deduction_status','deduction_period'].includes(field.name));
    const values={...recognized,tax_rate_percent:recognized.tax_rate,project_id:state.project};
    if(module==='input_invoices' && !values.tax_rate_label && recognized.tax_rate!==null)values.tax_rate_label=String(recognized.tax_rate)+'%';
    // Missing extracted values stay blank, including date, amounts and tax rate.
    for(const field of fields)if(!Object.hasOwn(values,field.name))values[field.name]='';
    const summary=notice(`票号 ${recognized.invoice_no || '待补充'} · 日期 ${recognized.invoice_date || '待补充'} · 价税合计 ${recognized.amount || '待补充'} 元 · 未税 ${recognized.net_amount || '待补充'} 元 · 税额 ${recognized.tax_amount || '待补充'} 元`);
    const warnings=recognized.warnings?.length?notice(recognized.warnings.join('；'),'warning'):'';
    openEditor(match?'已匹配原开票记录 · 核对附件':'核对票面与业务归属',summary+warnings+`<details><summary>本地规则识别结果与文件指纹</summary>${evidenceHTML(recognized)}</details>`+(match?notice('已找到相同票号的原发票；只关联其原文件，不新建发票或改变原项目。')+evidenceHTML(match):`<div class="form-grid">${fields.map(field=>fieldHTML(field,values[field.name]??'')).join('')}</div>`)+explicitConfirmation('pdf-confirmed','已核对原 PDF、票号、购销双方、日期、金额和业务归属，确认保存。'),async()=>{
      await api('pdf_apply',{token:result.token,record:match?{}:formValues(fields),confirmed:form.elements.namedItem('pdf-confirmed').checked});cancelEditor=null;editor.close();toast('发票与 PDF 已由原业务服务保存。');await refreshOptions();await renderRoute(false);
    },{label:match?'确认关联原发票 PDF':'确认登记与关联 PDF',cancel:()=>api('pdf_discard',{token:result.token})});
    if(!match && module==='invoices') {
      const project=form.elements.project_id;
      const choices=fields.filter(field=>['contracts','settlements'].includes(field.source))
        .map(field=>[form.elements.namedItem(field.name),field.source]);
      bindEditorChoices([project],choices,()=>api('options?'+query({project_id:project.value})));
    }
  },{label:'提取与预览'});
}
