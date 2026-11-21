// NYXOR smoke test: runs a generated web page (HTML + local scripts) headlessly in Node with a fake browser,
// plays ~4 seconds of frames with simulated key presses and clicks, and reports runtime errors, so a game that
// "looks fine" but crashes on frame 1 is caught before you ever open it.   usage: node smoke.js <dir> <entry.html>
const fs = require('fs'), path = require('path'), vm = require('vm');
const [dir, entry] = process.argv.slice(2);
const out = { ok: false, errors: [], warnings: [], frames: 0, raf: 0, draws: 0, scripts: 0, external: [], canvas: false, skipped: '' };
const seen = new Set();
const err = (m, w) => { m = String(m).slice(0, 300); if (!seen.has(m)) { seen.add(m); (w ? out.warnings : out.errors).push(m); } };
let html = '';
try { html = fs.readFileSync(path.join(dir, entry), 'utf8'); } catch (e) { out.errors.push('cannot read ' + entry); console.log(JSON.stringify(out)); process.exit(0); }

let vt = 0; const timers = []; let tid = 1; const raf = []; const listeners = {};   // listeners[type] = [[target, fn]]
const onTarget = (target, type, fn) => { (listeners[type] = listeners[type] || []).push([target, fn]); };
const guard = (fn, label) => { try { return fn(); } catch (e) { err((label ? label + ': ' : '') + (e && e.message ? e.constructor.name + ': ' + e.message : e) + (e && e.stack ? ' @' + (String(e.stack).split('\n').find(l => /<anonymous>|smoke-page/.test(l)) || '').trim() : '')); } };
const ctx2d = canvas => new Proxy({ canvas }, {
  get(t, p) {
    if (p in t) return t[p];
    if (p === 'measureText') return s => ({ width: String(s).length * 7 });
    if (p === 'createLinearGradient' || p === 'createRadialGradient' || p === 'createPattern') return () => ({ addColorStop() { } });
    if (p === 'getImageData') return (x, y, w, h) => ({ data: new Uint8ClampedArray(Math.max(4, (w || 1) * (h || 1) * 4)), width: w, height: h });
    if (p === 'createImageData') return (w, h) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h });
    if (p === 'isPointInPath' || p === 'isPointInStroke') return () => false;
    if (typeof p === 'symbol') return undefined;
    return (...a) => { if (/^(fill|stroke|draw|clear|put|set|rotate|scale|translate|arc|rect|line|quadratic|bezier|ellipse|round|text|fillText|strokeText)/i.test(String(p)) || /Rect|Text|Image|Path/.test(String(p))) out.draws++; };
  },
  set(t, p, v) { t[p] = v; return true; } });
