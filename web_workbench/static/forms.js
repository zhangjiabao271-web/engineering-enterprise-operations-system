'use strict';

let choiceSequence = 0;

function choiceSearchText(value) {
  return String(value).normalize('NFKC').toLocaleLowerCase()
    .replace(/(\d)\s*[x×*✕]\s*(?=\d)/g, '$1×').trim();
}

function enhanceForms(container) {
  for (const select of container.querySelectorAll('select[data-search]')) {
    if (select.dataset.enhanced) continue;
    select.dataset.enhanced = 'true';
    const host = document.createElement('span');
    host.className = 'search-choice';
    select.before(host);
    host.append(select);
    select.classList.add('native-choice');
    const input = document.createElement('input');
    const list = document.createElement('ul');
    const label = select.closest('label')?.querySelector('.field-label')?.textContent?.trim() || select.closest('label')?.childNodes[0]?.textContent?.trim() || '搜索选择';
    list.id = 'choice-' + ++choiceSequence;
    list.className = 'choice-results';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', label + '候选');
    list.hidden = true;
    input.type = 'search';
    input.autocomplete = 'off';
    input.placeholder = '输入关键词搜索';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-label', label);
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', list.id);
    host.append(input, list);
    let candidates = [];
    let active = -1;
    let selectedValue = select.value;
    const required = select.required;
    select.required = false;
    const sync = () => {
      selectedValue = select.value;
      input.value = select.value ? select.selectedOptions[0]?.textContent || '' : '';
      input.disabled = select.disabled;
      input.required = required;
      input.setCustomValidity('');
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
    };
    const hide = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
    const choose = option => {
      select.value = option.value;
      sync();
      hide();
      select.dispatchEvent(new Event('change', {bubbles: true}));
    };
    const show = () => {
      const keywords = choiceSearchText(input.value).split(/\s+/).filter(Boolean);
      candidates = [...select.options].filter(option => !option.disabled && keywords.every(keyword=>choiceSearchText(option.textContent).includes(keyword)));
      active = -1;
      list.innerHTML = candidates.length ? candidates.map((option, index) => `<li role="option" id="${list.id}-${index}" data-choice-index="${index}">${esc(option.textContent)}</li>`).join('') : '<li class="choice-empty">没有匹配项，请换个关键词</li>';
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      list.querySelectorAll('[data-choice-index]').forEach(item => item.onmousedown = event => { event.preventDefault(); choose(candidates[Number(item.dataset.choiceIndex)]); });
    };
    input.onfocus = show;
    input.oninput = () => {
      select.value = '';
      input.setCustomValidity(input.value ? '请从匹配结果中选择，不能仅填写项目名称。' : '');
      show();
    };
    input.onblur = () => {
      hide();
      if (!input.value.trim() && selectedValue !== select.options[0]?.value) {
        choose(select.options[0]);
      }
    };
    input.onkeydown = event => {
      if (event.key === 'Escape') { hide(); event.preventDefault(); event.stopPropagation(); }
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        if (list.hidden) show();
        if (!candidates.length) return;
        active = (active + (event.key === 'ArrowDown' ? 1 : -1) + candidates.length) % candidates.length;
        list.querySelectorAll('[role=option]').forEach((item, index) => item.setAttribute('aria-selected', index === active ? 'true' : 'false'));
        input.setAttribute('aria-activedescendant', `${list.id}-${active}`);
        document.getElementById(`${list.id}-${active}`).scrollIntoView({block: 'nearest'});
      }
      if (event.key === 'Enter' && !list.hidden) {
        event.preventDefault();
        if (active >= 0) choose(candidates[active]);
        else if (candidates.length === 1) choose(candidates[0]);
      }
    };
    select.addEventListener('change', sync);
    select.addEventListener('choice-sync', sync);
    new MutationObserver(sync).observe(select, {childList: true, attributes: true, attributeFilter: ['disabled', 'required']});
    sync();
  }
  for (const picker of container.querySelectorAll('.workday-picker')) {
    const value = picker.querySelector('input');
    const sync = () => picker.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', button.dataset.workday === value.value ? 'true' : 'false'));
    picker.querySelectorAll('button').forEach(button => button.onclick = () => { value.value = button.dataset.workday; sync(); value.dispatchEvent(new Event('change', {bubbles: true})); });
    value.addEventListener('change', sync);
    sync();
  }
}

