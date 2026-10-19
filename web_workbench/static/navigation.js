'use strict';

// Preserve bookmarks and contextual links after consolidating tool pages.
function canonicalWorkbenchHash(hash) {
  const [route, search='']=(hash || '#home').replace(/^#/, '').split('?');
  const aliases={profit:['projects','profit'],compare:['offers','compare'],cashflow:['funds','cashflow']};
  if (!aliases[route]) return hash || '#home';
  const [parent, tab]=aliases[route];
  const params=new URLSearchParams(search);
  params.set('tab',tab);
  return '#'+parent+'?'+params.toString();
}

function workbenchRecordScope(module) {
  const search=new URLSearchParams(location.hash.split('?')[1] || '');
  const params={}, notes=[];
  if (['purchases','offers','input_invoices'].includes(module) && search.has('supplier')) {
    params.supplier_id=search.get('supplier');
    const supplier=(state.boot.options.suppliers || []).find(row=>String(row.value)===params.supplier_id);
    notes.push('供应商：'+(supplier?.label || '指定供应商'));
  }
  if (module==='projects' && search.has('customer')) {
    params.customer_id=search.get('customer');
    const customer=(state.boot.options.customers || []).find(row=>String(row.value)===params.customer_id);
    notes.push('客户关联项目：'+(customer?.label || '指定客户')+'；以下为原项目全周期金额，不是客户年度发生额');
  }
  if (['purchases','input_invoices'].includes(module) && search.has('year')) {
    params.year=search.get('year');
    notes.push('业务年度：'+params.year);
  }
  const html=notes.length?`<p class="page-note">${esc(notes.join(' · '))} · <a href="#${state.route}">查看全部</a></p>`:'';
  return {params,html};
}

// Browser-history entries retain only view state, never ledger data or form inputs.
const WorkbenchNavigation = (() => {
  const entries = [];
  let index = -1;
  let renderedEntry = null;
  const copyPreferences=value=>Object.fromEntries(Object.entries(value || {}).map(([key,item])=>[key,{...item}]));

  function position() {
    return {top: window.scrollY, left: window.scrollX,
      tables: [...main.querySelectorAll('.table-wrap')].map(element =>
        ({left: element.scrollLeft, top: element.scrollTop}))};
  }

  function view() {
    return {project: state.project, collection: state.collection, page: state.page,
      keyword: state.keyword || '', costMonth: state.costMonth || 'all', costPage: state.costPage || 0,
      projectLedgerTab: state.projectLedgerTab || 'active',
      partnerOverview: copyPreferences(state.partnerOverview),
      contractYear: state.contractYear || '', contractStatus: state.contractStatus || '',
      contractEntity: state.contractEntity || '',
      contractBusinessScope: state.contractBusinessScope || 'all',
      selectedAnalysis: state.selectedAnalysis,
      scope: {...state.scope}};
  }

  function title(entry) {
    const hash=canonicalWorkbenchHash(entry?.hash || '#home');
    const route = hash.slice(1).split('?')[0];
    return state.boot.navigation.flatMap(([, links]) => links)
      .find(([key]) => key === route)?.[1] || '经营驾驶舱';
  }

  function updateButton() {
    const button = document.querySelector('#navigation-back');
    button.hidden = index === 0 && (!location.hash || location.hash === '#home');
    const label = '返回' + title(entries[index - 1]);
    button.title = label;
    button.setAttribute('aria-label', label);
  }

  function savePosition() {
    if (renderedEntry && renderedEntry.hash === location.hash
        && main.getAttribute('aria-busy') !== 'true') {
      renderedEntry.position = position();
    }
  }

  function prepare(reset) {
    savePosition();
    const key = history.state?.workbenchView;
    const found = entries.findIndex(entry => entry.key === key && entry.hash === location.hash);
    let restore = null;
    if (found < 0) {
      entries.splice(index + 1);
      const entry = {key: crypto.randomUUID(), hash: location.hash, view: null,
        position: {top: 0, left: 0, tables: []}};
      entries.push(entry);
      index = entries.length - 1;
      history.replaceState({...history.state, workbenchView: entry.key}, '');
      restore = entry.position;
    } else {
      if (found !== index && reset) restore = entries[found].position;
      index = found;
    }
    const saved = restore && entries[index].view;
    if (saved) {
      Object.assign(state, saved, {scope: {...saved.scope},partnerOverview:copyPreferences(saved.partnerOverview)});
      const project = document.querySelector('#project-scope');
      project.value = state.project;
      project.dispatchEvent(new Event('choice-sync'));
    }
    updateButton();
    return {restored: !!saved, position: restore};
  }

  function finish(context) {
    const entry = entries[index];
    entry.view = view();
    renderedEntry = entry;
    if (!context.position) {
      entry.position = position();
      return;
    }
    requestAnimationFrame(() => {
      if (renderedEntry !== entry || main.getAttribute('aria-busy') === 'true') return;
      main.querySelectorAll('.table-wrap').forEach((element, tableIndex) => {
        const saved = context.position.tables[tableIndex];
        if (saved) {element.scrollLeft = saved.left; element.scrollTop = saved.top;}
      });
      window.scrollTo({top: context.position.top, left: context.position.left, behavior: 'instant'});
      entry.position = position();
    });
  }

  function back() {
    if (editor.open) return;
    if (index > 0) history.back();
    else location.hash = 'home';
  }

  function backTo(hash) {
    if (editor.open) return;
    const target = canonicalWorkbenchHash(hash.startsWith('#') ? hash : '#'+hash);
    for (let previous=index-1; previous>=0; previous--) {
      if (entries[previous].hash===target) return history.go(previous-index);
    }
    location.hash=target;
  }

  function init() {
    // The application restores both window and table positions after rendering.
    // Native restoration runs before hashchange and would overwrite that snapshot.
    history.scrollRestoration = 'manual';
    document.addEventListener('scroll', savePosition, {capture: true, passive: true});
    document.addEventListener('click', savePosition, {capture: true});
    document.querySelector('#navigation-back').addEventListener('click', back);
  }

  return {init, prepare, finish, back, backTo};
})();
