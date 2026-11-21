/* NYXOR v2 — core: v1 visual identity, boot, persona engine, voice. Exposes window.NX for app.js. */
(() => {
'use strict';
const $ = s => document.querySelector(s);
const rand = (a, b) => a + Math.random() * (b - a);
const pick = a => a[Math.floor(Math.random() * a.length)];
const pad = n => String(n).padStart(2, '0');
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

const state = { energy: 0.15, target: 0.15, speaking: false, mode: 'listening', alerts: 0, voice: false, start: Date.now(), busy: false, tel: null, upBase: null, upAt: 0 };

/* ---------------- BOOT ---------------- */
const bootLines = [
  ['> starting interface', ''],
  ['> checking the local backend', ''],
  ['> checking the encrypted vault', ''],
  ['> your data stays on this PC unless you allow otherwise', ''],
  ['> ready', ''],
];
const bootLog = $('#bootLog');
let bootDone = false;
async function boot() {
  for (const [l, r] of bootLines) {
    if (bootDone) return;
    const line = document.createElement('div');
    bootLog.appendChild(line);
    for (const ch of l) { if (bootDone) return; line.textContent += ch; await wait(reduced ? 0 : 9); }
    if (r) { const s = document.createElement('span'); s.className = 'w'; s.textContent = '  [' + r + ']'; line.appendChild(s); }
    await wait(reduced ? 0 : 140);
  }
  await wait(500);
  finishBoot();
}
function finishBoot() {
  if (bootDone) return;
  bootDone = true;
  $('#boot').classList.add('done');
  $('#app').classList.add('on');
  setMode('listening');
  $('#stateLabel').textContent = 'READY';
  setTimeout(() => respond(null, greeting()).then(() => NX.afterGreeting && NX.afterGreeting()), 600);
  setTimeout(() => $('#input').focus({ preventScroll: true }), 900);
}
$('#bootSkip').addEventListener('click', finishBoot);
const wait = ms => new Promise(r => setTimeout(r, ms));

/* ---------------- PANELS: measured readings (telemetry.py), never simulated ---------------- */
const meters = [{ k: 'CPU', id: 'cpu' }, { k: 'Memory', id: 'mem' }, { k: 'Disk (data drive)', id: 'disk' }, { k: 'Model speed', id: 'tps' }, { k: 'Spend today', id: 'spend' }, { k: 'Vault auto-lock', id: 'lock' }];
const metersEl = $('#meters');
meters.forEach(m => {
  m.el = document.createElement('div'); m.el.className = 'meter';
  m.el.innerHTML = `<div class="row"><span>${m.k}</span><b>n/a</b></div><div class="bar"><i style="width:0%"></i></div>`;
  metersEl.appendChild(m.el);
});
const fmtClock = s => `${pad(~~(s / 3600))}:${pad(~~(s / 60) % 60)}:${pad(s % 60)}`;
const gb = b => (b / 1073741824).toFixed(b >= 10737418240 ? 0 : 1) + ' GB';
function setMeter(id, pct, text) {
  const m = meters.find(x => x.id === id); if (!m) return;
  m.el.querySelector('b').textContent = text;
  m.el.querySelector('i').style.width = (pct == null ? 0 : Math.max(0, Math.min(100, pct))) + '%';
}
const fmtTok = n => (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k' : String(n));
const fmtUsd = x => (x === 0 ? '$0.00' : x < 0.01 ? '$' + x.toFixed(4) : '$' + x.toFixed(2));
/* Spend today: dollars when a price is set, else tokens; the bar fills toward the tightest cap (empty when no cap is set). */
function spendText(u) {
  if (!u || !u.calls) return 'none yet';
  const main = u.usd > 0 ? fmtUsd(u.usd) : fmtTok(u.tokens) + ' tok';
  return main + (u.estimated ? ' ~' : '') + (u.cap_pct != null ? ' · ' + u.cap_pct + '% of cap' : '');
}
function setAlerts(n) {
  state.alerts = n;
  const t = $('#threat'); t.textContent = String(n); t.classList.toggle('hot', n > 0);
}
/* Called by app.js every few seconds with the backend's real readings. A reading that could not be measured shows n/a. */
function applyTelemetry(t) {
  state.tel = t;
  setMeter('cpu', t.cpu, t.cpu == null ? 'n/a' : t.cpu + '%');
  setMeter('mem', t.mem, t.mem == null ? 'n/a' : t.mem + '%');
  setMeter('disk', t.disk, t.disk == null ? 'n/a' : t.disk + '%');
  const tps = t.tps_last;
  setMeter('tps', tps ? tps / 60 * 100 : 0, tps ? tps + ' tok/s' : 'n/a');
  setMeter('spend', t.usage && t.usage.cap_pct != null ? t.usage.cap_pct : 0, spendText(t.usage));
  setMeter('lock', t.lock_left == null ? 0 : t.lock_left / (t.auto_lock_minutes * 60) * 100, t.lock_left == null ? 'off' : fmtClock(t.lock_left).slice(3));
  $('#nodeCount').textContent = t.models ? `${t.models_online}/${t.models}` : '0';
  setAlerts(t.alerts || 0);
  state.upBase = t.uptime; state.upAt = Date.now();
}

/* ---------------- LOG ---------------- */
const logEl = $('#log');
function log(text, cls = '', at) {
  const d = at ? new Date(at * 1000) : new Date();
  const li = document.createElement('li'); if (cls) li.className = cls;
  li.innerHTML = `<time>${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}</time><span></span>`;
  li.querySelector('span').textContent = text;
  logEl.prepend(li);
  while (logEl.children.length > 40) logEl.lastChild.remove();
}

/* ---------------- ACTIVITY (the animated sphere was removed in the 2.3 redesign) ---------------- */
const lnodes = [];   // kept only so older callers keep working

/* ---------------- CLOCK ---------------- */
setInterval(() => {
  const d = new Date();
  $('#clock').textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  $('#uptime').textContent = state.upBase == null ? '--:--:--' : fmtClock(state.upBase + ~~((Date.now() - state.upAt) / 1000));
}, 1000);

function setMode(m) { state.mode = m; document.body.dataset.mode = m; }
/* ---------------- PERSONA ENGINE ----------------
   The voice is calm and brief. It never claims abilities or actions the software does not have. This scripted voice only answers when
   no model is connected; it states plainly that it is scripted. */
function greeting() {
  const h = new Date().getHours();
  const part = h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return `${part}. NYXOR is ready.\nYour chats and keys stay on this PC unless you allow otherwise.\n\nType /help for commands.`;
}

const commands = {
  help: () => `COMMANDS\n/status     · measured system and model readings\n/clear      · clear this transcript\n\nAnything else is sent to the model you selected.`,
  status: () => {
    const t = state.tel;
    if (!t) return 'No readings yet. They appear once the vault is unlocked.';
    const n = v => (v == null ? 'n/a' : v);
    return `SYSTEM (measured on this PC)\ncpu ............ ${n(t.cpu)}${t.cpu == null ? '' : '%'}\nmemory ......... ${n(t.mem)}${t.mem == null ? '' : '%'}${t.mem_total ? ' (' + gb(t.mem_used) + ' of ' + gb(t.mem_total) + ')' : ''}\ndisk ........... ${n(t.disk)}${t.disk == null ? '' : '%'}${t.disk_free ? ' (' + gb(t.disk_free) + ' free)' : ''}\nuptime ......... ${fmtClock(t.uptime)}\nmodels ......... ${t.models_online} of ${t.models} reachable\nlast speed ..... ${t.tps_last ? t.tps_last + ' tok/s' : 'n/a'}\nchats .......... ${t.chats} · memories ${t.memories} · files ${t.files}\nalerts ......... ${t.alerts} in the last hour\nvault .......... ${t.lock_left == null ? 'auto-lock off' : 'locks after idle in ' + fmtClock(t.lock_left).slice(3)}`;
  },
  clear: () => { $('#transcript').innerHTML = ''; return 'Transcript cleared.'; },
};

const intents = [
  [/\b(who|what) are you\b|your name|identify/i, ['I am NYXOR, a local AI workspace. I run on this PC. Answers come from the model you connect: a local one such as Ollama, or a cloud one with your own key.']],
  [/\b(hi|hello|hey|greetings)\b/i, ['Hello. State what you need.']],
  [/\b(help|assist|can you|could you)\b/i, ['Type /help for commands. To chat with an AI model, connect one in SYSTEMS \u2192 PROVIDERS. Ollama runs locally and needs no key.']],
  [/\b(time|date|day)\b/i, [() => `Local time is ${new Date().toLocaleTimeString()}, ${new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}.`]],
  [/\b(thank|thanks)\b/i, ['Noted.']],
  [/\b(bye|goodbye|exit)\b/i, ['Understood. Your chats stay in the encrypted vault on this PC.']],
];
const fallback = [
  () => 'No model is connected, so I can only answer basic questions and commands. Connect one in SYSTEMS \u2192 PROVIDERS (Ollama runs locally and needs no key), then ask again. Type /help for commands.',
];

function think(q) {
  const t = q.trim();
  if (t.startsWith('/')) {
    const c = t.slice(1).split(/\s+/)[0].toLowerCase();
    return commands[c] ? commands[c](t.slice(1 + c.length).trim()) : `Unknown command "/${c}". Type /help.`;
  }
  for (const [re, opts] of intents) if (re.test(t)) { const o = pick(opts); return typeof o === 'function' ? o(t) : o; }
  return pick(fallback)(t);
}

/* ---------------- TRANSCRIPT ---------------- */
const tx = $('#transcript'), jump = $('#jumpBtn');
/* Auto-scroll that never fights the reader: the chat follows new text only while it is pinned to the bottom. Scrolling up (wheel,
   touch, scrollbar, PageUp/Home) unpins it; reaching the bottom again, or pressing LATEST, pins it again. Only the reader's own
   scrolling can change the pin, so scrolls we cause ourselves (or layout growth) never release it by accident. */
let stick = true, userAt = 0, dragging = false;
const gap = () => tx.scrollHeight - tx.scrollTop - tx.clientHeight;
const showJump = () => { if (jump) jump.hidden = stick || gap() < 40; };
function scrollTx(force) { if (force) stick = true; if (stick) tx.scrollTop = tx.scrollHeight; showJump(); }
['touchstart', 'touchmove'].forEach(t => tx.addEventListener(t, () => { userAt = Date.now(); }, { passive: true }));
tx.addEventListener('wheel', e => { userAt = Date.now(); if (e.deltaY < 0) stick = false; showJump(); }, { passive: true });
tx.addEventListener('pointerdown', () => { dragging = true; userAt = Date.now(); });
addEventListener('pointerup', () => { dragging = false; });
addEventListener('keydown', e => { if (/^(PageUp|PageDown|Home|End|ArrowUp|ArrowDown)$/.test(e.key) && !/^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '')) userAt = Date.now(); });
tx.addEventListener('scroll', () => { if (dragging || Date.now() - userAt < 700) stick = gap() < 8; showJump(); }, { passive: true });
if (jump) jump.addEventListener('click', () => scrollTx(true));
function addMsg(who, text, cls) {
  const m = document.createElement('div'); m.className = 'msg ' + cls;
  m.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
  m.querySelector('.body').textContent = text || '';
  tx.appendChild(m); scrollTx(true);
  return m.querySelector('.body');
}
async function typeOut(el, text) {
  const cur = document.createElement('span'); cur.className = 'cursor';
  el.textContent = ''; el.appendChild(cur);
  const node = document.createTextNode(''); el.insertBefore(node, cur);
  const speed = reduced ? 0 : Math.max(6, 22 - text.length / 40);
  for (let i = 0; i < text.length; i++) {
    node.textContent += text[i];
    if (i % 3 === 0) scrollTx();
    if (speed) await wait(text[i] === '\n' ? speed * 6 : text[i] === '.' ? speed * 4 : speed);
  }
  cur.remove(); scrollTx();
}

async function respond(q, preset) {
  state.busy = true;
  setMode('thinking'); state.target = 0.45; $('#stateLabel').textContent = 'WORKING';
  const el = addMsg('NYXOR', '', 'ai');
  el.innerHTML = '<span class="cursor"></span>';
  const [text] = await Promise.all([Promise.resolve(preset ?? think(q)), wait(reduced ? 0 : rand(450, 900))]);
  setMode('speaking'); state.speaking = true; state.target = 0.95;
  speak(text);
  await typeOut(el, text);
  state.speaking = false; state.target = 0.15; setMode('listening');
  $('#stateLabel').textContent = 'READY';
  state.busy = false;
}

/* ---------------- VOICE OUT ---------------- */
// Pluggable: engine 'browser' (Web Speech, pitched down) or 'cloud' (app.js plays backend TTS through the Ultron FX chain)
const synth = window.speechSynthesis;
const voiceCfg = { engine: 'browser', voiceName: '', pitch: 0.2, rate: 0.86, volume: 1, maxChars: 1200, hook: null, stop: null, busy: null };
let nyxVoice = null;
const DEEP = /david|mark|guy|ryan|davis|daniel|alex|fred|george|james|google uk english male|male/i;
function chooseVoice() {
  if (!synth) return;
  const vs = synth.getVoices();
  nyxVoice = (voiceCfg.voiceName && vs.find(v => v.name === voiceCfg.voiceName))
    || vs.find(v => /^en/i.test(v.lang) && DEEP.test(v.name)) || vs.find(v => /^en/i.test(v.lang)) || vs[0] || null;
}
if (synth) { chooseVoice(); synth.onvoiceschanged = chooseVoice; }
const cleanSpeech = t => t.replace(/```[\s\S]*?```/g, ' code block. ').replace(/[`*#▸·…]|\.{3,}/g, ' ').replace(/\/\w+/g, '').replace(/\s+/g, ' ').trim();
function say(t) {
  t = cleanSpeech(t); if (!t) return;
  if (voiceCfg.engine === 'cloud' && voiceCfg.hook) return voiceCfg.hook(t);
  if (!synth) return;
  const u = new SpeechSynthesisUtterance(t);
  if (nyxVoice) u.voice = nyxVoice;
  u.pitch = voiceCfg.pitch; u.rate = voiceCfg.rate; u.volume = voiceCfg.volume;
  synth.speak(u);
}
function stopSpeech() { if (synth) synth.cancel(); if (voiceCfg.stop) voiceCfg.stop(); }
function speak(text) { if (!state.voice) return; stopSpeech(); say(text); }
$('#voiceBtn').addEventListener('click', () => {
  if (!synth && voiceCfg.engine !== 'cloud') { log('voice synthesis unavailable in this browser', 'hot'); return; }
  state.voice = !state.voice;
  $('#voiceBtn').setAttribute('aria-pressed', state.voice);
  $('#voiceWave').setAttribute('opacity', state.voice ? '1' : '.3');
  log(`voice output ${state.voice ? 'enabled' : 'muted'}`, 'ok');
  if (!state.voice) stopSpeech(); else speak('Voice channel open.');
});

/* ---------------- VOICE IN ---------------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
$('#micBtn').addEventListener('click', () => {
  if (!SR) { addMsg('SYS', 'Speech recognition is not supported in this browser. Try Chrome, or type instead.', 'sys'); return; }
  try {
    const r = new SR(); r.lang = 'en-US'; r.interimResults = false;
    $('#micBtn').classList.add('rec'); setMode('hearing'); state.target = 0.5;
    r.onresult = e => { $('#input').value = e.results[0][0].transcript; $('#prompt').requestSubmit(); };
    r.onerror = () => addMsg('SYS', 'Microphone access was blocked. Allow mic permission, or type instead.', 'sys');
    r.onend = () => { $('#micBtn').classList.remove('rec'); if (!state.busy) { setMode('listening'); state.target = 0.15; } };
    r.start();
  } catch { addMsg('SYS', 'Microphone unavailable here. Type instead.', 'sys'); }
});

addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== $('#input')) { e.preventDefault(); $('#input').focus(); $('#input').value = '/'; }
});


// light markdown: code fences, inline code, bold
const esc = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
function md(s) {
  const parts = s.split(/```/);
  return parts.map((p, i) => {
    if (i % 2) { const nl = p.indexOf('\n'); const body = nl >= 0 ? p.slice(nl + 1) : p; return `<pre><code>${esc(body.replace(/\n$/, ''))}</code></pre>`; }
    return esc(p).replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  }).join('');
}

// sentence-level voice while streaming
let speakBuf = '', spokenChars = 0, closed = false;
// long answers: speak the opening, then say the rest is on screen (voiceCfg.maxChars, 0 = unlimited)
function resetSpeech() { speakBuf = ''; spokenChars = 0; closed = false; }
function speakStream(chunk, flush) {
  if (!state.voice || (!synth && voiceCfg.engine !== 'cloud')) return;
  speakBuf += chunk;
  const re = /^([\s\S]*?[.!?;:\n])(\s+|$)/;
  let m;
  while ((m = speakBuf.match(re)) && m[1].trim().length > 1) {
    queueUtter(m[1]); speakBuf = speakBuf.slice(m[0].length);
    if (!m[2]) break;
  }
  if (flush && speakBuf.trim()) { queueUtter(speakBuf); speakBuf = ''; }
}
function queueUtter(t) {
  const max = voiceCfg.maxChars || 0;
  if (max && spokenChars > max) {
    if (!closed) { closed = true; say('The full briefing is on screen.'); }
    return;
  }
  spokenChars += t.length; say(t);
}
const isSpeaking = () => !!((synth && (synth.speaking || synth.pending)) || (voiceCfg.busy && voiceCfg.busy()));


window.NX = { $, rand, pick, pad, wait, state, log, setMode, setAlerts, applyTelemetry, fmtTok, fmtUsd, addMsg, respond, commands, think, greeting, scrollTx,
              speakStream, esc, lnodes, reduced, afterGreeting: null, voiceCfg, speak, stopSpeech, chooseVoice, resetSpeech, isSpeaking,
              listVoices: () => (synth ? synth.getVoices() : []) };
boot();
})();