function replaceLinkedOptions(select, rows, {selected=select.value, disabled=false, preserve=false, placeholder}={}) {
  if(preserve && selected && !rows.some(row=>String(row.value)===String(selected))){
    rows=[...rows,{value:selected,label:select.selectedOptions[0]?.textContent||'原记录（仅保留历史关联）'}];
  }
  select.innerHTML=optionList(rows,selected);
  if(placeholder)select.options[0].textContent=placeholder;
  select.disabled=disabled;
  select.dispatchEvent(new Event('choice-sync'));
}

function watchLinkedChoice(select, changed, pending=changed) {
  if(!select)return;
  select.addEventListener('change',changed);
  select.closest('.search-choice')?.querySelector('input')?.addEventListener('input',pending);
}

function bindOfferSupplierTax(record={}) {
  const supplier=form.elements.namedItem('supplier_id');
  const tax=form.elements.namedItem('tax_rate_percent');
  const lookup=createEditorLookup();
  let automatic=null;
  const hint=document.createElement('small');
  hint.className='form-help';
  hint.textContent='新增报价默认使用供应商档案税率，可按本次报价修改；已有报价保留原税率。';
  tax.closest('.form-field').append(hint);
  const clear=()=>{
    lookup.cancel();
    if(automatic!==null && tax.value===automatic)tax.value='';
    automatic=null;
  };
  const update=()=>{
    clear();
    const owner=supplier.value;
    if(!owner)return;
    return lookup.run(()=>api('record?'+query({module:'suppliers',id:owner})),row=>{
      if(supplier.value!==owner)return;
      tax.value=String(row.default_tax_rate_percent ?? 0);
      automatic=tax.value;
      form.elements.namedItem('confirmed').checked=false;
      tax.dispatchEvent(new Event('change',{bubbles:true}));
    });
  };
  tax.addEventListener('input',()=>{lookup.cancel();automatic=null;});
  watchLinkedChoice(supplier,update,clear);
  if(!record.id && supplier.value && (record.tax_rate_percent==null || record.tax_rate_percent===''))return update();
}

