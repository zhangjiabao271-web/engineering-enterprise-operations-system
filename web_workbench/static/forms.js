'use strict';

let choiceSequence = 0;

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
      const keyword = input.value.toLocaleLowerCase().trim();
      candidates = [...select.options].filter(option => !option.disabled && (!keyword || option.textContent.toLocaleLowerCase().includes(keyword)));
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
  let version = 0;
  let timer;
  const update = () => {
    const current = ++version;
    clearTimeout(timer);
    total.textContent = '正在核对金额…';
    for (const output of container.querySelectorAll('.item-preview')) output.textContent = '正在核对…';
    timer = setTimeout(async () => {
      try {
        const result = await api('purchase_preview', {items: collectPurchaseItems(), freight: form.elements.freight_amount.value || '0'});
        if (current !== version || !total.isConnected) return;
        result.items.forEach((item, index) => {
          container.children[index].querySelector('.item-preview').textContent = `本条材料 ${money(item.line_amount_cents)} · 其中税额 ${money(item.tax_amount_cents)}`;
        });
        total.textContent = `材料 ${money(result.material_amount_cents)} + 运费 ${money(result.freight_amount_cents)} = 整单 ${money(result.total_amount_cents)}`;
      } catch (error) {
        if (current !== version || !total.isConnected) return;
        total.textContent = '请完善数量、单价和税率后核对整单金额：' + error.message;
      }
    }, 180);
  };
  container.addEventListener('input', update);
  container.addEventListener('change', update);
  new MutationObserver(update).observe(container, {childList: true});
  form.elements.freight_amount.addEventListener('input', update);
  update();
}
