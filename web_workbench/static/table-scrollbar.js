'use strict';

// A single native scrollbar follows the visible long table, outside animated layers.
const WorkbenchTableScrollbar = (() => {
  let scrollbar;
  let filler;
  let target = null;
  let frame = 0;
  let sequence = 0;

  function schedule() {
    if (!frame) frame = requestAnimationFrame(update);
  }

  function hide() {
    scrollbar.hidden = true;
    target = null;
  }

  function update() {
    frame = 0;
    if (editor.open || document.body.classList.contains('nav-open')
        || main.getAttribute('aria-busy') === 'true') return hide();
    const viewport = window.visualViewport;
    const bottom = (viewport?.offsetTop || 0) + (viewport?.height || window.innerHeight) - 10;
    const top = document.querySelector('.topbar').getBoundingClientRect().bottom;
    let selected = null;
    let visibleHeight = 0;
    for (const element of main.querySelectorAll('.table-wrap')) {
      if (element.scrollWidth <= element.clientWidth + 1) continue;
      const rect = element.getBoundingClientRect();
      const height = Math.min(rect.bottom, bottom) - Math.max(rect.top, top);
      // The original scrollbar takes over once the table's bottom is visible.
      if (rect.bottom <= bottom || height < 48 || height <= visibleHeight) continue;
      visibleHeight = height;
      selected = {element, rect};
    }
    if (!selected) return hide();
    const {element, rect} = selected;
    const left = Math.max(10, rect.left + element.clientLeft);
    const right = Math.min(window.innerWidth - 10, rect.left + element.clientLeft + element.clientWidth);
    if (right - left < 80) return hide();
    target = element;
    if (!element.id) element.id = 'table-scroll-target-' + ++sequence;
    scrollbar.setAttribute('aria-controls', element.id);
    scrollbar.style.left = left + 'px';
    scrollbar.style.width = (right - left) + 'px';
    scrollbar.style.bottom = (window.innerHeight - bottom) + 'px';
    scrollbar.hidden = false;
    // Match scroll ranges even when clipping or a vertical gutter changes widths.
    filler.style.width = (scrollbar.clientWidth + element.scrollWidth - element.clientWidth) + 'px';
    if (Math.abs(scrollbar.scrollLeft - element.scrollLeft) > 1) scrollbar.scrollLeft = element.scrollLeft;
  }

  function init() {
    scrollbar = document.createElement('div');
    scrollbar.id = 'floating-table-scrollbar';
    scrollbar.className = 'floating-table-scrollbar';
    scrollbar.hidden = true;
    scrollbar.tabIndex = 0;
    scrollbar.setAttribute('role', 'region');
    scrollbar.setAttribute('aria-label', '表格横向滚动条，左右拖动查看其他列');
    scrollbar.title = '左右拖动查看表格其他列';
    filler = document.createElement('div');
    filler.className = 'floating-table-scrollbar-content';
    scrollbar.append(filler);
    document.body.append(scrollbar);
    scrollbar.addEventListener('scroll', () => {
      if (target?.isConnected && !scrollbar.hidden
          && Math.abs(target.scrollLeft - scrollbar.scrollLeft) > 1) {
        target.scrollLeft = scrollbar.scrollLeft;
      }
    }, {passive: true});
    scrollbar.addEventListener('keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const steps = {ArrowLeft: -80, ArrowRight: 80,
        PageUp: -scrollbar.clientWidth, PageDown: scrollbar.clientWidth};
      if (event.key === 'Home') scrollbar.scrollLeft = 0;
      else if (event.key === 'End') scrollbar.scrollLeft = scrollbar.scrollWidth;
      else if (event.key in steps) scrollbar.scrollLeft += steps[event.key];
      else return;
      event.preventDefault();
    });
    const resize = new ResizeObserver(schedule);
    function observeTables() {
      resize.disconnect();
      resize.observe(main);
      main.querySelectorAll('.table-wrap, .table-wrap table').forEach(element => resize.observe(element));
      schedule();
    }
    new MutationObserver(observeTables).observe(main, {childList: true, subtree: true});
    new MutationObserver(schedule).observe(main, {attributes: true, attributeFilter: ['aria-busy']});
    new MutationObserver(schedule).observe(editor, {attributes: true, attributeFilter: ['open']});
    new MutationObserver(schedule).observe(document.body, {attributes: true, attributeFilter: ['class']});
    document.addEventListener('scroll', schedule, {capture: true, passive: true});
    window.addEventListener('resize', schedule, {passive: true});
    window.visualViewport?.addEventListener('resize', schedule, {passive: true});
    window.visualViewport?.addEventListener('scroll', schedule, {passive: true});
    observeTables();
  }

  return {init};
})();