function bindFinancialChoices(options, record={}) {
  const project=form.elements.project_id;
  const contract=form.elements.contract_id;
  const settlement=form.elements.settlement_id;
  const invoice=form.elements.invoice_id;
  const scope=form.elements.allocation_scope;
  const entity=form.elements.entity_id;
  const lookup=createEditorLookup();
  let current=options;
  const cash=()=>current.projects.some(row=>String(row.value)===project.value&&row.business_mode==='cash');
  const projectMode=()=>scope?.value==='project'&&!cash();
  const matches=row=>String(row.project_id)===project.value&&(row.contract_id===null?'':String(row.contract_id))===contract.value;
  const ready=()=>Boolean(project.value&&(cash()||contract.value||!form.elements.invoice_no));
  let suggestedEntity='';
  const visibility=()=>{
    for(const input of [contract,settlement,invoice].filter(Boolean)){
      input.closest('.form-field').hidden=projectMode();
      if(projectMode()){input.value='';input.disabled=true;input.dispatchEvent(new Event('choice-sync'));}
    }
    if(!projectMode()){contract.disabled=cash()||!project.value||Boolean(record.id);contract.dispatchEvent(new Event('choice-sync'));}
    if(scope)scope.closest('.form-field').hidden=cash();
    const advance=form.elements.namedItem('allow_advance');
    if(advance)advance.closest('.form-field').hidden=projectMode();
  };

  const updateInvoices=(preserve=false)=>{
    if(!invoice)return;
    if(projectMode())return visibility();
    let rows=current.invoices.filter(matches);
    if(settlement?.value)rows=rows.filter(row=>row.settlement_ids.map(String).includes(settlement.value));
    replaceLinkedOptions(invoice,rows,{disabled:!ready()||cash(),preserve,placeholder:cash()?'零星工程无需关联发票':undefined});
  };
  const updateSettlements=(preserve=false)=>{
    if(!settlement)return;
    if(projectMode())return visibility();
    const selectedInvoice=invoice&&current.invoices.find(row=>String(row.value)===invoice.value);
    let rows=current.settlements.filter(matches);
    if(selectedInvoice)rows=rows.filter(row=>selectedInvoice.settlement_ids.includes(row.value));
    let selected=settlement.value;
    if(selectedInvoice)selected=rows.length===1?String(rows[0].value):'';
    replaceLinkedOptions(settlement,rows,{selected,disabled:!ready()||Boolean(selectedInvoice),preserve,
      placeholder:selectedInvoice?'收入确认由所选发票确定':undefined});
  };
  const updateChildren=(preserve=false)=>{visibility();updateSettlements(preserve);updateInvoices(preserve);};
  const resetChildren=()=>{
    if(settlement)replaceLinkedOptions(settlement,[],{disabled:true});
    if(invoice)replaceLinkedOptions(invoice,[],{disabled:true});
  };
  const updateProject=()=>{
    lookup.cancel();resetChildren();replaceLinkedOptions(contract,[],{disabled:true});
    if(!project.value)return;
    return lookup.run(()=>api('options?'+query({project_id:project.value})),fresh=>{
      current=fresh;
      replaceLinkedOptions(contract,cash()?[]:current.contracts,{disabled:cash(),
        selected:!projectMode()&&current.contracts.length===1?String(current.contracts[0].value):'',
        placeholder:scope?'合同待补时可选择对应已确认业务':'合同待补，可暂不选择'});
      const owner=current.projects.find(row=>String(row.value)===project.value)?.entity_id;
      if(entity&&(!entity.value||entity.value===suggestedEntity)){
        entity.value=owner?String(owner):'';suggestedEntity=entity.value;
        entity.dispatchEvent(new Event('choice-sync'));
      }
      updateChildren();
    });
  };
  watchLinkedChoice(project,updateProject,()=>{lookup.cancel();resetChildren();replaceLinkedOptions(contract,[],{disabled:true});});
  watchLinkedChoice(contract,()=>{resetChildren();updateChildren();},resetChildren);
  watchLinkedChoice(settlement,()=>updateInvoices());
  watchLinkedChoice(invoice,()=>updateSettlements());
  scope?.addEventListener('change',()=>{resetChildren();contract.value='';contract.dispatchEvent(new Event('choice-sync'));updateChildren();});
  if(entity&& !record.id && !entity.value){
    entity.value=String(current.projects.find(row=>String(row.value)===project.value)?.entity_id||'');
    suggestedEntity=entity.value;entity.dispatchEvent(new Event('choice-sync'));
  }
  if(cash())replaceLinkedOptions(contract,[],{disabled:true});
  else contract.disabled=!project.value;
  updateChildren(Boolean(record.id));
}

function bindReceiptAllocationPreview(record={}) {
  const output=document.createElement('section');
  output.className='receipt-allocation-preview';output.setAttribute('aria-live','polite');
  form.elements.namedItem('amount').closest('.form-field').after(output);
  let timer;
  let generation=0;
  const refresh=()=>{
    const version=++generation;clearTimeout(timer);
    const values=Object.fromEntries(['project_id','contract_id','settlement_id','invoice_id','entity_id','amount','receipt_date','allocation_scope']
      .map(name=>[name,form.elements.namedItem(name)?.value||'']));
    if(values.allocation_scope!=='project'||!values.project_id||!values.amount||!values.receipt_date){output.replaceChildren();return;}
    output.textContent='正在核对本笔回款抵扣顺序…';
    timer=setTimeout(async()=>{
      try{
        const result=await api('receipt_preview',{record:values,id:record.id});
        if(version!==generation||!output.isConnected)return;
        output.innerHTML='<h3>本次回款将抵扣</h3>'+result.rows.map(row=>
          `<p><span>${esc(row.contract_name||'')} ${esc(row.settlement_no)}${row.receivable_date?' · 业务日期 '+esc(row.receivable_date):''}</span><strong>${esc(money(row.amount_minor))}</strong></p>`).join('')+
          '<small>同一项目、同一经营主体内先补旧应收；超出部分保留为预收款。补录更早到账或更早业务时，自动抵扣顺序会相应更新。</small>';
      }catch(error){if(version===generation&&output.isConnected)output.textContent=error.message;}
    },180);
  };
  form.addEventListener('change',refresh);form.addEventListener('input',refresh);refresh();
}

