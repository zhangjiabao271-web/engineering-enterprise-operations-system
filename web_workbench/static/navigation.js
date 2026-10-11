'use strict';

// Browser-history entries retain only view state, never ledger data or form inputs.
const WorkbenchNavigation = (() => {
  const entries = [];
  let index = -1;
  let renderedEntry = null;

  function position() {
    return {top: window.scrollY, left: window.scrollX,
      tables: [...main.querySelectorAll('.table-wrap')].map(element =>
        ({left: element.scrollLeft, top: element.scrollTop}))};
  }

  function view() {
    return {project: state.project, collection: state.collection, page: state.page,
      keyword: state.keyword || '', costMonth: state.costMonth || 'all', costPage: state.costPage || 0,
      projectLedgerTab: state.projectLedgerTab || 'active',
      selectedAnalysis: state.selectedAnalysis,
      scope: {...state.scope}};
  }

  function title(entry) {
    const route = entry?.hash.slice(1).split('?')[0] || 'home';
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
      Object.assign(state, saved, {scope: {...saved.scope}});
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

  function init() {
    // The application restores both window and table positions after rendering.
    // Native restoration runs before hashchange and would overwrite that snapshot.
    history.scrollRestoration = 'manual';
    document.addEventListener('scroll', savePosition, {capture: true, passive: true});
    document.addEventListener('click', savePosition, {capture: true});
    document.querySelector('#navigation-back').addEventListener('click', back);
  }

  return {init, prepare, finish};
})();
