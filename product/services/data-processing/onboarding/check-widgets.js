// Minimal DOM harness: parse the guide, run its script, assert every widget rendered.
const GUIDE = '/home/ubuntu/nmn/continual_learning/product/services/data-processing/onboarding/field-guide.html';
const src = await Deno.readTextFile(GUIDE);
const js = src.match(/<script>([\s\S]*?)<\/script>/)[1];

// ---- tiny DOM ------------------------------------------------------------
let idc = 0;
class El {
  constructor(tag){ this.tagName=(tag||'div').toUpperCase(); this.children=[]; this.attrs={}; this.dataset={};
    this.classList={ _s:new Set(),
      add:(...c)=>c.forEach(x=>this.classList._s.add(x)),
      remove:(...c)=>c.forEach(x=>this.classList._s.delete(x)),
      contains:c=>this.classList._s.has(c),
      toggle:c=>{ if(this.classList._s.has(c)){this.classList._s.delete(c);return false;} this.classList._s.add(c); return true; } };
    this._html=''; this._text=''; this.style={}; this._listeners={}; this.parentNode=null; this.id='e'+(idc++);
  }
  set innerHTML(v){ this._html=String(v); } get innerHTML(){ return this._html; }
  set textContent(v){ this._text=String(v); } get textContent(){ return this._text; }
  set className(v){ this.classList._s=new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className(){ return [...this.classList._s].join(' '); }
  setAttribute(k,v){ this.attrs[k]=v; } getAttribute(k){ return this.attrs[k] ?? null; }
  addEventListener(t,f){ (this._listeners[t] ||= []).push(f); }
  querySelector(){ return null; } querySelectorAll(){ return []; }
  closest(){ return null; } appendChild(c){ this.children.push(c); return c; }
  getContext(){ return CTX; } get clientWidth(){ return 900; }
}
const CTX = new Proxy({}, { get: (_,p) => (p==='canvas'?undefined:()=>{}) });
const REG = {};                              // id -> El
const mk = id => (REG[id] ||= Object.assign(new El(), { id }));
const doc = {
  querySelector(sel){
    if (sel.startsWith('#')) return mk(sel.slice(1));
    return null;
  },
  querySelectorAll(sel){
    // the script only enumerates rail links, sections, flip cards, and data-* buttons
    if (sel === '.rail a.tk') return Array.from({length:15}, (_,i)=>{ const e=new El('a'); e.setAttribute('href','#m'+i); return e; });
    if (sel === 'section.module') return Array.from({length:15}, ()=> new El('section'));
    if (sel === '.flip') return Array.from({length:6}, ()=> new El('button'));
    if (sel === '[data-rec]') return ['audio','video'].map(v=>{ const e=new El('button'); e.dataset.rec=v; return e; });
    if (sel === '[data-mod]') return ['audio','video'].map(v=>{ const e=new El('button'); e.dataset.mod=v; return e; });
    return [];
  },
  documentElement: new El('html'),
};
globalThis.document = doc;
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#5646C6' });
globalThis.matchMedia = () => ({ matches:false, addEventListener(){} });
globalThis.devicePixelRatio = 1;
globalThis.addEventListener = () => {};
globalThis.MutationObserver = class { observe(){} };
globalThis.IntersectionObserver = class { observe(){} };
globalThis.setInterval = () => 0;
globalThis.window = globalThis;

globalThis.TextEncoder = TextEncoder;

const fn = new Function(js);
fn();
await new Promise(r => setTimeout(r, 60));   // let the async composer settle

const checks = [
  ['record explorer', 'rec-json', h => h.includes('record_id') && h.includes('button class="row"')],
  ['record detail',   'rec-detail', h => true],
  ['dialect stages',  'dial-stages', h => h.includes('asr.v1-fw.v1')],
  ['dialect string',  'dial-out', (h,t) => t.includes('acoustic.v1-ast.v1+asr.v1-fw.v1')],
  ['dialect hash',    'dial-hash', (h,t) => /^[0-9a-f]{64}$/.test(t)],
  ['dialect note',    'dial-note', h => h.length > 40],
  ['explorer scenarios','exp-scn', h => h.includes('Everything works') && h.includes('diarization server is down')],
  ['explorer stations','exp-stations', h => h.includes('st-box') && h.includes('speaker_align')],
  ['explorer log',    'exp-log', h => h.includes('Ready')],
  ['claim buttons',   'ct-btns', h => h.includes('never seen before') && h.includes('budget is spent')],
  ['claim tree',      'ct-tree', h => h.includes('tnode') && h.includes('in flight')],
  ['claim verdict',   'ct-verdict', h => h.includes('fresh')],
  ['quiz',            'quiz', h => (h.match(/quiz-q/g)||[]).length >= 7],
];
let bad = 0;
for (const [name, id, ok] of checks) {
  const el = REG[id];
  const h = el ? el.innerHTML : '', t = el ? el.textContent : '';
  const pass = el && ok(h, t);
  if (!pass) bad++;
  console.log((pass ? 'PASS  ' : 'FAIL  ') + name.padEnd(20) + (pass ? (h||t).slice(0,52).replace(/\n/g,' ') : '(empty or wrong)'));
}
console.log(bad ? `\n${bad} WIDGET(S) BROKEN` : '\nALL WIDGETS RENDER');