function bindWorkdaySite() {
  const project=form.elements.project_id,site=form.elements.project_site_id,text=form.elements.construction_site;
  const lookup=createEditorLookup();
  let automatic=site.value?text.value:'';
  const clear=()=>{
    lookup.cancel();
    if(automatic&&text.value===automatic)text.value='';
    automatic='';
    replaceLinkedOptions(site,[],{disabled:true});
  };
  text.addEventListener('input',()=>{if(text.value!==automatic)automatic='';});
  site.addEventListener('change',()=>{
    if(site.value){text.value=site.selectedOptions[0].textContent;automatic=text.value;}
    else{if(automatic&&text.value===automatic)text.value='';automatic='';}
  });
  watchLinkedChoice(project,()=>{
    clear();if(!project.value)return;
    return lookup.run(()=>api('options?'+query({project_id:project.value})),fresh=>replaceLinkedOptions(site,fresh.sites));
  },clear);
  if(!project.value)replaceLinkedOptions(site,[],{disabled:true});
}

function bindContractPricing(editing) {
  const type=form.elements.contract_type,mode=form.elements.pricing_mode,amount=form.elements.amount,limit=form.elements.control_limit;
  let userChoice=false;
  const hint=document.createElement('p');hint.className='page-note';
  mode.closest('.form-field').append(hint);
  const update=()=>{
    const actual=mode.value==='actual',fixed=mode.value==='fixed';
    amount.closest('.form-field').hidden=actual;amount.disabled=actual;amount.required=!actual;
    amount.closest('.form-field').querySelector('.field-label').innerHTML=
      (fixed?'含税合同金额（元）':'暂定金额（元）')+'<span class="required">*</span>';
    limit.closest('.form-field').hidden=fixed;limit.disabled=fixed;
    hint.textContent=actual?'按实际结算，不填写预设合同金额。控制上限可留空。':
      fixed?'合同金额作为分配和收入确认上限。':'暂定金额用于参考，控制上限可留空。';
  };
  type.addEventListener('change',()=>{if(!editing&&!userChoice)mode.value=type.value==='annual'?'actual':'fixed';update();});
  mode.addEventListener('change',()=>{userChoice=true;update();});
  update();
}

function collectPurchaseItems() {
  return [...editor.querySelectorAll('.purchase-item')].map(section => {
    const item = {item_id: section.dataset.itemId || undefined};
    section.querySelectorAll('input[name],select[name]').forEach(input => { item[input.name.slice(5)] = input.value; });
    return item;
  });
}

function initializePurchasePreview() {
  const container = editor.querySelector('#purchase-items');
  const total = editor.querySelector('#purchase-preview');
  const allocation = editor.querySelector('#purchase-allocation-preview');
  let version = 0;
  let timer;
  const update = () => {
    const current = ++version;
    clearTimeout(timer);
    total.textContent = '正在核对金额…';
    if (allocation) allocation.textContent = form.elements.allocation_method.value === 'equal' ? '正在核对项目份额…' : '';
    for (const output of container.querySelectorAll('.item-preview')) output.textContent = '正在核对…';
    timer = setTimeout(async () => {
      try {
        const result = await api('purchase_preview', {items: collectPurchaseItems(), freight: form.elements.freight_amount.value || '0', ...collectPurchaseAllocation()});
        if (current !== version || !total.isConnected) return;
        result.items.forEach((item, index) => {
          container.children[index].querySelector('.item-preview').textContent = `本条材料 ${money(item.line_amount_cents)} · 其中税额 ${money(item.tax_amount_cents)}`;
        });
        total.textContent = `材料 ${money(result.material_amount_cents)} + 运费 ${money(result.freight_amount_cents)} = 整单 ${money(result.total_amount_cents)}`;
        renderPurchaseAllocationPreview(result);
      } catch (error) {
        if (current !== version || !total.isConnected) return;
        total.textContent = '请完善数量、单价和税率后核对整单金额：' + error.message;
        if (allocation) allocation.textContent = form.elements.allocation_method.value === 'equal' ? error.message : '';
      }
    }, 180);
  };
  container.addEventListener('input', update);
  container.addEventListener('change', update);
  new MutationObserver(update).observe(container, {childList: true});
  form.elements.freight_amount.addEventListener('input', update);
  form.elements.allocation_method?.addEventListener('change', update);
  editor.querySelector('#purchase-allocation')?.addEventListener('change', update);
  update();
}
