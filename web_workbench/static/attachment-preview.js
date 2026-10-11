'use strict';

function attachmentPreviewButton(row) {
  if (!row.file_exists || !row.preview_kind) return '';
  return `<button type="button" data-attachment-preview="${Number(row.id)}" data-preview-kind="${esc(row.preview_kind)}" data-preview-name="${esc(row.original_name)}" data-preview-history="${row.status === 'void' ? '1' : ''}">预览附件</button>`;
}

(() => {
  const dialog = document.querySelector('#attachment-preview');
  const body = document.querySelector('#attachment-preview-body');
  const status = document.querySelector('#attachment-preview-status');
  const tools = document.querySelector('#attachment-image-tools');
  const zoomLabel = document.querySelector('#attachment-zoom-label');
  const fitButton = document.querySelector('#attachment-fit');
  let generation = 0;
  let image = null;
  let zoom = null;
  let pdf = null;
  const pageTools = document.querySelector('#attachment-page-tools');
  const pageLabel = document.querySelector('#attachment-page-label');
  const maxRenderedPages = 6;
  let scrollFrame = 0;

  function releasePage(entry) {
    entry.media.hidden = true;
    entry.media.removeAttribute('src');
    if (entry.url) URL.revokeObjectURL(entry.url);
    entry.url = null;
    entry.state = 'idle';
    entry.message.textContent = '滚动到这里时加载';
    entry.placeholder.hidden = false;
    entry.retry.hidden = true;
  }

  function clearPreview() {
    generation++;
    if (pdf) {
      pdf.observer.disconnect();
      pdf.controller?.abort();
      pdf.entries.forEach(releasePage);
    }
    if (image) {
      image.onload = null;
      image.onerror = null;
      image.removeAttribute('src');
    }
    cancelAnimationFrame(scrollFrame);
    scrollFrame = 0;
    image = null;
    pdf = null;
    zoom = null;
    body.replaceChildren();
    body.scrollTo(0, 0);
  }

  function closePreview() {
    clearPreview();
    dialog.close();
  }

  function fitWidth() {
    const style = getComputedStyle(body);
    return Math.min(960, body.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
  }

  function sizePdf() {
    if (!pdf) return;
    const anchor = pdf.entries[pdf.page - 1].figure;
    const top = body.getBoundingClientRect().top;
    const before = anchor.getBoundingClientRect();
    const fraction = (top - before.top) / before.height;
    pdf.document.style.width = zoom === null ? '' : `${fitWidth() * zoom}px`;
    const after = anchor.getBoundingClientRect();
    body.scrollTop += after.top - top + fraction * after.height;
  }

  function setZoom(value) {
    if (pdf) {
      zoom = value;
      sizePdf();
      zoomLabel.textContent = value === null ? '适应宽度' : `${Math.round(value * 100)}%`;
      return;
    }
    if (!image || !image.naturalWidth) return;
    zoom = value;
    image.classList.toggle('is-zoomed', value !== null);
    image.style.width = value === null ? '' : `${image.naturalWidth * value}px`;
    zoomLabel.textContent = value === null ? '适应窗口' : `${Math.round(value * 100)}%`;
  }

  function changeZoom(factor) {
    if (pdf) {
      setZoom(Math.min(4, Math.max(.25, (zoom ?? 1) * factor)));
      return;
    }
    if (!image || !image.naturalWidth) return;
    const current = zoom ?? image.clientWidth / image.naturalWidth;
    setZoom(Math.min(4, Math.max(.1, current * factor)));
  }

  function loadImage(source, name, current, message) {
    image = null;
    zoom = null;
    body.replaceChildren();
    tools.querySelectorAll('button').forEach(control => control.disabled = true);
    zoomLabel.textContent = '适应窗口';
    const media = document.createElement('img');
    media.className = 'attachment-preview-image';
    media.alt = name;
    media.onload = () => {
      if (current !== generation || !dialog.open) return;
      image = media;
      status.textContent = message;
      tools.querySelectorAll('button').forEach(control => control.disabled = false);
    };
    media.onerror = () => {
      if (current !== generation) return;
      status.textContent = '预览未能读取，请核对文件，或下载原附件查看。';
    };
    media.src = source;
    body.append(media);
    body.scrollTo(0, 0);
  }

  function updatePageLabel() {
    if (!pdf || !dialog.open) return;
    const line = body.getBoundingClientRect().top + body.clientHeight * .3;
    let low = 0;
    let high = pdf.entries.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (pdf.entries[middle].figure.getBoundingClientRect().top <= line) low = middle;
      else high = middle - 1;
    }
    pdf.page = low + 1;
    pageLabel.textContent = `第 ${pdf.page} / ${pdf.entries.length} 页 · 向下滚动阅读`;
  }

  function nearbyPages(view) {
    return view.entries.filter(entry => entry.near)
      .sort((a, b) => Math.abs(a.number - view.page) - Math.abs(b.number - view.page)).slice(0, maxRenderedPages);
  }

  function refreshPages(view) {
    if (pdf !== view) return;
    const wanted = new Set(nearbyPages(view));
    view.entries.forEach(entry => {
      if (entry.state === 'ready' && !wanted.has(entry)) releasePage(entry);
    });
    loadNextPage(view);
  }

  async function loadNextPage(view) {
    if (pdf !== view || view.loading) return;
    const entry = nearbyPages(view).find(item => item.state === 'idle');
    if (!entry) return;
    view.loading = true;
    entry.state = 'loading';
    entry.message.textContent = `正在读取第 ${entry.number} 页…`;
    view.controller = new AbortController();
    try {
      const response = await fetch('/api/attachment_pdf_page?' + query({...view.params, page: entry.number}),
        {signal: view.controller.signal, cache: 'no-store'});
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || '此页未能读取，请重试或下载原附件查看。');
      }
      const blob = await response.blob();
      if (pdf !== view || !dialog.open) return;
      entry.url = URL.createObjectURL(blob);
      entry.media.src = entry.url;
      await entry.media.decode();
      if (pdf !== view || !dialog.open) return;
      if (nearbyPages(view).includes(entry)) {
        entry.state = 'ready';
        entry.media.hidden = false;
        entry.placeholder.hidden = true;
      } else releasePage(entry);
    } catch (error) {
      if (pdf !== view || error.name === 'AbortError') return;
      releasePage(entry);
      entry.state = 'error';
      entry.message.textContent = error.message;
      entry.retry.hidden = false;
    } finally {
      view.loading = false;
      view.controller = null;
      if (pdf === view) loadNextPage(view);
    }
  }

  function showPdf(info, params, name) {
    const pages = Number(info.pages);
    if (!Number.isInteger(pages) || pages < 1 || info.page_sizes?.length !== pages) {
      throw new Error('PDF页面资料未能读取，请重启工作台后再试。');
    }
    const documentView = document.createElement('div');
    documentView.className = 'attachment-pdf-document';
    const entries = info.page_sizes.map((size, index) => {
      const figure = document.createElement('figure');
      figure.className = 'attachment-pdf-page';
      figure.dataset.page = index + 1;
      figure.setAttribute('aria-label', `第 ${index + 1} 页`);
      const caption = document.createElement('figcaption');
      caption.textContent = `第 ${index + 1} 页`;
      const sheet = document.createElement('div');
      sheet.className = 'attachment-pdf-sheet';
      sheet.style.aspectRatio = `${size.width} / ${size.height}`;
      const media = document.createElement('img');
      media.alt = `${name} · 第${index + 1}页`;
      media.hidden = true;
      const placeholder = document.createElement('div');
      placeholder.className = 'attachment-pdf-placeholder';
      const message = document.createElement('span');
      message.textContent = '滚动到这里时加载';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.textContent = '重新加载此页';
      retry.hidden = true;
      const entry = {number: index + 1, figure, media, placeholder, message, retry,
        near: false, state: 'idle', url: null};
      retry.onclick = () => { releasePage(entry); refreshPages(view); };
      placeholder.append(message, retry);
      sheet.append(media, placeholder);
      figure.append(caption, sheet);
      documentView.append(figure);
      return entry;
    });
    const view = {params, entries, document: documentView, page: 1, loading: false, controller: null};
    const byFigure = new Map(entries.map(entry => [entry.figure, entry]));
    view.observer = new IntersectionObserver(changes => {
      if (pdf !== view) return;
      for (const change of changes) {
        const entry = byFigure.get(change.target);
        entry.near = change.isIntersecting;
      }
      updatePageLabel();
      refreshPages(view);
    }, {root: body, rootMargin: '100% 0px'});
    pdf = view;
    body.replaceChildren(documentView);
    entries.forEach(entry => view.observer.observe(entry.figure));
    tools.querySelectorAll('button').forEach(control => control.disabled = false);
    zoomLabel.textContent = '适应宽度';
    status.textContent = '鼠标滚轮或触屏连续阅读；附近页面按需加载，原文件不变。';
    updatePageLabel();
  }

  async function openPreview(button) {
    clearPreview();
    const current = generation;
    const name = button.dataset.previewName;
    const isImage = button.dataset.previewKind === 'image';
    const params = {id: Number(button.dataset.attachmentPreview), history: button.dataset.previewHistory};
    const source = '/api/attachment?' + query({...params, view: '1'});
    document.querySelector('#attachment-preview-title').textContent = name;
    document.querySelector('#attachment-preview-download').href = '/api/attachment?' + query(params);
    document.querySelector('#attachment-preview-open').href = source;
    status.textContent = '正在读取本机附件…';
    tools.hidden = false;
    pageTools.hidden = isImage;
    fitButton.textContent = isImage ? '适应窗口' : '适应宽度';
    tools.querySelectorAll('button').forEach(control => control.disabled = true);
    zoomLabel.textContent = '适应窗口';
    pageLabel.textContent = '正在读取页数…';
    if (!dialog.open) dialog.showModal();
    if (isImage) {
      loadImage(source, name, current, '可放大查看，原文件不变。');
      return;
    }
    try {
      const info = await api('attachment_pdf_info?' + query(params));
      if (current !== generation || !dialog.open) return;
      showPdf(info, params, name);
    } catch (error) {
      if (current === generation) status.textContent = error.message;
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-attachment-preview]');
    if (button) safe(() => openPreview(button));
  });
  document.querySelector('#attachment-preview-close').onclick = closePreview;
  document.querySelector('#attachment-preview-done').onclick = closePreview;
  fitButton.onclick = () => setZoom(null);
  document.querySelector('#attachment-zoom-in').onclick = () => changeZoom(1.25);
  document.querySelector('#attachment-zoom-out').onclick = () => changeZoom(.8);
  body.addEventListener('scroll', () => {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      updatePageLabel();
      if (pdf) refreshPages(pdf);
    });
  }, {passive: true});
  new ResizeObserver(() => {
    sizePdf();
    updatePageLabel();
    if (pdf) refreshPages(pdf);
  }).observe(body);
  dialog.addEventListener('cancel', event => { event.preventDefault(); closePreview(); });
})();
