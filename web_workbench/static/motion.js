'use strict';

// Motion owns only presentation. It never schedules or modifies business requests.
window.WorkbenchMotion = (() => {
  const animations = new Map();
  const systemMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let keyboard = false;
  let reduced = false;
  let indicator;
  let routeContent;
  const preferences = {reduced: false, solid: false};

  function readPreference(name) {
    try { return localStorage.getItem('workbench-ui-' + name) === '1'; }
    catch { return false; }
  }

  function stop(element) {
    animations.get(element)?.cancel();
    animations.delete(element);
  }

  function animate(element, frames, duration = 170) {
    stop(element);
    if (!element || reduced || keyboard || !element.animate) return Promise.resolve(true);
    const animation = element.animate(frames, {duration, easing: 'cubic-bezier(.22,1,.36,1)'});
    animations.set(element, animation);
    return animation.finished.then(() => true, () => false).finally(() => {
      if (animations.get(element) === animation) animations.delete(element);
    });
  }

  function applyPreferences() {
    reduced = preferences.reduced || systemMotion.matches;
    document.body.classList.toggle('reduced', reduced);
    document.body.classList.toggle('solid-glass', preferences.solid);
    document.querySelector('#reduced-motion').checked = reduced;
    document.querySelector('#reduced-motion').disabled = systemMotion.matches;
    document.querySelector('#motion-note').hidden = !systemMotion.matches;
    document.querySelector('#solid-glass').checked = preferences.solid;
    if (reduced) for (const element of [...animations.keys()]) stop(element);
    updateNavigation();
  }

  function updateNavigation() {
    const nav = document.querySelector('#navigation');
    const active = nav.querySelector('.nav-link.active');
    nav.querySelectorAll('.nav-link').forEach(link => {
      if (link === active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    if (!indicator || !active) return;
    const previous = getComputedStyle(indicator).transform;
    const bounds = active.getBoundingClientRect();
    const top = bounds.top - nav.getBoundingClientRect().top + nav.scrollTop;
    const transform = `translateY(${top}px)`;
    const visible = !indicator.hidden;
    indicator.hidden = false;
    indicator.style.height = bounds.height + 'px';
    indicator.style.transform = transform;
    if (visible && previous !== 'none') animate(indicator, [{transform: previous}, {transform}], 180);
  }

  function init() {
    preferences.reduced = readPreference('reduced');
    preferences.solid = readPreference('solid');
    for (const [name, id] of [['reduced', 'reduced-motion'], ['solid', 'solid-glass']]) {
      document.querySelector('#' + id).addEventListener('change', event => {
        preferences[name] = event.target.checked;
        try { localStorage.setItem('workbench-ui-' + name, preferences[name] ? '1' : '0'); }
        catch { /* Preferences still work when browser storage is disabled. */ }
        window.pywebview?.api?.save_ui_preferences(preferences.reduced, preferences.solid).catch(() => {});
        applyPreferences();
      });
    }
    systemMotion.addEventListener('change', applyPreferences);
    document.addEventListener('keydown', () => { keyboard = true; }, true);
    document.addEventListener('pointerdown', () => { keyboard = false; }, true);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) for (const element of [...animations.keys()]) stop(element);
    });
    const nav = document.querySelector('#navigation');
    indicator = document.createElement('span');
    indicator.className = 'nav-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    indicator.hidden = true;
    nav.append(indicator);
    new ResizeObserver(updateNavigation).observe(nav);
    applyPreferences();
    const restoreNativePreferences = async () => {
      try {
        const saved = await window.pywebview.api.get_ui_preferences();
        for (const name of ['reduced', 'solid']) if (typeof saved[name] === 'boolean') preferences[name] = saved[name];
        applyPreferences();
      } catch { /* Retain the browser fallback if the native bridge is unavailable. */ }
    };
    if (window.pywebview?.api) restoreNativePreferences();
    else window.addEventListener('pywebviewready', restoreNativePreferences, {once: true});
  }

  return {
    init,
    updateNavigation,
    beginRoute(element) {
      stop(routeContent);
      routeContent = null;
      element.setAttribute('aria-busy', 'true');
      updateNavigation();
    },
    finishRoute(element) {
      element.setAttribute('aria-busy', 'false');
      // Reusing the animated main layer can leave stale hit regions in Edge/WebView2.
      // Move the current nodes (not their HTML) so existing event listeners survive.
      if (routeContent?.parentElement !== element) {
        routeContent = document.createElement('div');
        routeContent.className = 'route-content';
        routeContent.append(...element.childNodes);
        element.append(routeContent);
      }
      animate(routeContent, [{opacity: 0, transform: 'translateY(6px)'}, {opacity: 1, transform: 'translateY(0)'}]);
    },
    openDialog(element) {
      animate(element, [{opacity: 0, transform: 'translateY(8px) scale(.99)'}, {opacity: 1, transform: 'translateY(0) scale(1)'}], 180);
    },
    async closeDialog(element) {
      const finished = await animate(element, [{opacity: 1, transform: 'translateY(0)'}, {opacity: 0, transform: 'translateY(4px)'}], 100);
      if (finished) element.close();
    },
    showToast(element) {
      animate(element, [{opacity: 0, transform: 'translate(-50%, 5px)'}, {opacity: 1, transform: 'translate(-50%, 0)'}], 130);
    },
    get reduced() { return reduced; },
    get activeCount() { return animations.size; },
  };
})();