class El {
  constructor(tag, attrs = {}) { this.tagName = String(tag).toUpperCase(); this.nodeName = this.tagName; this.attrs = attrs; this.id = attrs.id || ''; this.className = attrs.class || ''; this.style = {}; this.dataset = {}; this.children = []; this.childNodes = this.children;
    this.width = +attrs.width || 300; this.height = +attrs.height || 150; this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this.parentNode = null;
    const self = this; this.classList = { _s: new Set(String(this.className).split(/\s+/).filter(Boolean)), add(...c) { c.forEach(x => this._s.add(x)); }, remove(...c) { c.forEach(x => this._s.delete(x)); }, toggle(c, f) { (f === undefined ? !this._s.has(c) : f) ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } };
    if (this.tagName === 'CANVAS') { out.canvas = true; this._ctx = ctx2d(this); } }
  getContext(k) { return k === '2d' || !k ? this._ctx || (this._ctx = ctx2d(this)) : new Proxy({}, { get: () => () => 0 }); }
  addEventListener(t, f) { onTarget(this, t, f); } removeEventListener() { }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; } append(...c) { c.forEach(x => this.appendChild(x)); } prepend(c) { this.children.unshift(c); } insertBefore(c) { return this.appendChild(c); } removeChild(c) { return c; } remove() { } replaceChildren() { this.children.length = 0; }
  setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k] ?? null; } hasAttribute(k) { return k in this.attrs; } removeAttribute(k) { delete this.attrs[k]; }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.width, height: this.height, right: this.width, bottom: this.height, x: 0, y: 0 }; }
  focus() { } blur() { } click() { } querySelector(s) { return qs(s); } querySelectorAll(s) { return qsa(s); } closest() { return this; } contains() { return false; } cloneNode() { return new El(this.tagName, Object.assign({}, this.attrs)); }
  get offsetWidth() { return this.width; } get offsetHeight() { return this.height; } get clientWidth() { return this.width; } get clientHeight() { return this.height; } toDataURL() { return 'data:image/png;base64,'; } requestFullscreen() { return Promise.resolve(); }
}
// parse the page so that getElementById behaves like a real browser (missing ids return null)
const nodes = [];
for (const m of html.matchAll(/<(\w+)\b([^>]*)>/g)) {
  const attrs = {}; for (const a of m[2].matchAll(/([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) attrs[a[1]] = a[3] ?? a[4];
  if (attrs.id || /^(canvas|button|body|input|div|span)$/i.test(m[1])) nodes.push(new El(m[1], attrs));
}
const body = nodes.find(n => n.tagName === 'BODY') || new El('body');
const byId = id => nodes.find(n => n.id === id) || null;
const match = (n, s) => (s[0] === '#' ? n.id === s.slice(1) : s[0] === '.' ? n.classList._s.has(s.slice(1)) : n.tagName === s.toUpperCase());
const qs = s => { s = String(s).trim().split(/[\s>]+/).pop(); return nodes.find(n => match(n, s)) || null; };
const qsa = s => { s = String(s).trim().split(/[\s>]+/).pop(); return nodes.filter(n => match(n, s)); };
const doc = { getElementById: byId, querySelector: qs, querySelectorAll: qsa, getElementsByClassName: c => qsa('.' + c), getElementsByTagName: t => qsa(t), createElement: t => new El(t), createTextNode: t => ({ textContent: t }), createElementNS: (_, t) => new El(t),
  body, documentElement: new El('html'), head: new El('head'), title: '', readyState: 'loading', hidden: false, visibilityState: 'visible', addEventListener: (t, f) => onTarget(doc, t, f), removeEventListener() { }, hasFocus: () => true, exitFullscreen() { } };
const store = () => { const d = {}; return { getItem: k => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = String(v); }, removeItem: k => { delete d[k]; }, clear: () => { for (const k in d) delete d[k]; }, key: i => Object.keys(d)[i] ?? null, get length() { return Object.keys(d).length; } }; };
const noop = new Proxy(function () { }, { get: (t, p) => (p === Symbol.toPrimitive ? () => 0 : noop), apply: () => noop, construct: () => noop, set: () => true });
const win = {
  document: doc, innerWidth: 1280, innerHeight: 720, outerWidth: 1280, outerHeight: 720, devicePixelRatio: 1, scrollX: 0, scrollY: 0, location: { href: 'http://localhost/', hash: '', search: '', pathname: '/', origin: 'http://localhost', reload() { } },
  navigator: { userAgent: 'smoke', language: 'en-US', platform: 'smoke', maxTouchPoints: 0, getGamepads: () => [], vibrate() { } }, localStorage: store(), sessionStorage: store(), screen: { width: 1280, height: 720 }, history: { pushState() { }, replaceState() { } },
  performance: { now: () => vt }, console: { log() { }, info() { }, debug() { }, warn: (...a) => err('console.warn: ' + a.join(' '), true), error: (...a) => err('console.error: ' + a.join(' '), true) },
  requestAnimationFrame: f => { raf.push(f); out.raf++; return raf.length; }, cancelAnimationFrame: i => { if (raf[i - 1]) raf[i - 1] = null; },
  setTimeout: (f, ms = 0, ...a) => { timers.push({ id: ++tid, at: vt + Math.max(0, +ms || 0), f, a }); return tid; }, setInterval: (f, ms = 0, ...a) => { timers.push({ id: ++tid, at: vt + Math.max(1, +ms || 1), every: Math.max(1, +ms || 1), f, a }); return tid; },
  clearTimeout: id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); }, clearInterval: id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); },
  addEventListener: (t, f) => onTarget(win, t, f), removeEventListener() { }, dispatchEvent() { return true; }, matchMedia: () => ({ matches: false, addEventListener() { }, addListener() { } }), getComputedStyle: () => ({ getPropertyValue: () => '' }),
  alert() { }, confirm: () => true, prompt: () => '', open() { }, focus() { }, scrollTo() { }, fetch: () => Promise.reject(new Error('network is blocked in the smoke test')), AudioContext: noop, webkitAudioContext: noop, Audio: class { play() { return Promise.resolve(); } pause() { } addEventListener() { } },
  Image: class { constructor() { setTimeout(() => this.onload && this.onload(), 1); } set src(v) { this._s = v; } get src() { return this._s; } addEventListener() { } },
  Path2D: class { }, ResizeObserver: class { observe() { } disconnect() { } }, MutationObserver: class { observe() { } disconnect() { } }, IntersectionObserver: class { observe() { } disconnect() { } },
  Event: class { constructor(t) { this.type = t; } preventDefault() { } stopPropagation() { } }, CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
};
win.window = win; win.self = win; win.globalThis = win; win.top = win; win.parent = win;
for (const k of ['Math', 'JSON', 'Date', 'Array', 'Object', 'String', 'Number', 'Boolean', 'RegExp', 'Map', 'Set', 'WeakMap', 'WeakSet', 'Symbol', 'Promise', 'Error', 'TypeError', 'RangeError', 'Float32Array', 'Float64Array', 'Uint8Array', 'Uint8ClampedArray', 'Uint16Array', 'Uint32Array', 'Int8Array', 'Int16Array', 'Int32Array', 'ArrayBuffer', 'DataView', 'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURIComponent', 'decodeURIComponent', 'Infinity', 'NaN', 'undefined', 'Proxy', 'Reflect', 'BigInt', 'Intl', 'structuredClone', 'URL', 'URLSearchParams', 'TextEncoder', 'TextDecoder', 'queueMicrotask'])
  if (k in globalThis) win[k] = globalThis[k];
