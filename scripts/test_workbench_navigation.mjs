// Deterministic view-state checks; no browser or business database is accessed.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import vm from 'node:vm';

const frames = [];
const listeners = new Map();
const button = {hidden: true, addEventListener() {}, setAttribute() {}};
const projectChoice = {value: '', dispatchEvent() {}};
let tables = [{scrollLeft: 0, scrollTop: 0}];
let busy = false;
const context = vm.createContext({
  crypto: {randomUUID}, Event: class {},
  requestAnimationFrame: callback => frames.push(callback),
  main: {querySelectorAll: () => tables, getAttribute: () => busy ? 'true' : 'false'},
  editor: {open: false},
  document: {
    querySelector: selector => selector === '#navigation-back' ? button : projectChoice,
    addEventListener: (event, callback) => listeners.set(event, callback),
  },
  history: {state: null, scrollRestoration: 'auto', replaceState(value) {this.state = value;}},
  location: {hash: '#home'},
  window: {scrollY: 0, scrollX: 0, scrollTo({top, left}) {this.scrollY = top; this.scrollX = left;}},
  state: {project: '', collection: '', page: 0, keyword: '', selectedAnalysis: null,
    scope: {contract_id: null, project_id: null},
    boot: {navigation: [['经营', [['home', '经营驾驶舱'], ['projects', '项目台账'], ['contracts', '合同与结算']]]]}},
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/navigation.js', import.meta.url), 'utf8'), context);
const navigation = vm.runInContext('WorkbenchNavigation', context);
const flush = () => {while (frames.length) frames.shift()();};
const render = reset => {
  const saved = navigation.prepare(reset);
  tables = [{scrollLeft: 0, scrollTop: 0}];
  navigation.finish(saved);
  flush();
  return saved;
};

navigation.init();
assert.equal(context.history.scrollRestoration, 'manual');
render(true);
assert.equal(button.hidden, true);

context.location.hash = '#projects';
context.history.state = null;
render(true);
const projectEntry = context.history.state;
assert.equal(button.hidden, false);
assert.equal(button.title, '返回经营驾驶舱');
context.state.page = 2;
context.state.keyword = '合成项目';
context.state.projectLedgerTab = 'history';
navigation.finish({position: null});
context.window.scrollY = 540;
tables[0].scrollLeft = 170;
listeners.get('scroll')();
listeners.get('click')();

// Hash navigation can change native scroll before hashchange; do not overwrite
// the source entry using the destination's window or project range.
context.state.project = '7';
context.state.collection = 'contracts';
context.state.projectLedgerTab = 'all';
context.location.hash = '#contracts';
context.history.state = null;
context.window.scrollY = 0;
render(true);
const contractEntry = context.history.state;
assert.equal(button.title, '返回项目台账');

context.location.hash = '#projects';
context.history.state = projectEntry;
const restored = render(true);
assert.equal(restored.restored, true);
assert.equal(context.state.project, '');
assert.equal(context.state.page, 2);
assert.equal(context.state.keyword, '合成项目');
assert.equal(context.state.projectLedgerTab, 'history');
assert.equal(context.window.scrollY, 540);
assert.equal(tables[0].scrollLeft, 170);
assert.equal(projectChoice.value, '');

context.location.hash = '#contracts';
context.history.state = contractEntry;
render(true);
assert.equal(context.state.project, '7');
assert.equal(context.state.collection, 'contracts');

// A callback from the previous page must not move a newer loading page.
context.location.hash = '#projects';
context.history.state = projectEntry;
const pending = navigation.prepare(true);
navigation.finish(pending);
busy = true;
context.window.scrollY = 0;
flush();
assert.equal(context.window.scrollY, 0);
console.log('Navigation checks passed: manual restoration, pagination, query, scope, table position, forward, stale frame.');
