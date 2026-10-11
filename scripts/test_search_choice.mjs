// Focus/blur regression checks against the real searchable-select code, no ledger.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const created = [];
const listeners = new Map();
let changes = 0;
const select = {
  value:'', required:false, disabled:false, dataset:{},
  options:[{value:'',textContent:'全部项目'},{value:'3',textContent:'合成已完工项目'}],
  get selectedOptions() {return this.options.filter(option=>option.value===this.value);},
  before(){}, classList:{add(){}}, closest(){return null;},
  addEventListener(name,callback){listeners.set(name,callback);},
  dispatchEvent(event){changes++;listeners.get(event.type)?.();},
};
const context = vm.createContext({
  Event:class {constructor(type){this.type=type;}},
  MutationObserver:class {observe(){}},
  esc:value=>value,
  document:{createElement(tag) {
    const element={tag,value:'',setAttribute(){},removeAttribute(){},append(){},
      setCustomValidity(){},querySelectorAll(){return [];}};
    created.push(element);
    return element;
  }},
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/forms.js',import.meta.url),'utf8'),context);
const enhance=vm.runInContext('enhanceForms',context);
enhance({querySelectorAll:selector=>selector==='select[data-search]'?[select]:[]});
const input=created.find(element=>element.tag==='input');

// Clicking a page button while the scope already means "all" must not re-render.
input.onblur();
assert.equal(changes,0);

// Clearing a previously committed project still updates its underlying selection.
select.value='3';listeners.get('change')();
input.value='';input.oninput();input.onblur();
assert.equal(select.value,'');
assert.equal(changes,1);
input.onblur();
assert.equal(changes,1);

// Explicitly selecting "all" after typing remains a real commit, not a blur no-op.
select.value='3';listeners.get('change')();
input.value='全部项目';input.oninput();
input.onkeydown({key:'Enter',preventDefault(){}});
assert.equal(select.value,'');
assert.equal(changes,2);
input.onblur();
assert.equal(changes,2);
console.log('PASS: scope blur preserves the next click; clearing and explicit selection still commit.');