const _DateNow = Date.now; win.Date = class extends Date { static now() { return 1700000000000 + vt; } };
const context = vm.createContext(win);
const run = (code, label) => guard(() => vm.runInContext(code, context, { timeout: 3000, filename: 'smoke-page:' + label }), label);

// scripts in document order (inline, or local src files)
const base = path.dirname(path.join(dir, entry));
for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  const src = (m[1].match(/\bsrc\s*=\s*["']([^"']+)["']/i) || [])[1], type = (m[1].match(/\btype\s*=\s*["']([^"']+)["']/i) || [])[1] || '';
  if (/module/i.test(type)) { out.skipped = 'ES module script skipped'; continue; }
  if (type && !/javascript|ecmascript/i.test(type)) continue;
  let code = m[2], label = 'inline';
  if (src) {
    if (/^(https?:)?\/\//i.test(src)) { out.external.push(src); err('uses an external script (' + src + '); it will not work offline', true); continue; }
    try { code = fs.readFileSync(path.join(base, src.split('?')[0]), 'utf8'); label = src; } catch { err('script ' + src + ' is referenced but missing'); continue; }
  }
  if (code.trim()) { out.scripts++; run(code, label); }
}
const fire = (type, ev = {}, targets) => {
  for (const [t, f] of [...(listeners[type] || [])]) {
    if (targets && !targets.includes(t)) continue;
    const e = Object.assign({ type, target: t, currentTarget: t, preventDefault() { }, stopPropagation() { }, key: '', code: '', keyCode: 0, which: 0, clientX: 400, clientY: 150, offsetX: 400, offsetY: 150, pageX: 400, pageY: 150, button: 0, touches: [{ clientX: 400, clientY: 150, pageX: 400, pageY: 150 }], changedTouches: [{ clientX: 400, clientY: 150 }], timeStamp: vt }, ev);
    guard(() => vm.runInContext('(f,e)=>f.call(e.currentTarget,e)', context, { timeout: 3000 })(f, e), type + ' handler');
  }
};
doc.readyState = 'interactive'; fire('DOMContentLoaded'); doc.readyState = 'complete'; fire('load');
const KEYS = [[' ', 'Space', 32], ['ArrowUp', 'ArrowUp', 38], ['ArrowDown', 'ArrowDown', 40], ['Enter', 'Enter', 13], ['ArrowRight', 'ArrowRight', 39], ['ArrowLeft', 'ArrowLeft', 37], ['w', 'KeyW', 87], ['r', 'KeyR', 82]];
const press = (k) => { const ev = { key: k[0], code: k[1], keyCode: k[2], which: k[2] }; fire('keydown', ev); fire('keyup', ev); };
const FRAMES = 240;
for (let f = 0; f < FRAMES; f++) {
  vt += 1000 / 60; out.frames = f + 1;
  for (const t of timers.filter(t => t.at <= vt)) { guard(() => vm.runInContext('(f,a)=>f(...a)', context, { timeout: 3000 })(t.f, t.a || []), 'timer'); if (t.every) t.at += t.every; else timers.splice(timers.indexOf(t), 1); }
  const q = raf.splice(0); for (const cb of q) if (cb) guard(() => vm.runInContext('(f,t)=>f(t)', context, { timeout: 3000 })(cb, vt), 'animation frame');
  if (f === 8 || f % 25 === 0) press(KEYS[(f / 25 | 0) % KEYS.length]);
  if (f === 60 || f === 150) { fire('mousedown'); fire('mouseup'); fire('click'); fire('touchstart'); fire('touchend'); fire('pointerdown'); fire('pointerup'); }
  if (f === 100) { fire('keydown', { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, which: 40 }); fire('keyup', { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, which: 40 }); }
  if (out.errors.length > 8) break;
}
if (out.canvas && out.draws === 0) err('a <canvas> exists but nothing was ever drawn on it');
if (out.canvas && out.raf === 0 && timers.length === 0 && !(listeners.keydown || []).length) err('the page has a canvas but no animation loop or input handlers', true);
out.ok = out.errors.length === 0;
console.log(JSON.stringify(out));
