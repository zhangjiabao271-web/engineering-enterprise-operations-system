// Deterministic preview lifecycle checks, without a browser or business ledger.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const elements = new Map();
const observers = [];
const urls = new Set();
const requests = [];
const documentListeners = new Map();
const frames = new Map();
let frameId = 0;
let pendingOpen;
let deferPages = false;
let failPage = 0;
let metadata;

class Element {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.classList = {toggle() {}};
    this.clientWidth = 800;
    this.clientHeight = 600;
    this.scrollTop = 0;
    this.naturalWidth = 1000;
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
  querySelectorAll() {return this.children;}
  setAttribute(name, value) {this.attributes[name] = value;}
  removeAttribute(name) {delete this.attributes[name]; if (name === 'src') this.src = '';}
  addEventListener(name, callback) {this.listeners.set(name, callback);}
  scrollTo(_x, y) {this.scrollTop = y;}
  showModal() {this.open = true;}
  close() {this.open = false;}
  async decode() {}
  getBoundingClientRect() {
    if (this.dataset.page) {
      const width = parseFloat(body.children[0]?.style.width) || 800;
      const height = 1000 * width / 800;
      return {top: 16 + (Number(this.dataset.page) - 1) * height - body.scrollTop, height};
    }
    return {top: 0, height: 600};
  }
}

const names = ['attachment-preview', 'attachment-preview-body', 'attachment-preview-status',
  'attachment-image-tools', 'attachment-zoom-label', 'attachment-fit', 'attachment-page-tools',
  'attachment-page-label', 'attachment-preview-title', 'attachment-preview-download',
  'attachment-preview-open', 'attachment-preview-close', 'attachment-preview-done',
  'attachment-zoom-in', 'attachment-zoom-out'];
names.forEach(name => elements.set(`#${name}`, new Element()));
const body = elements.get('#attachment-preview-body');
const dialog = elements.get('#attachment-preview');
const tools = elements.get('#attachment-image-tools');
tools.children = [elements.get('#attachment-fit'), elements.get('#attachment-zoom-in'), elements.get('#attachment-zoom-out')];
const context = vm.createContext({
  document: {
    querySelector: selector => elements.get(selector),
    createElement: tag => new Element(tag),
    addEventListener: (name, callback) => documentListeners.set(name, callback),
  },
  IntersectionObserver: class {
    constructor(callback) {this.callback = callback; this.targets = []; observers.push(this);}
    observe(element) {this.targets.push(element);}
    disconnect() {this.disconnected = true;}
  },
  ResizeObserver: class {observe() {}},
  AbortController, Blob,
  URL: {createObjectURL() {const url = `blob:test-${urls.size}-${requests.length}`; urls.add(url); return url;},
    revokeObjectURL: url => urls.delete(url)},
  getComputedStyle: () => ({paddingLeft: '16', paddingRight: '16'}),
  requestAnimationFrame(callback) {frames.set(++frameId, callback); return frameId;},
  cancelAnimationFrame: id => frames.delete(id),
  query: values => new URLSearchParams(values).toString(),
  esc: value => String(value),
  safe: callback => {pendingOpen = callback();},
  api: async () => metadata,
  fetch: (url, options) => {
    const number = Number(new URL(url, 'http://localhost').searchParams.get('page'));
    let resolve;
    const request = {number, signal: options.signal};
    requests.push(request);
    const response = {ok: number !== failPage, json: async () => ({error: 'Synthetic page failure'}),
      blob: async () => new Blob(['synthetic page'])};
    if (!deferPages) return Promise.resolve(response);
    const promise = new Promise(done => {resolve = done;});
    request.finish = () => resolve(response);
    options.signal.addEventListener('abort', () => request.aborted = true);
    return promise;
  },
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/attachment-preview.js', import.meta.url), 'utf8'), context);
const flush = async () => {for (let count = 0; count < 30; count++) await Promise.resolve();};
const click = id => elements.get(`#${id}`).onclick();
const open = async (kind = 'pdf', id = 1) => {
  documentListeners.get('click')({target: {closest: () => ({dataset: {
    previewName: 'Synthetic.pdf', previewKind: kind, attachmentPreview: String(id), previewHistory: '',
  }})}});
  await pendingOpen;
};
const intersect = visible => {
  const observer = observers.at(-1);
  observer.callback(observer.targets.map((target, index) => ({target, isIntersecting: visible.includes(index + 1)})));
};
const sheets = () => body.children[0].children;
const media = index => sheets()[index].children[1].children[0];
const placeholder = index => sheets()[index].children[1].children[1];
metadata = {pages: 12, page_sizes: Array.from({length: 12}, () => ({width: 595, height: 842}))};

await open();
assert.equal(sheets().length, 12);
assert.equal(requests.length, 0, 'offscreen placeholders must not render eagerly');
intersect([1, 2, 3]);
await flush();
assert.deepEqual(requests.map(request => request.number), [1, 2, 3]);
assert.equal(urls.size, 3);
assert.equal(placeholder(0).hidden, true);
assert.equal(media(0).hidden, false);

body.scrollTop = 5000;
body.listeners.get('scroll')();
for (const callback of frames.values()) callback();
frames.clear();
intersect([5, 6, 7]);
await flush();
assert.match(elements.get('#attachment-page-label').textContent, /第 6 \/ 12 页/);
assert.equal(media(0).src, '');
assert.equal(media(0).hidden, true);
assert.equal(urls.size, 3, 'offscreen decoded images are released');
const anchorTop = sheets()[5].getBoundingClientRect().top;
click('attachment-zoom-in');
assert.equal(elements.get('#attachment-zoom-label').textContent, '125%');
assert.ok(Math.abs(sheets()[5].getBoundingClientRect().top - anchorTop * 1.25) < 1);
click('attachment-fit');
assert.ok(Math.abs(sheets()[5].getBoundingClientRect().top - anchorTop) < 1);

intersect(Array.from({length: 12}, (_, index) => index + 1));
await flush();
assert.equal(urls.size, 6, 'decoded page budget is bounded even with many visible tiny pages');
click('attachment-preview-close');
assert.equal(urls.size, 0);
assert.equal(body.children.length, 0);
assert.equal(observers.at(-1).disconnected, true);

failPage = 2;
await open();
intersect([1, 2, 3]);
await flush();
assert.match(placeholder(1).children[0].textContent, /Synthetic page failure/);
assert.equal(placeholder(1).children[1].hidden, false);
assert.equal(placeholder(2).hidden, true, 'one failed page must not block following pages');
failPage = 0;
placeholder(1).children[1].onclick();
await flush();
assert.equal(placeholder(1).hidden, true);
click('attachment-preview-done');

deferPages = true;
await open();
intersect([1, 2, 3]);
const oldRequest = requests.at(-1);
assert.equal(oldRequest.number, 1);
const beforeClose = requests.length;
click('attachment-preview-close');
assert.equal(oldRequest.aborted, true);
await open('image', 2);
const currentImage = body.children[0];
currentImage.onload();
oldRequest.finish();
await flush();
assert.equal(body.children[0], currentImage, 'late PDF response cannot overwrite a reopened image');
assert.equal(requests.length, beforeClose, 'closed PDF cannot keep scheduling pages');
click('attachment-zoom-in');
assert.match(elements.get('#attachment-zoom-label').textContent, /%/);
click('attachment-fit');
assert.equal(elements.get('#attachment-zoom-label').textContent, '适应窗口');
click('attachment-preview-close');
assert.equal(urls.size, 0);
console.log('Continuous preview checks passed: lazy pages, reading position, resource budget, retry, cancellation, stale response, images.');
