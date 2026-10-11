// Deterministic header geometry and lifecycle checks, without a business ledger.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const frames = new Map(), listeners = new Map(), observers = [], resizes = [];
let frameId=0, busy=false, navOpen=false, headerTop=400, headerHeight=50, top=78;
let bounds={left:201,width:1100,bottom:2500};
let widths=[300,180,170,170,150,130];
class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.style={};this.attributes={};}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  setAttribute(name,value) {this.attributes[name]=value;}
}
const header={
  get rows() {return [{cells:widths.map(width=>({getBoundingClientRect:()=>({width})}))}];},
  getBoundingClientRect:()=>({top:headerTop,height:headerHeight}),
  cloneNode:()=>new Element('thead'),
};
const source={isConnected:true,tHead:header,getBoundingClientRect:()=>bounds};
const wrapper={clientLeft:1,clientWidth:900,
  getBoundingClientRect:()=>({left:200}),querySelector:()=>source};
source.parentElement=wrapper;
const main={getAttribute:()=>busy?'true':'false'};
const editor={open:false};
const body=new Element('body');
body.classList={contains:()=>navOpen};
const on=(prefix)=>(event,callback)=>{
  const name=prefix+event;
  assert(!listeners.has(name),'duplicate listener: '+name);
  listeners.set(name,callback);
};
const context=vm.createContext({
  main,editor,
  document:{body,createElement:tag=>new Element(tag),
    querySelector:()=>({getBoundingClientRect:()=>({bottom:top})}),addEventListener:on('document:')},
  window:{innerWidth:1280,addEventListener:on('window:'),visualViewport:{
    width:1280,offsetTop:0,offsetLeft:0,addEventListener:on('viewport:')}},
  requestAnimationFrame:callback=>{frames.set(++frameId,callback);return frameId;},
  MutationObserver:class {constructor(callback){observers.push(callback);}observe(){}},
  ResizeObserver:class {
    constructor(callback){this.callback=callback;this.targets=[];resizes.push(this);}
    observe(element){this.targets.push(element);}disconnect(){this.targets=[];}
  },
});
vm.runInContext(readFileSync(new URL('../web_workbench/static/project-ledger.js',import.meta.url),'utf8'),context);
const follow=vm.runInContext('ProjectLedgerHeader.follow',context);
const flush=()=>{const callbacks=[...frames.values()];frames.clear();callbacks.forEach(callback=>callback());};
const scroll=()=>{listeners.get('document:scroll')();flush();};

follow(wrapper);flush();
const floating=body.children[0];
assert.equal(floating.hidden,true,'original header is still visible');
assert.equal(floating.attributes['aria-hidden'],'true');

headerTop=-100;
listeners.get('document:scroll')();listeners.get('document:scroll')();
assert.equal(frames.size,1,'scroll events share one animation frame');flush();
assert.equal(floating.hidden,false);
assert.equal(floating.style.top,'78px');
assert.equal(floating.style.width,'900px');
assert.equal(floating.children[0].style.width,'1100px');
assert.deepEqual(floating.children[0].children[0].children.map(col=>col.style.width),widths.map(width=>width+'px'));
bounds={...bounds,left:-39};scroll();
assert.equal(floating.children[0].style.transform,'translate(-240px, 0px)');

editor.open=true;observers[1]();flush();assert.equal(floating.hidden,true);
editor.open=false;busy=true;observers[0]();flush();assert.equal(floating.hidden,true);
busy=false;navOpen=true;observers[2]();flush();assert.equal(floating.hidden,true);
navOpen=false;scroll();assert.equal(floating.hidden,false);

bounds.bottom=103;scroll();
assert.equal(floating.style.height,'25px');
assert.equal(floating.children[0].style.transform,'translate(-240px, -25px)');
bounds.bottom=70;scroll();assert.equal(floating.hidden,true,'header leaves at the table end');
source.isConnected=false;observers[0]();flush();
assert.equal(floating.children.length,0,'old table references are released after navigation');
assert.equal(resizes[0].targets.length,0);

source.isConnected=true;bounds={left:201,width:1100,bottom:2500};
const count=listeners.size;follow(wrapper);flush();
assert.equal(listeners.size,count);assert.equal(body.children.length,1,'no duplicated floating headers');
top=105;headerHeight=60;widths=[250,160,155,155,150,130];
resizes[0].callback();flush();
assert.equal(floating.style.top,'105px');assert.equal(floating.style.height,'60px');
assert.equal(floating.children[0].children[0].children[0].style.width,'250px');
follow(null);flush();assert.equal(floating.hidden,true);assert.equal(floating.children.length,0);
console.log('PASS: sticky position, exact columns, horizontal sync, end boundary, modal/navigation cleanup, resize and one-frame scheduling.');
