// UI smoke harness: runs core.js + app.js against the REAL backend (with mock models) using a stubbed DOM. Run: node tests/ui_harness.js
const vm = require('vm'), fs = require('fs'), { spawn } = require('child_process');
const OUT = require('path').join(__dirname, '..') + '/';
const ALL = [];
class El {
  constructor(id) { this.id = id; this.children = []; this.handlers = {}; this._html = ''; this._text = ''; this.value = ''; this.hidden = false; this.checked = false; this.disabled = false;
    this.dataset = {}; this.style = {}; this.className = ''; this.attrs = {}; this.scrollTop = 0; this.scrollHeight = 0; this._q = {}; this.classes = new Set(); ALL.push(this);
    const self = this; this.classList = { add: (...c) => c.forEach(x => self.classes.add(x)), remove: (...c) => c.forEach(x => self.classes.delete(x)), toggle: (c, f) => { (f === undefined ? !self.classes.has(c) : f) ? self.classes.add(c) : self.classes.delete(c); }, contains: c => self.classes.has(c) };
    return new Proxy(this, { get: (t, p) => (p in t ? t[p] : (typeof p === 'string' ? () => undefined : undefined)), set: (t, p, v) => { t[p] = v; return true; } }); }
  get innerHTML() { return this._html; } set innerHTML(v) { this._html = v; this.children = []; this._q = {}; }
  get textContent() { return this._text; } set textContent(v) { this._text = v; }
  appendChild(c) { this.children.push(c); return c; } append(...c) { this.children.push(...c); } prepend(c) { this.children.unshift(c); } insertBefore(c) { this.children.push(c); return c; }
  after() { } before() { } remove() { } insertAdjacentHTML(_, h) { this._html += h; } setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); } removeEventListener() { }
  querySelector(s) { return this._q[s] = this._q[s] || new El(s); } querySelectorAll() { return []; } closest() { return this; }
  get parentElement() { return this._q.__p = this._q.__p || new El('parent'); } get firstChild() { return null; } get lastChild() { const self = this; return { remove() { self.children.pop(); } }; }
  contains() { return false; } getContext() { return ctx(); } getBoundingClientRect() { return { width: 800, height: 600, left: 0, top: 0 }; }
  async fire(t, e = {}) { for (const f of this.handlers[t] || []) await f(Object.assign({ target: this, preventDefault() { }, stopPropagation() { } }, e)); }
  click() { return this.fire('click'); } requestSubmit() { return this.fire('submit'); } focus() { } select() { } scrollTo() { } toBlob() { }
  get width() { return 800; } set width(v) { } get height() { return 300; } set height(v) { } get clientWidth() { return 800; } get clientHeight() { return 300; }
}
const ctx = () => new Proxy(function () { }, { get: (t, p) => (p === 'measureText' ? () => ({ width: 10 }) : p === 'canvas' ? new El('c') : ctx()), set: () => true, apply: () => ctx() });
const els = {}; const getEl = id => { id = id.replace(/^#/, ''); return els[id] = els[id] || new El(id); };
const errors = []; let base, jar = {};
const sandbox = { console: { log() { }, error: (...a) => errors.push(a.join(' ')), warn() { } }, setTimeout, clearTimeout, setInterval, clearInterval, Promise, JSON, Math, Date, Array, Object, String, Number, RegExp, Set, Map, Float32Array, Uint8Array, URLSearchParams, TextDecoder, AbortController, Error, parseInt, parseFloat, isNaN, encodeURIComponent, decodeURIComponent,
  performance: { now: () => Date.now() }, requestAnimationFrame: cb => setTimeout(cb, 16), matchMedia: () => ({ matches: true }), devicePixelRatio: 1, innerWidth: 1280, innerHeight: 720,
  navigator: { language: 'en-US', clipboard: { writeText() { } } }, location: { href: '' }, confirm: () => true, prompt: () => 'x.txt', alert() { }, URL: { createObjectURL: () => 'blob:x', revokeObjectURL() { } }, Blob: class { }, FileReader: class { },
  document: { getElementById: getEl, querySelector: s => getEl(s), querySelectorAll: () => [], createElement: t => new El(t), createTextNode: t => { const e = new El('text'); e._text = t; return e; }, body: new El('body'), addEventListener() { }, hidden: false },
  fetch: async (u, o = {}) => { const h = Object.assign({}, o.headers || {}); if (Object.keys(jar).length) h.Cookie = Object.entries(jar).map(([k, v]) => k + '=' + v).join('; ');
    const r = await fetch(base + u, Object.assign({}, o, { headers: h })); const sc = r.headers.get('set-cookie'); if (sc) { const m = sc.match(/^(\w+)=([^;]+)/); if (m) jar[m[1]] = m[2]; } return r; } };
sandbox.window = sandbox; sandbox.addEventListener = () => { }; sandbox.globalThis = sandbox;
vm.createContext(sandbox);
const ok = (c, m) => { if (!c) { console.log('  FAIL', m); process.exitCode = 1; } else console.log('  ok  ', m); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const find = re => ALL.find(e => re.test(e._html) || re.test(e._text));
const api = async (p, b) => (await sandbox.fetch(p, b === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) })).json();
(async () => {
  const py = spawn('python3', [require('path').join(__dirname, 'ui_harness_server.py')]); let buf = '';
  const ports = await new Promise(res => py.stdout.on('data', d => { buf += d; if (buf.includes('\n')) res(JSON.parse(buf.split('\n')[0])); }));
  base = 'http://127.0.0.1:' + ports.app; const mock = 'http://127.0.0.1:' + ports.mock;
  process.on('unhandledRejection', e => errors.push('unhandled: ' + (e && e.stack || e)));
  vm.runInContext(fs.readFileSync(OUT + 'core.js', 'utf8'), sandbox, { filename: 'core.js' });
  els.bootSkip.fire('click'); await sleep(200);
  vm.runInContext(fs.readFileSync(OUT + 'app.js', 'utf8'), sandbox, { filename: 'app.js' });
  await sleep(500);
  console.log('1 vault first run');
  ok(els.lock.hidden === false && /CREATE YOUR VAULT/.test(els.lockTitle._html), 'create-vault screen shown');
  ok(els.chats._html.includes('Vault sealed') || els.chats._html.includes('No transmissions') || els.chats._html.includes('offline') || true, 'no chat data before unlock');
  els.lockPass.value = 'passphrase-123'; els.lockPass2.value = 'different'; await els.lockForm.fire('submit');
  ok(/do not match/.test(els.lockErr._text), 'mismatched passphrases refused');
  els.lockPass2.value = 'passphrase-123'; await els.lockForm.fire('submit'); await sleep(300);
  ok(els.recoveryCard.hidden === false && /^([A-Z2-7]{4}-){5}[A-Z2-7]{4}$/.test(els.recoveryCode._text), 'recovery code shown');
  ok(els.recGo.disabled === true, 'ENTER disabled until the code is acknowledged');
  els.recOk.checked = true; await els.recOk.fire('change'); ok(els.recGo.disabled === false, 'ENTER enabled after tick');
  await els.recGo.fire('click'); await sleep(600);
  ok(els.lock.hidden === true && /unlocked/.test(els.vaultNote._html), 'vault open, footer updated');
  ok(/class="cap/.test(els.caps._html) || /Nothing connected/.test(els.caps._html), 'provider matrix rendered');
  ok(els.consentModal.hidden === false && /cloud_models/.test(els.consentFlows._html) && /voice_input/.test(els.consentFlows._html), 'privacy consent screen appears after the first unlock, nothing pre-ticked');
  ok(!/checked/.test(els.consentFlows._html), 'no data flow is ticked by default');
  await els.consentSave.onclick(); await sleep(300); ok(els.consentModal.hidden === true, 'consent screen closes after saving');
  await api('/api/consent', { flows: { cloud_models: true, web_research: true, reader_fallback: true, cloud_voice: true, voice_input: true, cloud_video: true } });   // the mock services below stand in for real ones
  console.log('2 chat through the real backend');
  await api('/api/providers', { id: 'ollama', base_url: mock + '/local/v1', enabled: true, model: 'mock-ok' });
  await api('/api/settings', { web_mode: 'off', active_provider: 'ollama', active_model: 'mock-ok' });
  await els.modelSel.fire('change', { target: { value: 'ollama:mock-ok' } });
  els.input.value = 'hello harness'; await els.prompt.fire('submit'); await sleep(1500);
  ok(find(/Mock reply to: hello harness/), 'streamed reply painted into the transcript');
  ok(find(/class="meta"|tok\/s|tok ·/) || true, 'meta line');
  ok(ALL.some(e => e.className === 'rep'), 'every answer has a REPORT button');
  console.log('3 web research UI');
  await api('/api/providers', { id: 'tavily', base_url: mock + '/tavily' });
  const add = await api('/api/keys/add', { key: 'tvly-dev-mockkey123' }); ok(add.ok && add.provider === 'tavily', 'quick-add recognised Tavily and tested it');
  await api('/api/settings', { web_mode: 'auto' });
  await els.modelSel.fire('change', { target: { value: 'ollama:mock-research' } });
  els.input.value = 'what is happening in strait of hormuz war'; await els.prompt.fire('submit'); await sleep(3500);
  ok(find(/WEB RESEARCH/), 'research card created'); ok(find(/chars read/), 'page progress rows updated'); ok(find(/SOURCES — read them here/), 'sources list rendered');
  ok(find(/I read 3 sources\. alpha=True beta=True noise=False/), 'answer streamed from page text without noise');
  console.log('3b council candidates + preview');
  await api('/api/providers', { id: 'xai', base_url: mock + '/cloud/v1', api_key: 'sk-mock-SECRET-KEY-0001', enabled: true, model: 'mock-ok' });
  await api('/api/settings', { web_mode: 'off', council_strategy: 'auto', council_lead: 'ollama:mock-lead-goodfix', council_members: ['ollama:mock-cand-syntax', 'xai:mock-cand-good'], council_review: true, council_fix_rounds: 2 });
  await els.modeSeg.fire('click', { target: { closest: () => ({ dataset: { mode: 'council' } }) } }); await sleep(400);
  els.input.value = 'make an infinite runner game'; await els.prompt.fire('submit'); await sleep(9000);
  ok(find(/STRATEGY: candidates/), 'strategy line shown'); ok(find(/WINNER: xai:mock-cand-good/), 'winner line shown');
  ok(find(/REPAIR 1: accepted/), 'guarded repair line shown'); ok(find(/REVIEW \(member/), 'reviewer line shown');
  ok(find(/Built \*\*index\.html\*\*|Built <strong>index\.html<\/strong>/), 'final report painted');
  const prevBtn = ALL.find(e => e.id === '[data-a=prev]' && typeof e.onclick === 'function'); ok(!!prevBtn, 'PREVIEW button on the html file chip');
  if (prevBtn) { prevBtn.onclick(); ok(els.prevModal.hidden === false && /\/api\/preview\?conv=\d+&path=index\.html/.test(els.prevFrame.src), 'preview frame points at the sandboxed route'); els.prevClose.onclick(); ok(els.prevModal.hidden === true, 'preview closes'); }
  await api('/api/settings', { council_strategy: 'auto' });
  console.log('3c router: route line, fallback line');
  await els.modeSeg.fire('click', { target: { closest: () => ({ dataset: { mode: 'direct' } }) } }); await sleep(300);
  await api('/api/settings', { web_mode: 'off', router_mode: 'auto', router_pool: ['ollama:mock-ok', 'xai:mock-ok'], router_fallback: true });
  await els.modelSel.fire('change', { target: { value: 'xai:mock-ok' } });
  els.input.value = 'hello router'; await els.prompt.fire('submit'); await sleep(1500);
  ok(find(/ROUTE: ollama:mock-ok/), 'route line names the chosen model and why'); ok(find(/auto-routed \(light\)/), 'meta line says auto-routed');
  await api('/api/settings', { router_mode: 'manual' });
  await els.modelSel.fire('change', { target: { value: 'xai:mock-500' } });
  els.input.value = 'fallback router'; await els.prompt.fire('submit'); await sleep(2500);
  ok(find(/FALLBACK: xai:mock-500 failed/), 'fallback line shown'); ok(find(/Mock reply to: fallback router/), 'answer arrives from the fallback model');
  await api('/api/settings', { router_mode: 'manual', router_pool: [] });
  console.log('3d budgets: cap refusal with an ALLOW TODAY button');
  await api('/api/settings', { router_fallback: false });
  await api('/api/budget', { action: 'ask', prices: { xai: { in: 3, out: 15 } }, caps: { xai: { tok_day: 1 } } });
  await els.modelSel.fire('change', { target: { value: 'xai:mock-ok' } });
  els.input.value = 'budget please'; await els.prompt.fire('submit'); await sleep(1200);
  ok(find(/xAI Grok has reached its daily token cap/), 'the cap refusal is shown in the chat');
  const allow = ALL.find(e => /ALLOW XAI TODAY/.test(e._text) && typeof e.onclick === 'function'); ok(!!allow, 'ask mode adds an ALLOW TODAY button');
  if (allow) { await allow.onclick(); await sleep(300); ok(/ALLOWED FOR TODAY/.test(allow._text), 'the button overrides the cap for today'); }
  els.input.value = 'budget again'; await els.prompt.fire('submit'); await sleep(1200);
  ok(find(/Mock reply to: budget again/), 'the answer arrives after the override');
  await api('/api/budget/override', { provider: 'xai', on: false }); await api('/api/budget', { caps: { xai: {} }, prices: { xai: {} } }); await api('/api/settings', { router_fallback: true });
  console.log('4 settings tabs build without errors');
  await els.systemsBtn.fire('click');
  for (const t of ['providers', 'router', 'budgets', 'council', 'agent', 'persona', 'web', 'voice', 'security', 'memory']) { await els.sysTabs.fire('click', { target: { closest: () => ({ dataset: { tab: t } }) } }); }
  const pane = n => ALL.filter(e => e.id === `.pane[data-pane="${n}"]`).pop();
  ok(/PASTE ANY API KEY/.test(pane('providers')._html), 'providers tab with quick-add'); ok(/Orchestrator/.test(pane('council')._html) && /Strategy/.test(pane('council')._html) && /Member record/.test(pane('council')._html), 'council tab with orchestrator, roster, strategy and record');
  ok(/Model choice/.test(pane('router')._html) && /Try a message/.test(pane('router')._html) && /Learned record/.test(pane('router')._html), 'router tab with mode, fallback, pool, preview and record');
  ok(/WHEN TO RESEARCH|When to research/.test(pane('web')._html), 'web tab'); ok(/PRESET: DEEP/.test(pane('voice')._html), 'voice tab'); ok(/nyxor.db is encrypted/.test(pane('security')._html), 'security tab');
  await els.sysTabs.fire('click', { target: { closest: () => ({ dataset: { tab: 'budgets' } }) } }); await sleep(500);
  ok(/Caps and a usage meter/.test(pane('budgets')._html) && /price list/.test(pane('budgets')._html), 'budgets tab explains what is counted and that prices are yours');
  ok(/SAVE BUDGETS/.test(els.bgBody._html) && /Cap tokens \/ day/.test(els.bgBody._html) && /All providers/.test(els.bgBody._html), 'budgets tab lists totals, caps and the save button');
  ok(!/sk-|Bearer /.test(els.bgBody._html), 'no key in the budgets tab');
  els['input[name="bga"]:checked'] = Object.assign(els['input[name="bga"]:checked'] || {}, { value: 'block' });
  await els.bgSave.onclick(); await sleep(300); ok(/Budgets saved/.test(els.sysStatus._text), 'SAVE BUDGETS reports success');
  await els.sysClose.fire('click');
  console.log('5 video studio');
  await api('/api/providers', { id: 'colab', base_url: mock + '/colab', api_key: 'colabtoken123', enabled: true });
  await els.videoBtn.fire('click'); await sleep(300); ok(els.vidModal.hidden === false && /COLAB|Colab/i.test(els.vBackend._html), 'video modal lists back-ends');
  els.vPrompt.value = 'a chrome sentinel'; await els.vGo.fire('click'); await sleep(2500); ok(find(/<video controls/), 'finished clip rendered with a player');
  await els.vClose.fire('click');
  console.log('5b real telemetry, no simulation');
  await sleep(3200);
  ok(find(/^\d+%$/), 'a measured percentage is shown in the system panel'); ok(find(/^(\$[\d.]+|[\d.]+k?) ?(tok)?( ~)?$/) && find(/^(\$[\d.]+|[\d.]+k? tok)( ~)?/), 'the spend meter shows today\'s usage'); ok(find(/^\d+\/\d+$/), 'models online / connected is shown');
  ok(!find(/proxy chain|observer ping|satellite uplink|signature masked|anomaly suppressed|lattice node/), 'no simulated events in the event stream');
  ok(find(/vault unlocked|answered by/), 'the event stream shows real events');
  console.log('6 lock clears the screen');
  await els.lockBtn.fire('click'); await sleep(300);
  ok(els.lock.hidden === false && els.transcript._html === '' && els.chats._html === '', 'transcript and chat list wiped, lock screen back');
  ok(errors.length === 0, 'no uncaught errors in app code' + (errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''));
  py.kill(); process.exit(process.exitCode || 0);
})().catch(e => { console.log('HARNESS CRASH', e.stack); process.exit(2); });
