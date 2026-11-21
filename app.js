/* NYXOR v2.1 — application layer: vault, archive, providers, modes, attachments, web research, files, video, voice */
(() => {
'use strict';
const { $, state, log, wait, esc, addMsg, commands: C, rand, fmtTok, fmtUsd } = NX;
const el = id => document.getElementById(id);
const ea = s => esc(String(s ?? '')).replace(/"/g, '&quot;');
const tx = el('transcript');
const scroll = force => NX.scrollTx(force);   // follows new text only while the reader is at the bottom (core.js)
const OFFLINE = 'Run locally to enable models and memory — start it with: python server.py';
const S = { online: false, locked: true, st: null, conv: null, ctl: null, task: null, files: [], matrix: [], scripted: false, built: {}, atts: [], lastAnswer: '' };
const queue = [];
const domain = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u; } };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt); let j = {};
  try { j = await r.json(); } catch { }
  if (r.status === 423) { S.locked = true; clearUI(); showLock('unlock'); throw new Error(j.error || 'The vault is locked.'); }
  if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status);
  return j;
}
const saveSettings = async patch => { const r = await api('/api/settings', patch); S.st.settings = r.settings; return r.settings; };
const prov = id => S.st.providers.find(p => p.id === id);
const split = ref => { const i = ref.indexOf(':'); return i < 0 ? [ref, ''] : [ref.slice(0, i), ref.slice(i + 1)]; };
const toast = (t, cls) => { addMsg('SYS', t, 'sys'); log(t.slice(0, 140), cls || 'ok'); };

/* ================= vault screen ================= */
let lockMode = 'unlock';
function showLock(mode, legacy) {
  lockMode = mode;
  const create = mode === 'create', rec = mode === 'recovery';
  el('lock').hidden = false; el('recoveryCard').hidden = true; el('lockForm').hidden = false; el('lockErr').hidden = true;
  el('lockTitle').innerHTML = create ? '<span>▣</span>CREATE YOUR VAULT' : '<span>▣</span>VAULT SEALED';
  el('lockSub').textContent = create
    ? 'Choose a passphrase. Your chats, API keys and memories are stored encrypted in nyxor.db and can only be opened here, in the browser.' + (legacy ? ' An older unencrypted database was found: it will be encrypted and the plain copy erased.' : '')
    : rec ? 'Enter the recovery code you saved when the vault was created.' : 'Enter your passphrase. Nothing is readable until you do.';
  el('lockLabel').textContent = rec ? 'Recovery code' : 'Passphrase';
  el('lockConfirmRow').hidden = !create;
  el('lockGo').textContent = create ? 'CREATE VAULT' : 'UNLOCK';
  el('lockAlt').hidden = create; el('lockAlt').textContent = rec ? 'USE PASSPHRASE' : 'USE RECOVERY CODE';
  el('lockPass').type = rec ? 'text' : 'password'; el('lockPass').value = ''; el('lockPass2').value = '';
  el('vaultNote').innerHTML = 'Vault: <b>sealed</b>'; setLink('', 'SEALED');
  setTimeout(() => el('lockPass').focus(), 50);
}
function lockErr(t) { const e = el('lockErr'); e.textContent = t; e.hidden = !t; }
el('lockAlt').addEventListener('click', () => showLock(lockMode === 'recovery' ? 'unlock' : 'recovery'));
el('lockForm').addEventListener('submit', async e => {
  e.preventDefault(); lockErr('');
  const pw = el('lockPass').value;
  try {
    if (lockMode === 'create') {
      if (pw.length < 8) return lockErr('Use at least 8 characters.');
      if (pw !== el('lockPass2').value) return lockErr('The two passphrases do not match.');
      const r = await (await fetch('/api/vault/create', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passphrase: pw }) })).json();
      if (!r.recovery_code) return lockErr(r.error || 'Could not create the vault.');
      el('lockForm').hidden = true; el('recoveryCard').hidden = false; el('recoveryCode').textContent = r.recovery_code; el('recOk').checked = false; el('recGo').disabled = true;
    } else {
      el('lockGo').disabled = true;
      const resp = await fetch('/api/vault/unlock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ secret: pw }) });
      const j = await resp.json().catch(() => ({}));
      el('lockGo').disabled = false;
      if (!resp.ok) return lockErr(j.error || 'Could not unlock.');
      el('lock').hidden = true; S.locked = false; await loadState();
    }
  } catch (err) { el('lockGo').disabled = false; lockErr(err.message); }
});
el('recOk').addEventListener('change', e => { el('recGo').disabled = !e.target.checked; });
el('recGo').addEventListener('click', async () => { el('lock').hidden = true; el('recoveryCode').textContent = ''; S.locked = false; await loadState(); });
el('recCopy').addEventListener('click', () => { navigator.clipboard && navigator.clipboard.writeText(el('recoveryCode').textContent); el('recCopy').textContent = 'COPIED'; });
el('recSave').addEventListener('click', () => {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['NYXOR vault recovery code\n\n' + el('recoveryCode').textContent + '\n\nKeep this file offline. Anyone with it and your nyxor.db can open your vault.\n'], { type: 'text/plain' }));
  a.download = 'nyxor-recovery-code.txt'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
function clearUI() {
  tx.innerHTML = ''; S.conv = null; S.files = []; S.atts = []; S.st = null; S.matrix = []; queue.length = 0;
  ['chats', 'filesList', 'caps'].forEach(i => { el(i).innerHTML = ''; }); renderTray(); stopVC(true);
  document.querySelectorAll('.modal').forEach(m => { if (m.id !== 'lock') m.hidden = true; });
  el('fileCount').textContent = '0'; el('zipAll').hidden = true;
}
async function lockNow() {
  if (!S.online || S.locked) return;
  try { await fetch('/api/vault/lock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch { }
  S.locked = true; clearUI(); showLock('unlock');
}
el('lockBtn').addEventListener('click', lockNow);

/* ================= loading ================= */
async function init() {
  let vs;
  try { vs = await (await fetch('/api/vault/status')).json(); }
  catch { S.online = false; el('offline').hidden = false; el('lock').hidden = true; return renderAll(); }
  S.online = true; el('offline').hidden = true;
  if (!vs.exists || vs.legacy) return showLock('create', vs.legacy);
  if (!vs.session) return showLock('unlock');
  el('lock').hidden = true; S.locked = false; await loadState();
}
let telSince = 0;
async function pollTelemetry() {
  if (S.locked || !S.online || !S.st) return;
  try {
    const t = await api('/api/telemetry?since=' + telSince); NX.applyTelemetry(t);
    for (const e of t.events) { telSince = Math.max(telSince, e.id); log(e.text, e.cls, e.t); }
  } catch (err) { /* the next poll tries again; nothing is invented in the meantime */ }
}
setInterval(pollTelemetry, 2500);
async function loadState() {
  telSince = 0;
  try { S.st = await api('/api/state'); S.online = true; S.locked = false; }
  catch { return renderAll(); }
  applyVoiceSettings(); applyFocus(); renderAll(); refreshMatrix();
  el('vaultNote').innerHTML = 'Vault: <b>unlocked</b>' + (S.st.settings.auto_lock_minutes ? ' · auto-lock ' + S.st.settings.auto_lock_minutes + ' min' : '');
  if (S.st.consent && S.st.consent.needs_prompt) openConsent();
  pollTelemetry();
  if (S.st.settings.voice_control) startVC(true);
}
function renderAll() { renderChats(); renderModelSel(); renderMode(); renderMatrix(); renderFiles(); renderWeb(); linkNow(); if (typeof renderSecbar === 'function') renderSecbar(); }
function setLink(status, label) { el('linkDot').className = 'dot' + (status ? ' ' + status : ''); el('modelLabel').textContent = label; el('modelLabel').title = label; }
function linkNow() {
  if (!S.online) return setLink('', 'OFFLINE');
  if (S.locked || !S.st) return setLink('', 'SEALED');
  if (S.scripted) return setLink('', 'SCRIPTED');
  const s = S.st.settings;
  setLink(S.ctl ? 'busy' : 'on', s.mode === 'council' ? 'COUNCIL' : s.active_provider + ':' + s.active_model);
}

/* ================= model selector, mode, web, focus ================= */
function options(localOnly) {
  const out = [], cache = S.st.settings.model_cache || {};
  for (const p of S.st.providers) {
    if (p.kind !== 'llm' || !p.enabled || (localOnly && !p.local)) continue;
    const ms = new Set(cache[p.id] || []); if (p.model) ms.add(p.model);
    for (const m of [...ms].sort()) out.push({ ref: p.id + ':' + m, pid: p.id, pname: p.name, model: m, local: p.local });
  }
  return out;
}
function renderModelSel() {
  const sel = el('modelSel');
  if (!S.online || !S.st) { sel.innerHTML = '<option>' + (S.online ? 'sealed' : 'offline') + '</option>'; sel.disabled = true; return; }
  sel.disabled = false;
  const s = S.st.settings, council = s.mode === 'council';
  const cur = council && s.council_lead ? s.council_lead : s.active_provider + ':' + s.active_model;
  const opts = options(s.mode === 'agent'), groups = {};
  opts.forEach(o => (groups[o.pname] = groups[o.pname] || []).push(o));
  let h = '';
  for (const g in groups) h += `<optgroup label="${ea(g)}">` + groups[g].map(o => `<option value="${ea(o.ref)}"${o.ref === cur ? ' selected' : ''}>${esc(o.model)}</option>`).join('') + '</optgroup>';
  if (!opts.some(o => o.ref === cur)) h = `<option value="${ea(cur)}" selected>${esc(cur)}</option>` + h;
  sel.innerHTML = h;
  el('selLabel').textContent = council ? 'ORCHESTRATOR' : '';
  el('rosterBtn').hidden = !council;
  sel.title = council ? 'Any model, local or cloud, can orchestrate the council' : 'Model';
}
el('modelSel').addEventListener('change', async e => {
  const [pid, model] = split(e.target.value);
  if (S.st.settings.mode === 'council') await saveSettings({ council_lead: e.target.value });
  else await saveSettings({ active_provider: pid, active_model: model });
  linkNow();
});
function renderMode() {
  const m = S.online && S.st ? S.st.settings.mode : 'direct';
  document.querySelectorAll('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.mode === m));
  const n = el('modeNote'); let t = '', warn = false;
  if (S.online && S.st && m === 'council') {
    const k = S.st.settings.council_members.length;
    t = `COUNCIL — the orchestrator plans, ${k} member${k === 1 ? '' : 's'} build in parallel, files are checked and repaired. Cloud members receive this task.`;
  }
  if (S.online && S.st && m === 'agent') {
    const a = S.st.settings.agent_approval;
    t = `AGENT — local models only. Approval: ${a.toUpperCase()} · max ${S.st.settings.agent_max_steps} steps.`;
    if (a === 'auto') { t = '⚠ AUTO MODE — the agent writes files and runs commands WITHOUT asking. Local model only.'; warn = true; }
  }
  n.hidden = !t; n.textContent = t; n.className = 'modenote' + (warn ? ' warn' : '');
}
async function setModeUI(m) {
  if (!S.online || !S.st) return false;
  const patch = { mode: m }, s = S.st.settings;
  if (m === 'agent') {
    const cur = prov(s.active_provider);
    if (!cur || !cur.local || !cur.enabled) {
      const first = options(true)[0];
      if (!first) { toast('Agent mode needs an enabled local model (Ollama, LM Studio or llama.cpp). Open SYSTEMS → PROVIDERS.', 'hot'); return false; }
      patch.active_provider = first.pid; patch.active_model = first.model;
    }
  }
  await saveSettings(patch); renderModelSel(); renderMode(); linkNow(); log('mode → ' + m, 'ok'); return true;
}
el('modeSeg').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { if (!S.online || !S.st) return addMsg('SYS', S.online ? 'Unlock the vault first.' : OFFLINE, 'sys'); setModeUI(b.dataset.mode); } });
el('rosterBtn').addEventListener('click', () => openSystems('council'));
const WEB = { auto: ['AUTO', 'Web research: AUTO — searches and reads pages when a question needs fresh facts'], on: ['ON', 'Web research: ON for every message'], off: ['OFF', 'Web research: OFF'] };
function renderWeb() {
  const m = S.st ? S.st.settings.web_mode : 'auto', b = el('webBtn');
  b.dataset.m = m; el('webTag').textContent = WEB[m][0]; b.title = WEB[m][1];
}
el('webBtn').addEventListener('click', async () => {
  if (!S.st) return;
  const next = { auto: 'on', on: 'off', off: 'auto' }[S.st.settings.web_mode];
  await saveSettings({ web_mode: next }); renderWeb(); log('web research ' + next, 'ok');
});
function applyFocus() { document.body.classList.toggle('focus', !!(S.st && S.st.settings.ui_focus)); }
el('focusBtn').addEventListener('click', async () => {
  if (!S.st) return document.body.classList.toggle('focus');
  await saveSettings({ ui_focus: !S.st.settings.ui_focus }); applyFocus();
});

/* ================= provider matrix + archive ================= */
async function refreshMatrix() {
  if (!S.online || S.locked) return;
  try { S.matrix = (await api('/api/matrix')).matrix; renderMatrix(); } catch { }
}
function renderMatrix() {
  const box = el('caps');
  if (!S.online) { box.innerHTML = '<div class="empty" style="grid-column:1/-1">Providers unavailable.<br>Run locally to enable models and memory.</div>'; return; }
  if (!S.st) { box.innerHTML = ''; return; }
  const cls = { 'ONLINE': 's-online', 'NO KEY': 's-nokey', 'OFFLINE': 's-offline', 'RATE-LIMITED': 's-rate' };
  const show = S.matrix.filter(m => m.enabled || m.status !== 'NO KEY');
  box.innerHTML = show.length ? show.map(m =>
    `<div class="cap${m.status === 'ONLINE' ? ' on' : ''}${m.enabled ? '' : ' dim'}" title="${ea(m.name + ' · ' + m.kind + (m.enabled ? '' : ' (disabled)'))}"><b>${esc(m.name.replace(/ \((local|free|voice|no key)\)/i, ''))}</b><span class="st ${cls[m.status] || ''}">${esc(m.status)}</span></div>`).join('')
    : '<div class="empty" style="grid-column:1/-1">Nothing connected yet.<br>Paste an API key in SYSTEMS → PROVIDERS.</div>';
}
function renderChats() {
  const ul = el('chats'), list = S.online && S.st ? S.st.conversations : [];
  if (!list.length) { ul.innerHTML = `<li class="empty">${S.online ? (S.st ? 'No chats yet.' : 'Vault sealed.') : 'Archive offline.'}</li>`; return; }
  ul.innerHTML = list.map(c => `<li class="chat${c.id === S.conv ? ' on' : ''}${c.pinned ? ' pin' : ''}" data-id="${c.id}" tabindex="0"><span class="ct" title="${ea(c.title)}">${esc(c.title)}</span><span class="ca"><button type="button" data-a="pin" title="${c.pinned ? 'Unpin' : 'Pin'}">★</button><button type="button" data-a="ren" title="Rename">✎</button><button type="button" data-a="del" title="Delete">✕</button></span></li>`).join('');
}
async function refreshChats() {
  if (!S.online || !S.st) return;
  try { S.st.conversations = (await api('/api/conversations?q=' + encodeURIComponent(el('chatSearch').value.trim()))).conversations; renderChats(); } catch { }
}
let searchT; el('chatSearch').addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(refreshChats, 200); });
el('newChat').addEventListener('click', () => newChat());
el('chats').addEventListener('click', async e => {
  const li = e.target.closest('.chat'); if (!li) return;
  const id = +li.dataset.id, a = e.target.closest('button')?.dataset.a, c = S.st.conversations.find(x => x.id === id);
  if (a === 'pin') { S.st.conversations = (await api('/api/conversation/update', { id, pinned: !c.pinned })).conversations; return refreshChats(); }
  if (a === 'ren') { const t = prompt('Rename transmission', c.title); if (t !== null) { await api('/api/conversation/update', { id, title: t }); await refreshChats(); } return; }
  if (a === 'del') { if (!confirm(`Delete "${c.title}" for good? Its messages, files and records are erased.`)) return; await api('/api/conversation/delete', { id }); if (S.conv === id) newChat(true); return refreshChats(); }
  loadConv(id);
});
el('chats').addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.classList.contains('chat')) loadConv(+e.target.dataset.id); });
function newChat(silent) {
  if (S.ctl) return;
  S.conv = null; S.files = []; tx.innerHTML = ''; renderChats(); renderFiles();
  if (!silent) addMsg('SYS', 'New transmission opened.', 'sys');
}
async function loadConv(id) {
  if (S.ctl || !S.online || !S.st) return;
  const j = await api('/api/conversation?id=' + id);
  S.conv = id; tx.innerHTML = ''; S.files = j.files;
  j.messages.forEach(addHistory);
  renderChats(); renderFiles(); scroll(true); log('archive loaded · #' + id, 'ok');
}

/* ================= rendering ================= */
const inline = s => esc(s).replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
function block(info, code) {
  const fm = info.match(/(?:^|\s)file:(.+)$/);
  if (fm) return `<div class="fileblk"><div class="fh"><span>FILE · ${esc(fm[1].trim())}</span></div><pre><code>${esc(code)}</code></pre></div>`;
  return `<div class="codeblk"><div class="fh"><span>${esc(info || 'code')}</span><button type="button" class="savebtn">SAVE AS FILE</button></div><pre><code>${esc(code)}</code></pre></div>`;
}
function rich(text) {
  const lines = text.split('\n'), out = []; let para = [], i = 0;
  const flush = () => { if (para.length) { out.push(inline(para.join('\n'))); para = []; } };
  while (i < lines.length) {
    const m = lines[i].match(/^\s{0,3}(`{3,})([^`]*)$/);
    if (!m) { para.push(lines[i]); i++; continue; }
    flush();
    const close = new RegExp('^\\s{0,3}`{' + m[1].length + ',}\\s*$'), body = []; let j = i + 1;
    while (j < lines.length && !close.test(lines[j])) body.push(lines[j++]);
    out.push(block(m[2].trim(), body.join('\n'))); i = j + 1;
  }
  flush(); return out.join('');
}
function mkAi() {
  const root = document.createElement('div'); root.className = 'msg ai';
  root.innerHTML = '<div class="who">NYXOR</div><div class="body"><div class="extra"></div><div class="txt"></div><div class="foot"></div></div>';
  tx.appendChild(root); scroll();
  return { root, extra: root.querySelector('.extra'), txt: root.querySelector('.txt'), foot: root.querySelector('.foot'), raw: '', trace: '', sources: [] };
}
function paint(m, cursor) {
  let h = '';
  if (S.st && S.st.settings.show_think && m.trace.trim()) h += `<span class="trace">${esc(m.trace.trim())}</span>`;
  h += rich(m.raw.replace(/^\s+/, '')) + (cursor ? '<span class="cursor"></span>' : '');
  m.txt.innerHTML = h; scroll();
}
tx.addEventListener('click', async e => {
  const b = e.target.closest('.savebtn');
  if (b) {
    const name = prompt('Save as file (relative path, e.g. src/app.py)', 'snippet.txt'); if (!name) return;
    try {
      if (!S.conv) S.conv = (await api('/api/conversation/new', {})).id;
      const r = await api('/api/files/save', { conv_id: S.conv, path: name, content: b.closest('.codeblk').querySelector('code').textContent });
      await refreshFiles(); await refreshChats(); b.textContent = 'SAVED ✓'; log('file saved · ' + r.file.path, 'ok');
    } catch (err) { addMsg('SYS', 'Could not save file: ' + err.message, 'sys'); }
  }
});

/* ---- sources + in-app reader: web pages never open in a new tab ---- */
function renderSources(box, sources) {
  box.querySelectorAll('.srcs').forEach(x => x.remove());
  if (!sources || !sources.length) return;
  const d = document.createElement('div'); d.className = 'srcs';
  d.innerHTML = '<div class="sh">SOURCES — read them here</div>' + sources.map((s, i) =>
    `<button type="button" class="src${s.ok ? '' : ' nopage'}" data-i="${i}"><b>[${i + 1}]</b><span class="t">${esc(s.title || s.url)}</span><span class="d">${esc(domain(s.url))}${s.date ? ' · ' + esc(s.date) : ''}</span></button>`).join('');
  d.addEventListener('click', e => { const b = e.target.closest('.src'); if (b) openReader(sources[+b.dataset.i]); });
  box.appendChild(d);
}
let readerUrl = '';
function openReader(s) {
  el('viewTitle').innerHTML = '<span>▤</span>' + esc((s.title || 'Source').slice(0, 90));
  const m = el('viewMeta'); m.hidden = false; m.textContent = domain(s.url) + (s.date ? ' · ' + s.date : '') + ' · ' + s.url;
  const body = el('viewBody'); body.className = 'viewer reader';
  body.textContent = s.ok && s.text ? s.text : (s.snippet || 'No text was captured for this source.') + (s.ok ? '' : '\n\n(NYXOR could not open this page, so only the search snippet is available.)');
  readerUrl = s.url; el('viewCopy').hidden = false; el('viewModal').hidden = false;
}
el('viewCopy').addEventListener('click', () => { navigator.clipboard && navigator.clipboard.writeText(readerUrl); el('viewCopy').textContent = 'COPIED'; setTimeout(() => { el('viewCopy').textContent = 'COPY LINK'; }, 1200); });
function researchCard(ai, ev) {
  const d = document.createElement('div'); d.className = 'rcard';
  d.innerHTML = `<div class="rh"><span>WEB RESEARCH · ${esc(ev.provider)}</span><span class="rs">SEARCHING…</span></div><div class="rq">${esc(ev.query)}</div>${ev.queries && ev.queries.length > 1 ? `<div class="rq">angles: ${esc(ev.queries.slice(1).join(' · '))}</div>` : ''}<div class="rlist"></div>`;
  ai.extra.appendChild(d); ai.rc = d; scroll();
}
function researchStatus(ai, t) { if (ai.rc) ai.rc.querySelector('.rs').textContent = t; }

/* ---- files ---- */
function fileChip(f, cid) {
  const d = document.createElement('span'); d.className = 'fchip';
  const html = /\.html?$/i.test(f.path);
  d.innerHTML = `<b title="${ea(f.path)}">${esc(f.path)}</b><a href="/api/file?conv=${cid}&path=${encodeURIComponent(f.path)}" download>DOWNLOAD</a><button type="button" data-a="view">VIEW</button>` + (html ? '<button type="button" data-a="prev">▶ PREVIEW</button>' : '');
  d.querySelector('[data-a=view]').onclick = () => viewFile(cid, f.path);
  if (html) d.querySelector('[data-a=prev]').onclick = () => openPreview(cid, f.path);
  return d;
}
function chipRow(files, cid) { const d = document.createElement('div'); d.className = 'fchips'; files.forEach(f => d.appendChild(fileChip(f, cid))); return d; }
function openPreview(cid, path) {
  el('prevTitle').innerHTML = '<span>▶</span>' + esc(path);
  el('prevFrame').src = `/api/preview?conv=${cid}&path=${encodeURIComponent(path)}`; el('prevModal').hidden = false;
  setTimeout(() => el('prevFrame').focus(), 150);
}
function closePreview() { el('prevModal').hidden = true; el('prevFrame').src = 'about:blank'; }
el('prevClose').onclick = closePreview;
el('prevReload').onclick = () => { const f = el('prevFrame'); const u = f.src; f.src = 'about:blank'; setTimeout(() => { f.src = u; }, 60); };
async function viewFile(cid, path) {
  el('viewMeta').hidden = true; el('viewCopy').hidden = true; el('viewBody').className = 'viewer';
  el('viewTitle').innerHTML = '<span>▤</span>' + esc(path); el('viewBody').textContent = 'loading…'; el('viewModal').hidden = false;
  try { const r = await fetch(`/api/file?conv=${cid}&path=${encodeURIComponent(path)}&view=1`); el('viewBody').textContent = r.ok ? await r.text() : 'File not found.'; }
  catch (e) { el('viewBody').textContent = e.message; }
}
async function refreshFiles() {
  if (!S.online || !S.st || !S.conv) { S.files = []; return renderFiles(); }
  try { S.files = (await api('/api/files?conv=' + S.conv)).files; } catch { }
  renderFiles();
}
function renderFiles() {
  el('fileCount').textContent = S.files.length;
  const l = el('filesList'); l.innerHTML = S.files.length ? '' : '<div class="empty">No files in this chat yet. Ask NYXOR to build something, or attach some.</div>';
  S.files.forEach(f => {
    const row = fileChip(f, S.conv);
    const del = document.createElement('button'); del.type = 'button'; del.textContent = 'DELETE'; del.dataset.a = 'del'; row.appendChild(del);
    del.onclick = async () => {
      if (!confirm(`Delete ${f.path}? It is removed from disk and from the database.`)) return;
      try { S.files = (await api('/api/file/delete', { conv: S.conv, path: f.path })).files; renderFiles(); log('file deleted · ' + f.path, 'hot'); } catch (e) { toast(e.message, 'hot'); }
    };
    l.appendChild(row);
  });
  const z = el('zipAll'); z.hidden = !S.files.length; z.href = '/api/zip?conv=' + S.conv;
}
function showRt(name) {
  document.querySelectorAll('.rtabs button').forEach(b => b.classList.toggle('on', b.dataset.rt === name));
  el('log').hidden = name !== 'log'; el('filesPane').hidden = name !== 'files';
}
document.querySelector('.rtabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) showRt(b.dataset.rt); });
el('viewClose').onclick = () => { el('viewModal').hidden = true; };

/* ================= council + agent widgets ================= */
function councilRows(box, members, notice) {
  box.insertAdjacentHTML('beforeend', notice ? '<div class="cnotice">☁ Cloud members receive this task.</div>' : '');
  return members.map(m => {
    const r = document.createElement('div'); r.className = 'mrow pending'; r.dataset.i = m.idx;
    r.innerHTML = `<div class="mh"><b>${esc(m.ref)}${m.role ? ' · ' + esc(m.role) : ''} · ${m.local ? 'local' : 'cloud'}</b><span class="ms">PENDING</span></div><div class="mt">waiting for the orchestrator's plan…</div><details><summary>OUTPUT</summary><pre class="mo"></pre></details>`;
    box.appendChild(r); return r;
  });
}
function setRow(r, o) {
  if (!r) return;
  if (o.status) { r.className = 'mrow ' + o.status; r.querySelector('.ms').textContent = o.status.toUpperCase(); }
  if (o.sub !== undefined) r.querySelector('.mt').textContent = o.sub;
  if (o.err) r.querySelector('.mo').textContent = '⚠ ' + o.err;
}
function lightNode(i, on) { const n = NX.lnodes[(i * 3 + 2) % NX.lnodes.length]; if (n) { n.hot = on; if (on) n.owned = true; } }
function cline(ai, text) { const d = document.createElement('div'); d.className = 'cnotice'; d.textContent = text; ai.extra.appendChild(d); scroll(); }
function validationLine(ev) {
  const d = document.createElement('div'); d.className = 'cnotice';
  d.textContent = ev.issues.length ? `CHECK ${ev.round}: ${ev.issues.length} problem(s) — ` + ev.issues.slice(0, 3).map(i => `${i.path}: ${i.problem}`).join(' · ') : `CHECK ${ev.round}: all files passed`;
  return d;
}
function diffHtml(t) {
  if (!/^(--- |NEW FILE)/.test(t)) return esc(t);
  return t.split('\n').map(l => `<span class="${l.startsWith('@@') ? 'hunk' : l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : ''}">${esc(l)}</span>`).join('');
}
function toolCard(ev) {
  const d = document.createElement('div'); d.className = 'tool'; d.dataset.run = ev.run_id; d.dataset.tool = ev.tool;
  d.innerHTML = `<div class="th"><span>${esc(ev.tool.toUpperCase())}</span><em class="stt">${ev.needs_approval ? 'AWAITING APPROVAL' : 'RUNNING'}</em></div><div class="ts2">${esc(ev.summary)}</div>` +
    (ev.detail ? `<pre class="${/^(--- |NEW FILE)/.test(ev.detail) ? 'diff' : ''}">${diffHtml(ev.detail)}</pre>` : '') +
    (ev.needs_approval ? '<div class="btns"><button type="button" class="allow" data-d="allow">ALLOW</button><button type="button" data-d="deny">DENY</button>' + (isStore() ? '' : '<button type="button" data-d="allow_all">ALLOW ALL FOR THIS TASK</button>') + '</div>' : '');
  d.querySelector('.btns')?.addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    d.querySelectorAll('button').forEach(x => (x.disabled = true));
    d.querySelector('.stt').textContent = b.dataset.d === 'deny' ? 'DENIED' : 'APPROVED';
    try { await api('/api/agent/decision', { task_id: S.task, run_id: ev.run_id, decision: b.dataset.d }); } catch (err) { addMsg('SYS', 'Approval failed: ' + err.message, 'sys'); }
  });
  return d;
}

/* ================= messages + history ================= */
function addUserMsg(text, items) {
  const root = document.createElement('div'); root.className = 'msg user';
  root.innerHTML = '<div class="who">YOU</div><div class="body"></div>';
  const body = root.querySelector('.body');
  const imgs = items.filter(i => i.kind === 'image');
  if (imgs.length) { const t = document.createElement('div'); t.className = 'thumbs'; imgs.forEach(i => { const im = document.createElement('img'); im.src = i.src; im.alt = i.name; t.appendChild(im); }); body.appendChild(t); }
  const files = items.filter(i => i.kind !== 'image');
  if (files.length) { const f = document.createElement('div'); f.className = 'fchips'; files.forEach(i => { const s = document.createElement('span'); s.className = 'fchip'; s.innerHTML = '<b>' + esc(i.name) + '</b>'; f.appendChild(s); }); body.appendChild(f); }
  if (text) { const t = document.createElement('div'); t.textContent = text; body.appendChild(t); }
  tx.appendChild(root); scroll(true);
}
function metaLine(ai, meta, mid) {
  if (!meta || !meta.model) return;
  const s = document.createElement('span'); s.className = 'meta';
  s.textContent = `${meta.model} · ${meta.toks || 0} tok · ${meta.tps || 0} tok/s` + (meta.council ? ` · council of ${meta.council.members.length}` : '') + (meta.sources ? ` · ${meta.sources.length} sources` : '')
    + (meta.route ? ` · ${meta.route.auto ? 'auto-routed (' + meta.route.complexity + (meta.route.private ? ', private' : '') + ')' : 'manual'}` + (meta.route.fallbacks && meta.route.fallbacks.length ? ` · fallback from ${meta.route.fallbacks.map(f => f.from).join(' → ')}` : '') : '');
  ai.foot.appendChild(s);
  const rb = document.createElement('button'); rb.type = 'button'; rb.className = 'rep'; rb.textContent = 'REPORT'; rb.title = 'Report a problem with this answer';
  rb.onclick = () => openReport(mid);
  ai.foot.appendChild(rb);
}
function addHistory(m) {
  if (m.role === 'user') {
    const items = ((m.meta && m.meta.attachments) || []).map(a => a.kind === 'image' ? { kind: 'image', name: a.name, src: `/api/file?conv=${S.conv}&path=${encodeURIComponent(a.path)}&img=1` } : { kind: 'file', name: a.name });
    return addUserMsg(m.content === '[attachment]' ? '' : m.content, items);
  }
  if (m.role === 'system') return addMsg('SYS', m.content, 'sys');
  if (m.role === 'tool') {
    const d = document.createElement('div'); d.className = 'msg sys';
    const [head, ...rest] = m.content.split('\n');
    d.innerHTML = `<div class="who">TOOL</div><div class="body"><div class="tool ${esc(m.meta.status || '')}"><div class="th"><span>${esc((m.meta.tool || '').toUpperCase())}</span><em>${esc((m.meta.status || '').toUpperCase())}</em></div><div class="ts2">${esc(head)}</div><details><summary>OUTPUT</summary><pre>${esc(rest.join('\n'))}</pre></details></div></div>`;
    tx.appendChild(d); return;
  }
  const ai = mkAi(); ai.raw = m.content; ai.trace = m.reasoning || ''; paint(ai, false);
  const c = m.meta && m.meta.council;
  if (c) {
    const rows = councilRows(ai.extra, c.members.map((x, i) => ({ idx: i, ref: x.ref, role: x.role, local: false })), false);
    c.members.forEach((x, i) => {
      setRow(rows[i], { status: x.status === 'done' ? 'done' : 'failed', sub: x.sub + (x.files && x.files.length ? '  → ' + x.files.join(', ') : ''), err: x.error });
      if (x.out) rows[i].querySelector('.mo').textContent = x.out;
    });
  }
  if (m.meta && m.meta.files && m.meta.files.length) ai.foot.appendChild(chipRow(m.meta.files, S.conv));
  metaLine(ai, Object.assign({ model: m.model }, m.meta), m.id);
  if (m.meta && m.meta.sources) renderSources(ai.foot, m.meta.sources);
}

/* ================= composer: textarea, attachments ================= */
const input = el('input');
const autoGrow = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; };
input.addEventListener('input', autoGrow);
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); el('prompt').requestSubmit(); } });
const IMG_RE = /^image\/(png|jpe?g|gif|webp)$/;
const toB64 = blob => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(blob); });
async function prepImage(f) {
  let blob = f, name = f.name || 'image.png';
  if (f.type !== 'image/gif' && f.size > 1.2 * 2 ** 20 && window.createImageBitmap) {
    try {
      const bm = await createImageBitmap(f), k = Math.min(1, 1600 / Math.max(bm.width, bm.height));
      const cv = document.createElement('canvas'); cv.width = Math.round(bm.width * k); cv.height = Math.round(bm.height * k);
      cv.getContext('2d').drawImage(bm, 0, 0, cv.width, cv.height);
      blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', 0.85)); name = name.replace(/\.\w+$/, '') + '.jpg';
    } catch { blob = f; }
  }
  return { name, b64: await toB64(blob), kind: 'image', src: URL.createObjectURL(blob), size: blob.size };
}
async function addFiles(files) {
  for (const f of files) {
    if (S.atts.length >= 6) { toast('Only 6 attachments per message.', 'hot'); break; }
    if (f.size > 10 * 2 ** 20 && !IMG_RE.test(f.type)) { toast(f.name + ' is larger than 10 MB.', 'hot'); continue; }
    try { S.atts.push(IMG_RE.test(f.type) ? await prepImage(f) : { name: f.name, b64: await toB64(f), kind: 'file', size: f.size }); }
    catch { toast('Could not read ' + f.name, 'hot'); }
  }
  renderTray();
}
function renderTray() {
  const t = el('tray'); t.hidden = !S.atts.length; t.innerHTML = '';
  S.atts.forEach((a, i) => {
    const d = document.createElement('div'); d.className = 'att';
    d.innerHTML = (a.kind === 'image' ? `<img src="${a.src}" alt="">` : `<div class="ico">${esc((a.name.split('.').pop() || 'file').slice(0, 4).toUpperCase())}</div>`) + `<span class="n" title="${ea(a.name)}">${esc(a.name)}</span><button type="button" aria-label="Remove">✕</button>`;
    d.querySelector('button').onclick = () => { S.atts.splice(i, 1); renderTray(); };
    t.appendChild(d);
  });
}
el('attachBtn').addEventListener('click', () => el('fileIn').click());
el('fileIn').addEventListener('change', async e => { await addFiles([...e.target.files]); e.target.value = ''; });
const cons = el('console');
['dragenter', 'dragover'].forEach(ev => cons.addEventListener(ev, e => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.preventDefault(); cons.classList.add('drop'); } }));
['dragleave', 'drop'].forEach(ev => cons.addEventListener(ev, e => { if (ev === 'dragleave' && cons.contains(e.relatedTarget)) return; cons.classList.remove('drop'); }));
cons.addEventListener('drop', e => { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); addFiles([...e.dataTransfer.files]); } });
input.addEventListener('paste', e => { const fs = [...(e.clipboardData ? e.clipboardData.files : [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } });

/* ================= sending ================= */
function setSend(on) { const b = el('prompt').querySelector('.send'); b.classList.toggle('abort', on); b.textContent = on ? 'ABORT' : 'TRANSMIT'; }
function abortRun() { if (!S.ctl) return; const t = S.task; S.ctl.abort(); if (t) api('/api/abort', { task_id: t }).catch(() => { }); NX.stopSpeech(); }
el('prompt').querySelector('.send').addEventListener('click', e => { if (S.ctl) { e.preventDefault(); abortRun(); } });

async function sendModel(it) {
  const s = S.st.settings, ctl = new AbortController();
  S.ctl = ctl; state.busy = true; setSend(true); linkNow(); NX.resetSpeech();
  NX.setMode('thinking'); state.target = .45; el('stateLabel').textContent = 'WORKING';
  let ai = mkAi(), dirty = false, aborted = false, members = [], failed = false;
  paint(ai, true);
  const sched = () => { if (!dirty) { dirty = true; requestAnimationFrame(() => { dirty = false; if (ai) paint(ai, true); }); } };
  const sever = () => { if (aborted) return; aborted = true; ai.foot.insertAdjacentHTML('beforeend', '<span class="meta">· transmission severed by operator</span>'); log('transmission aborted', 'hot'); };
  const handle = ev => {
    switch (ev.type) {
      case 'start': S.conv = ev.conv_id; S.task = ev.task_id; log(`${ev.mode} transmission started`, 'ok'); break;
      case 'token': if (ai.rc && !ai.raw) researchStatus(ai, 'WRITING…'); ai.raw += ev.text; NX.setMode('speaking'); state.speaking = true; state.target = .75 + Math.random() * .25; NX.speakStream(ev.text); sched(); break;
      case 'reasoning': ai.trace += ev.text; NX.setMode('reasoning'); state.target = .6; sched(); break;
      case 'notice': addMsg('SYS', ev.text, 'sys'); break;
      case 'route': cline(ai, 'ROUTE: ' + ev.line + (ev.excluded && ev.excluded.length ? ' · skipped: ' + ev.excluded.map(x => x.ref).join(', ') : '')); log('routed to ' + ev.chosen, 'ok'); break;
      case 'fallback': cline(ai, 'FALLBACK: ' + ev.line); log('fallback to ' + ev.to, 'hot'); break;
      case 'error': {
        failed = true; const body = addMsg('SYS', ev.message, 'sys'); log('request failed', 'hot');
        if (ev.kind === 'budget' && ev.action === 'ask' && ev.provider && body) {      // budget action "ask": the refusal comes with a button
          const b = document.createElement('button'); b.type = 'button'; b.className = 'ghost-btn sm'; b.textContent = 'ALLOW ' + ev.provider.toUpperCase() + ' TODAY'; b.style.marginLeft = '8px';
          b.onclick = async () => { try { await api('/api/budget/override', { provider: ev.provider }); b.disabled = true; b.textContent = 'ALLOWED FOR TODAY — SEND AGAIN'; log('budget override for ' + ev.provider, 'hot'); } catch (err) { b.textContent = 'FAILED: ' + err.message; } };
          body.appendChild(b);
        }
        break;
      }
      case 'files': ai.foot.appendChild(chipRow(ev.files, S.conv)); refreshFiles(); log(`${ev.files.length} file(s) saved`, 'ok'); break;
      case 'aborted': sever(); break;
      case 'research_start': researchCard(ai, ev); NX.setMode('researching'); state.target = .55; log('web research: ' + ev.query.slice(0, 40), 'hot'); break;
      case 'search':
        ai.sources = ev.results.map(r => Object.assign({ ok: false, text: '' }, r));
        if (ai.rc) { ai.rc.querySelector('.rlist').innerHTML = ev.results.map((r, i) => `<div class="rp" data-i="${i}"><i>…</i><span>${esc(r.title)} — ${esc(domain(r.url))}</span></div>`).join(''); researchStatus(ai, `READING ${ev.results.length} PAGES…`); }
        break;
      case 'research_page': {
        const sr = ai.sources[ev.i]; if (sr) { sr.ok = ev.ok; sr.text = ev.text; if (ev.title) sr.title = ev.title; }
        const row = ai.rc && ai.rc.querySelector(`.rp[data-i="${ev.i}"]`);
        if (row) { row.className = 'rp ' + (ev.ok ? 'ok' : 'bad'); row.querySelector('i').textContent = ev.ok ? '✓' : '✗'; row.querySelector('span').textContent = (ev.title || ev.url) + ' — ' + (ev.ok ? ev.chars + ' chars read' + (ev.via === 'reader' ? ' via reader' : '') + (ev.date ? ' · ' + ev.date.slice(0, 10) : '') : ev.error); }
        break;
      }
      case 'step_start':
        if (ai.raw || ai.foot.children.length) { paint(ai, false); ai = mkAi(); paint(ai, true); }
        NX.setMode('thinking'); log(`agent step ${ev.n}/${ev.max}`, 'ok'); break;
      case 'council_start': members = councilRows(ai.extra, ev.members, ev.members.some(m => !m.local)); NX.setMode('convening'); log('council convened', 'hot'); break;
      case 'council_plan':
        ev.subtasks.forEach((t, i) => setRow(members[i], { sub: (ev.titles && ev.titles[i] ? ev.titles[i] + ': ' : '') + t + (ev.files && ev.files[i] && ev.files[i].length ? '  → ' + ev.files[i].join(', ') : '') }));
        if (ev.spec) { const d = document.createElement('details'); d.className = 'mrow'; d.innerHTML = `<summary class="ms">SHARED SPEC</summary><pre class="mo">${esc(ev.spec)}</pre>`; ai.extra.insertBefore(d, members[0] || null); }
        if (ev.fallback) addMsg('SYS', 'The orchestrator could not produce a valid plan: every member receives the whole task.', 'sys'); break;
      case 'member_status': setRow(members[ev.idx], { status: ev.status, err: ev.error }); lightNode(ev.idx, ev.status === 'running'); log(`council member ${ev.idx + 1} ${ev.status}`, ev.status === 'failed' ? 'hot' : 'ok'); break;
      case 'member_token': { const o = members[ev.idx] && members[ev.idx].querySelector('.mo'); if (o) o.textContent += ev.text; state.target = .7; break; }
      case 'council_validation': ai.extra.appendChild(validationLine(ev)); break;
      case 'council_strategy': cline(ai, 'STRATEGY: ' + ev.strategy + (ev.recipe ? ' · ' + ev.recipe + ' recipe: every candidate is tested before it can win' : '')); break;
      case 'council_candidate': setRow(members[ev.idx], { sub: ev.line + (ev.problems.length ? ' — ' + ev.problems.join(' · ') : '') }); log(`candidate ${ev.idx + 1}: ${ev.valid ? 'valid' : 'rejected'} (${ev.score})`, ev.valid ? 'ok' : 'hot'); break;
      case 'council_winner': cline(ai, 'WINNER: ' + ev.ref + ' — ' + ev.line); if (ev.idx !== null && members[ev.idx]) members[ev.idx].classList.add('win'); break;
      case 'council_review': cline(ai, `REVIEW (member ${ev.idx + 1}): ` + ev.text.replace(/\s+/g, ' ').slice(0, 220)); break;
      case 'council_repair': cline(ai, `REPAIR ${ev.round}: ${ev.accepted ? 'accepted' : 'REJECTED'} (${ev.before} → ${ev.after}) ${ev.reason || ''}`.slice(0, 260)); log('repair ' + (ev.accepted ? 'accepted' : 'rejected'), ev.accepted ? 'ok' : 'hot'); break;
      case 'council_lead': NX.setMode(ev.status); log('orchestrator ' + ev.status, 'ok'); break;
      case 'tool_request': { const c = toolCard(ev); ai.foot.appendChild(c); scroll(); NX.setMode(ev.needs_approval ? 'awaiting approval' : 'acting'); break; }
      case 'tool_result': {
        const c = tx.querySelector(`.tool[data-run="${ev.run_id}"]`);
        if (c) {
          c.classList.add(ev.status); c.querySelector('.stt').textContent = ev.status.toUpperCase(); c.querySelector('.btns')?.remove();
          c.insertAdjacentHTML('beforeend', `<details><summary>OUTPUT</summary><pre>${esc(ev.output)}</pre></details>`);
          if (c.dataset.tool === 'write_file' && ev.status === 'ok') refreshFiles();
        }
        scroll(); break;
      }
      case 'done': paint(ai, false); NX.speakStream('', true); metaLine(ai, ev.meta, ev.message_id); researchStatus(ai, 'DONE'); S.lastAnswer = ai.raw; break;
    }
  };
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctl.signal,
      body: JSON.stringify({ conv_id: S.conv, text: it.text, mode: s.mode, model_ref: s.active_provider + ':' + s.active_model, attachments: it.atts.map(a => ({ name: a.name, b64: a.b64 })) }),
    });
    if (r.status === 423) { ai.root.remove(); ai = null; S.locked = true; clearUI(); showLock('unlock'); }
    else if (!r.ok) {
      let j = {}; try { j = await r.json(); } catch { }
      ai.root.remove(); ai = null; addMsg('SYS', j.error || 'Request refused (HTTP ' + r.status + ').', 'sys'); log('request refused', 'hot');
    } else {
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { value, done } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let k;
        while ((k = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, k).split('\n').find(l => l.startsWith('data:')); buf = buf.slice(k + 2);
          if (!line) continue;
          try { handle(JSON.parse(line.slice(5))); } catch (e) { console.error(e); }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') sever(); else { addMsg('SYS', 'Link failure: ' + e.message, 'sys'); failed = true; }
  } finally {
    if (ai) {
      paint(ai, false); renderSources(ai.foot, ai.sources.filter(x => x));
      if (!ai.raw && !ai.extra.children.length && !ai.foot.children.length) ai.root.remove();
      else if (!ai.raw && ai.trace.trim() && !s.show_think) ai.txt.innerHTML = `<span class="trace">${esc(ai.trace.trim())}</span>`;
    }
    NX.speakStream('', true); NX.lnodes.forEach(n => (n.hot = false));
    S.ctl = null; S.task = null; setSend(false); state.speaking = false; state.target = .15;
    NX.setMode('listening'); el('stateLabel').textContent = 'READY'; state.busy = false; linkNow();
    if (!S.locked) { refreshChats(); refreshFiles(); refreshMatrix(); }
  }
}

async function drain() {
  if (state.busy || !queue.length) return;
  const it = queue.shift();
  if (S.locked && S.online) { showLock('unlock'); queue.length = 0; return; }
  const slash = it.text.startsWith('/') && !it.atts.length;
  addUserMsg(it.text, it.atts.map(a => ({ kind: a.kind, name: a.name, src: a.src })));
  log(`operator input · ${it.text.length} chars${it.atts.length ? ' · ' + it.atts.length + ' file(s)' : ''}`, 'ok');
  if (!slash && S.online && S.st && !S.scripted) await sendModel(it); else await NX.respond(it.text);
  drain();
}
NX.afterGreeting = drain;
el('prompt').addEventListener('submit', e => {
  e.preventDefault();
  const v = input.value.trim(); if (!v && !S.atts.length) return;
  queue.push({ text: v, atts: S.atts.slice() }); input.value = ''; autoGrow(); S.atts = []; renderTray(); drain();
});
const chipDefs = ['/status', '/memories', '/new', '/video', 'What is happening in the Strait of Hormuz?'];
el('chips').innerHTML = chipDefs.map(c => `<button class="chip" type="button">${esc(c)}</button>`).join('');
el('chips').addEventListener('click', e => { const b = e.target.closest('.chip'); if (b) { input.value = b.textContent; autoGrow(); el('prompt').requestSubmit(); } });

/* ================= commands ================= */
const memText = ms => ms.length ? ms.map(m => `#${m.id}  ${m.fact}`).join('\n') : 'No long-term memories stored. Use /remember <fact>.';
const needSt = () => (!S.online ? OFFLINE : !S.st ? 'The vault is sealed. Unlock it first.' : null);
C.new = () => { if (S.ctl) return 'Finish or ABORT the current transmission first.'; newChat(true); return 'A fresh transmission is open. The archive keeps the old ones.'; };
C.reset = () => C.new();
C.lock = () => { lockNow(); return 'Sealing the vault.'; };
C.mode = async a => {
  const n = needSt(); if (n) return n;
  a = (a || '').toLowerCase();
  if (!['direct', 'council', 'agent'].includes(a)) return `Mode is ${S.st.settings.mode.toUpperCase()}.\nUsage: /mode direct|council|agent`;
  await setModeUI(a); return `Mode: ${S.st.settings.mode.toUpperCase()}.`;
};
C.use = async a => {
  const n = needSt(); if (n) return n;
  const [pid, model] = split((a || '').trim()), p = pid && prov(pid);
  if (!p || p.kind !== 'llm') return `Usage: /use provider:model\nProviders: ${S.st.providers.filter(x => x.kind === 'llm').map(x => x.id).join(', ')}`;
  if (S.st.settings.mode === 'agent' && !p.local) return 'Agent mode is local-only. Switch with /mode direct first.';
  await saveSettings({ active_provider: pid, active_model: model || p.model }); renderModelSel(); linkNow();
  return `Using ${pid}:${model || p.model}.`;
};
C.remember = async a => { const n = needSt(); if (n) return n; if (!a) return 'Usage: /remember <fact>'; const r = await api('/api/memories', { fact: a }); S.st.memories = r.memories; return `Stored as memory #${r.id}. I will not forget it.`; };
C.forget = async a => { const n = needSt(); if (n) return n; const id = parseInt(a); if (!id) return 'Usage: /forget <id>   (see /memories)'; const r = await api('/api/memories/delete', { id }); S.st.memories = r.memories; return r.ok ? `Memory #${id} erased.` : `No memory #${id}.`; };
C.memories = async () => { const n = needSt(); if (n) return n; S.st.memories = (await api('/api/memories')).memories; return 'LONG-TERM MEMORY\n' + memText(S.st.memories); };
C.files = async () => {
  const n = needSt(); if (n) return n;
  await refreshFiles(); showRt('files');
  return S.files.length ? 'FILES IN THIS TRANSMISSION\n' + S.files.map(f => `${f.path}  (${f.size} bytes)`).join('\n') + '\nUse DOWNLOAD / VIEW / DELETE in the FILES tab, or /zip.' : 'No files yet in this chat.';
};
C.zip = async () => { const n = needSt(); if (n) return n; await refreshFiles(); if (!S.files.length) return 'Nothing to archive yet.'; location.href = '/api/zip?conv=' + S.conv; return `Packing ${S.files.length} file(s) into a zip.`; };
C.export = () => { const n = needSt(); if (n) return n; location.href = '/api/export'; return 'Exporting the database as JSON. That file is NOT encrypted (API keys are left out). For an encrypted backup use SYSTEMS → SECURITY.'; };
C.web = async a => {
  const n = needSt(); if (n) return n;
  a = (a || '').toLowerCase();
  if (!['auto', 'on', 'off'].includes(a)) return `Web research is ${S.st.settings.web_mode.toUpperCase()}.\nUsage: /web auto|on|off`;
  await saveSettings({ web_mode: a }); renderWeb(); return `Web research: ${a.toUpperCase()}.`;
};
C.video = a => { const n = needSt(); if (n) return n; openVideo(a || '', false); return a ? 'Video studio opened with your prompt. Press GENERATE.' : 'Video studio opened.'; };
C.listen = async a => {
  a = (a || 'on').toLowerCase();
  if (a === 'off') { stopVC(); if (S.st) saveSettings({ voice_control: false }); return 'Voice control off.'; }
  if (!startVC()) return 'This browser has no speech recognition (use Chrome or Edge).';
  if (S.st) saveSettings({ voice_control: true }); return 'Listening. Say "' + wakeWord() + ', …" followed by a question or command.';
};
C.model = () => S.online && S.st
  ? `NEURAL LINK\nmode ........ ${S.st.settings.mode.toUpperCase()}\nactive ...... ${S.st.settings.active_provider}:${S.st.settings.active_model}\nweb ......... ${S.st.settings.web_mode.toUpperCase()} (${S.st.settings.web_depth})\nscripted .... ${S.scripted ? 'yes' : 'no'}\nhistory ..... last ${S.st.settings.history_turns} turns\nmemories .... ${S.st.memories.length}\n\nOpen SYSTEMS (top right) to change this.`
  : needSt() + '\nThe scripted persona is answering.';
C.systems = C.link = () => { openSystems(); return 'Opening SYSTEMS.'; };
C.online = () => { S.scripted = false; linkNow(); return S.online ? 'Neural link engaged.' : OFFLINE; };
C.offline = () => { S.scripted = true; linkNow(); return 'Neural link disengaged. I will speak with my own scripted voice.'; };
const baseHelp = C.help;
C.help = () => baseHelp().replace('/clear      · clear this transcript', `/clear      · clear this transcript
/new        · open a new chat
/mode       · direct | council | agent
/use        · /use provider:model
/web        · auto | on | off  (live web research)
/video      · open the video studio
/listen     · voice control on|off
/lock       · seal the vault now
/remember   · /remember <fact>
/forget     · /forget <id>
/memories   · list long-term memory
/files /zip · files in this chat / download them
/export     · export the database (plain JSON)
/model      · link status
/systems    · open SYSTEMS (alias /link)
/online /offline · model or scripted persona`);

/* ================= SYSTEMS dialog ================= */
const sysModal = el('sysModal');
function sysStatus(t, cls) { const s = el('sysStatus'); s.textContent = t; s.className = 'modal-status' + (cls ? ' ' + cls : ''); s.hidden = !t; }
const pane = n => document.querySelector(`.pane[data-pane="${n}"]`);
const TABS = () => ({ providers: buildProviders, router: buildRouter, budgets: buildBudgets, privacy: buildPrivacy, council: buildCouncil, agent: buildAgent, persona: buildPersona, web: buildWeb, voice: buildVoice, security: buildSecurity, memory: buildMemory });
function showTab(n) {
  document.querySelectorAll('#sysTabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === n));
  document.querySelectorAll('.pane').forEach(p => (p.hidden = p.dataset.pane !== n));
  if (!S.online || !S.st) { pane(n).innerHTML = `<p class="hint">${S.online ? 'Unlock the vault first.' : OFFLINE}</p>`; return; }
  TABS()[n]();
}
function openSystems(tab) {
  if (!S.st) return toast(S.online ? 'Unlock the vault first.' : OFFLINE, 'hot');
  sysModal.hidden = false; S.built = {}; sysStatus('Provider changes save as you type. Other tabs save with APPLY.'); showTab(tab || 'providers');
}
function closeSystems() { sysModal.hidden = true; input.focus(); }
el('systemsBtn').addEventListener('click', () => openSystems());
el('sysClose').addEventListener('click', closeSystems);
sysModal.addEventListener('click', e => { if (e.target === sysModal) closeSystems(); });
el('sysTabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });
const fldRow = (label, inner) => `<label class="fld"><span>${label}</span>${inner}</label>`;

/* ---- providers: paste any key ---- */
const GROUPS = [['LOCAL MODELS (no key)', p => p.kind === 'llm' && p.is_local], ['CLOUD MODELS', p => p.kind === 'llm' && !p.is_local],
  ['WEB SEARCH TOOLS', p => p.kind === 'search'], ['VIDEO BACK-ENDS', p => p.kind === 'video'], ['VOICE SERVICES', p => p.kind === 'tts']];
function slotHtml(p) {
  const cache = (S.st.settings.model_cache || {})[p.id] || [], llm = p.kind === 'llm', rep = p.protocol === 'replicate' || p.protocol === 'fal', colab = p.protocol === 'colab';
  const keyLbl = colab ? 'Token (from the connect string)' : 'API key' + (p.is_local ? ' (optional)' : '');
  return `<details class="slot${p.enabled ? '' : ' off'}" data-id="${ea(p.id)}"${p.has_key || p.enabled ? ' open' : ''}>
<summary><b>${esc(p.name)}</b><span class="tag ${p.local ? 'local' : 'cloud'}">${p.kind === 'llm' ? (p.local ? 'LOCAL' : 'CLOUD') : p.kind.toUpperCase()}</span>${p.has_key ? '<span class="tag local">KEY ✓</span>' : ''}${p.is_custom ? '<span class="tag">CUSTOM</span>' : ''}<span class="sp"></span>${p.is_custom ? '<button type="button" class="ghost-btn sm del">REMOVE</button>' : ''}</summary>
<div class="slot-b">
<label class="chk" style="grid-column:1/-1"><input type="checkbox" class="en"${p.enabled ? ' checked' : ''}> Enabled</label>
<label class="fld"><span>${colab ? 'Public URL' : 'Base URL'}</span><input class="url" value="${ea(p.base_url)}" spellcheck="false"></label>
<label class="fld"><span>${keyLbl}</span><input class="key" type="password" value="${ea(p.api_key)}" placeholder="${p.is_local ? 'not needed' : 'paste key'}" autocomplete="off"></label>
${llm || rep ? `<label class="fld"><span>Model${rep ? ' id' : ''}</span><input class="mdl" list="dl-${ea(p.id)}" value="${ea(p.model)}" spellcheck="false" placeholder="${rep ? (p.protocol === 'fal' ? 'fal-ai/ltx-video' : 'owner/model-name') : 'press DETECT'}"><datalist id="dl-${ea(p.id)}">${cache.map(m => `<option value="${ea(m)}">`).join('')}</datalist></label>` : ''}
${p.id === 'openrouter' ? `<label class="chk"><input type="checkbox" class="free"${S.st.settings.openrouter_free_only ? ' checked' : ''}> Free models only (ids ending in :free)</label>` : ''}
<div class="slot-a">${llm ? '<button type="button" class="ghost-btn sm det">DETECT</button>' : ''}<button type="button" class="ghost-btn sm tst">TEST</button><span class="ss"></span>${p.key_url ? `<span class="ss">get a key: ${esc(p.key_url.replace(/^https?:\/\//, ''))}</span>` : ''}</div>
</div></details>`;
}
function fillProvList() {
  el('provList').innerHTML = GROUPS.map(([label, f]) => { const ps = S.st.providers.filter(f); return ps.length ? `<div class="grp-h">${label}</div>` + ps.map(slotHtml).join('') : ''; }).join('') +
    `<div class="grp-h">CUSTOM (any OpenAI-compatible URL)</div><div class="slot"><div class="slot-b"><label class="fld"><span>Name</span><input id="cuName" placeholder="My vLLM" spellcheck="false"></label><label class="fld"><span>Base URL</span><input id="cuUrl" placeholder="https://host/v1" spellcheck="false"></label><div class="slot-a"><button type="button" class="ghost-btn sm" id="cuAdd">ADD PROVIDER</button><span class="ss" id="cuSs"></span></div></div></div>`;
}
function buildProviders() {
  pane('providers').innerHTML = `<div class="quick"><div class="qh">PASTE ANY API KEY — NYXOR DOES THE REST</div>
<div class="qrow"><input id="qkKey" type="password" placeholder="sk-… · gsk_… · nvapi-… · AIza… · tvly-… · r8_… · or a Colab connect string" autocomplete="off" spellcheck="false"><button type="button" class="send" id="qkAdd">ADD</button></div>
<div class="ss" id="qkHint">The provider is recognised from the key, connected, tested, and its models are loaded. Works for models (OpenAI, Claude, Grok, Gemini, Groq, NVIDIA, OpenRouter, Mistral, DeepSeek, Together…), web search (Tavily, Brave, Serper, Exa…), video and voice. A key is only ever sent to the provider it belongs to.</div>
<div class="cands" id="qkCands"></div></div><div id="provList"></div>`;
  fillProvList();
}
let detT;
const qkHint = (t, c) => { const h = el('qkHint'); if (h) { h.textContent = t; h.className = 'ss ' + (c || ''); } };
pane('providers').addEventListener('input', e => {
  if (e.target.id !== 'qkKey') return;
  clearTimeout(detT);
  detT = setTimeout(async () => {
    const v = e.target.value.trim(); if (!v) return qkHint('');
    try { const c = (await api('/api/keys/detect', { key: v })).candidates; qkHint(!c.length ? 'Unknown key format — ADD will let you give its API URL.' : c[0].confidence === 'certain' ? 'Looks like ' + c[0].name + ' (' + c[0].kind + ').' : 'Could be: ' + c.map(x => x.name).join(' / ') + ' — ADD will ask which.'); } catch { }
  }, 250);
});
async function addKey(forceProvider, extra) {
  const key = el('qkKey').value.trim(); if (!key) return;
  qkHint('connecting and testing…'); el('qkCands').innerHTML = '';
  try {
    const r = await api('/api/keys/add', Object.assign({ key, provider: forceProvider }, extra || {}));
    if (r.ambiguous) {
      if (r.candidates.length) {
        qkHint('This key could belong to more than one provider. It has NOT been sent anywhere. Which one is it?');
        el('qkCands').innerHTML = r.candidates.map(c => `<button type="button" data-p="${ea(c.id)}">${esc(c.name)}</button>`).join('');
      } else {
        qkHint('Unknown key format. Give the provider\'s OpenAI-compatible API URL to add it as a custom provider:');
        el('qkCands').innerHTML = '<input id="qkName" placeholder="name" style="max-width:130px"><input id="qkUrl" placeholder="https://host/v1"><button type="button" data-p="custom">ADD AS CUSTOM</button>';
      }
      return;
    }
    S.st = await api('/api/state'); renderAll(); fillProvList(); refreshMatrix();
    if (r.ok) { el('qkKey').value = ''; qkHint(`✓ ${r.name} connected` + (r.models ? ` — ${r.models} models, using ${r.model}` : '') + (r.test && r.test.latency_ms ? ` · ${r.test.latency_ms} ms` : '') + (r.message ? ' · ' + r.message : ''), 'ok'); log(r.name + ' connected', 'ok'); }
    else qkHint(`✗ ${r.name || 'Provider'}: ${r.error}${r.removed ? ' (the key was not kept)' : ''}`, 'bad');
  } catch (err) { qkHint(err.message, 'bad'); }
}
pane('providers').addEventListener('click', async e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.id === 'qkAdd') return addKey();
  if (b.closest('#qkCands') && b.dataset.p) return addKey(b.dataset.p, b.dataset.p === 'custom' ? { name: el('qkName').value, base_url: el('qkUrl').value } : {});
  if (b.id === 'cuAdd') {
    try { await api('/api/providers', { name: el('cuName').value.trim() || 'Custom provider', base_url: el('cuUrl').value.trim() }); S.st = await api('/api/state'); fillProvList(); refreshMatrix(); }
    catch (err) { el('cuSs').textContent = err.message; }
    return;
  }
  const slot = b.closest('.slot[data-id]'); if (!slot) return;
  const id = slot.dataset.id, ss = slot.querySelector('.ss'), say = (t, c) => { ss.textContent = t; ss.className = 'ss ' + (c || ''); };
  if (b.classList.contains('del')) { e.preventDefault(); if (!confirm('Remove this provider?')) return; await api('/api/providers/delete', { id }); S.st = await api('/api/state'); fillProvList(); renderModelSel(); refreshMatrix(); return; }
  try {
    await saveSlot(slot);
    if (b.classList.contains('det')) {
      say('detecting…'); const r = await api('/api/providers/models', { id });
      if (!r.ok) return say(r.error, 'bad');
      (S.st.settings.model_cache = S.st.settings.model_cache || {})[id] = r.models;
      const i = S.st.providers.findIndex(p => p.id === id); r.provider.local = S.st.providers[i].local; r.provider.key_url = S.st.providers[i].key_url; S.st.providers[i] = r.provider;
      slot.querySelector('datalist').innerHTML = r.models.map(m => `<option value="${ea(m)}">`).join(''); slot.querySelector('.mdl').value = r.provider.model; renderModelSel();
      say(r.models.length ? `${r.models.length} model(s) loaded — pick one in the Model box.` : 'No models returned.', r.models.length ? 'ok' : 'bad');
    } else if (b.classList.contains('tst')) {
      say('testing…'); const r = await api('/api/providers/test', { id });
      say(r.ok ? `OK · ${r.latency_ms} ms · ${r.message}` : r.error, r.ok ? 'ok' : 'bad'); refreshMatrix();
    }
  } catch (err) { say(err.message, 'bad'); }
});
async function saveSlot(slot) {
  const q = c => slot.querySelector(c), id = slot.dataset.id, body = { id };
  if (q('.en')) body.enabled = q('.en').checked;
  if (q('.url')) body.base_url = q('.url').value;
  if (q('.key')) body.api_key = q('.key').value;
  if (q('.mdl')) body.model = q('.mdl').value;
  const r = await api('/api/providers', body), i = S.st.providers.findIndex(p => p.id === id);
  r.provider.local = S.st.providers[i].local; r.provider.key_url = S.st.providers[i].key_url; S.st.providers[i] = r.provider;
  if (q('.key')) q('.key').value = r.provider.api_key;
  slot.classList.toggle('off', !r.provider.enabled); renderModelSel(); linkNow(); refreshMatrix();
}
pane('providers').addEventListener('change', async e => {
  const slot = e.target.closest('.slot[data-id]'); if (!slot) return;
  try { if (e.target.classList.contains('free')) await saveSettings({ openrouter_free_only: e.target.checked }); else await saveSlot(slot); sysStatus('Saved.', 'ok'); }
  catch (err) { sysStatus('Save failed: ' + err.message, 'bad'); }
});

/* ---- council roster ---- */
const rosterRow = (ref, label, on, role) => `<div class="rr"><input type="checkbox" value="${ea(ref)}"${on ? ' checked' : ''}><span title="${ea(ref)}">${esc(label)}</span><input type="text" class="role" list="roles" placeholder="role (optional)" value="${ea(role || '')}"></div>`;
function buildCouncil() {
  const s = S.st.settings, opts = options(false), chosen = new Set(s.council_members), roles = s.council_roles || {};
  const known = new Set(opts.map(o => o.ref)), extra = s.council_members.filter(r => !known.has(r));
  pane('council').innerHTML = `<p class="hint">One <b>orchestrator</b> (any model, local or cloud) writes a shared spec and gives every member its own files. Members build in parallel; NYXOR then checks the files (syntax, references, placeholders) and the orchestrator repairs what is broken. Tick who sits on the council — any mix of local and cloud models, and the same model can appear once per ref. Models appear after DETECT in PROVIDERS.</p>
<div class="modenote warn">☁ Cloud members receive this task (not your chat history, and never local files or command output).</div>
<label class="fld"><span>Orchestrator</span><select id="cLead"><option value="">(the model selected in the console)</option>${opts.map(o => `<option value="${ea(o.ref)}"${o.ref === s.council_lead ? ' selected' : ''}>${esc(o.pname)} · ${esc(o.model)}${o.local ? ' (local)' : ''}</option>`).join('')}</select></label>
<div class="fld"><span>Council members</span><div class="roster" id="cRoster">${opts.map(o => rosterRow(o.ref, o.pname + ' · ' + o.model, chosen.has(o.ref), roles[o.ref])).join('') + extra.map(r => rosterRow(r, r, true, roles[r])).join('') || '<div class="empty">No models yet. Paste a key or press DETECT.</div>'}</div></div>
<datalist id="roles"><option value="Architect"><option value="Programmer"><option value="UI designer"><option value="Game designer"><option value="Tester"><option value="Researcher"><option value="Reviewer"></datalist>
<div class="row2"><button type="button" class="ghost-btn sm" id="cAll">ALL ENABLED</button><button type="button" class="ghost-btn sm" id="cNone">CLEAR</button><input id="cExtra" placeholder="add by hand: provider:model" spellcheck="false" style="flex:1;background:var(--bg2);border:1px solid var(--line2);color:var(--text);font:inherit;font-size:11px;padding:6px 8px;outline:0"><button type="button" class="ghost-btn sm" id="cAdd">ADD</button></div>
<div class="fld-row"><label class="fld"><span>Strategy</span><select id="cStrat">${[['auto', 'Auto — build tasks use candidates, the rest merge'], ['candidates', 'Candidates — best-of-N, every one tested'], ['split', 'Split — each member owns files'], ['merge', 'Merge — text answers']].map(([v, t]) => `<option value="${v}"${s.council_strategy === v ? ' selected' : ''}>${t}</option>`).join('')}</select></label>
<label class="fld"><span>Repair rounds (0–3)</span><input id="cFix" type="number" min="0" max="3" value="${s.council_fix_rounds}"></label>
<label class="fld"><span>Member timeout (s)</span><input id="cTimeout" type="number" min="30" max="1800" value="${s.council_member_timeout}"></label>
<label class="fld"><span>Wait for stragglers (s)</span><input id="cGrace" type="number" min="0" max="120" value="${s.council_grace}"></label></div>
<label class="chk"><input id="cAuto" type="checkbox"${s.council_auto ? ' checked' : ''}> Auto-pick members when none are ticked (best recent record first)</label>
<label class="chk"><input id="cRev" type="checkbox"${s.council_review ? ' checked' : ''}> The other members review the winner before it is repaired</label>
<div class="fld"><span>Member record (valid builds / runs · average time) — a member that fails 3 times in a row is skipped</span><div class="mlist" id="cStats"><div class="empty">loading…</div></div><div><button type="button" class="ghost-btn sm" id="cStatReset">RESET RECORD</button></div></div>`;
  S.built.council = true;
  const showStats = st => { const rows = Object.entries(st); el('cStats').innerHTML = rows.length ? rows.map(([ref, r]) => `<div class="mi"><span>${esc(ref)}</span><i>${r.valid}/${r.runs} valid · ${r.ok} ok · avg ${r.runs ? Math.round(r.secs / r.runs) : 0}s${r.streak >= 3 ? ' · SKIPPED' : ''}</i></div>`).join('') : '<div class="empty">No runs recorded yet.</div>'; };
  api('/api/council/stats').then(r => showStats(r.stats)).catch(() => { });
  el('cStatReset').onclick = async () => { showStats((await api('/api/council/stats/reset', {})).stats); };
  const box = el('cRoster');
  el('cAll').onclick = () => box.querySelectorAll('input[type=checkbox]').forEach(c => (c.checked = true));
  el('cNone').onclick = () => box.querySelectorAll('input[type=checkbox]').forEach(c => (c.checked = false));
  el('cAdd').onclick = () => { const v = el('cExtra').value.trim(); if (v.includes(':')) { box.insertAdjacentHTML('beforeend', rosterRow(v, v, true, '')); el('cExtra').value = ''; } };
}

/* ---- security bar, terminal, command palette (2.3 redesign) ---- */
function renderSecbar() {
  const box = el('secItems'); if (!box) return;
  if (S.locked || !S.st) { box.innerHTML = '<span class="chip-s bad"><i></i>Vault sealed</span>'; return; }
  const st = S.st, t = state.tel, f = (st.consent && st.consent.flows) || {};
  const open = Object.values(f).filter(Boolean).length;
  const mm = x => String(~~(x / 60)).padStart(2, '0') + ':' + String(x % 60).padStart(2, '0');
  const approval = st.settings.agent_approval;
  const ws = String(st.workspace || '').split(/[\\/]/).filter(Boolean).slice(-2).join('/');
  const chips = [
    ['ok', `Vault unlocked${t && t.lock_left != null ? ' · locks in ' + mm(t.lock_left) : st.settings.auto_lock_minutes ? '' : ' · auto-lock off'}`, 'security', 'Encrypted on disk. Click for security settings.'],
    [open ? 'warn' : 'ok', open ? `${open} data flow${open > 1 ? 's' : ''} allowed` : 'Local only · nothing leaves this PC', 'privacy', 'Which kinds of data may leave this PC. Click to change.'],
    [approval === 'auto' ? 'bad' : 'ok', { readonly: 'Agent: read-only', ask: 'Agent: asks before acting', auto: 'Agent: AUTO, no prompts' }[approval] || 'Agent', 'agent', 'What the agent may do without asking.'],
    [st.terminal && st.terminal.enabled ? 'warn' : 'ok', st.terminal && st.terminal.enabled ? 'Terminal on' : 'Terminal off', 'agent', 'The terminal runs commands as you. Off by default.'],
  ];
  if (ws) chips.push(['shrink', 'Workspace: ' + ws, 'agent', st.workspace]);
  if (isStore()) chips.push(['', 'Store build', 'privacy', 'Stricter privacy and safety limits.']);
  box.innerHTML = chips.map(([c, txt, tab, title]) => `<button type="button" class="chip-s ${c}" data-open="${tab}" title="${ea(title)}"><i></i><span class="ct">${esc(txt)}</span></button>`).join('');
  box.querySelectorAll('button').forEach(b => { b.onclick = () => openSystems(b.dataset.open); });
}
const term = { hist: [], hi: 0, run: null, off: 0, timer: null };
function termPrint(txt, cls) {
  const o = el('termOut'), sp = document.createElement('span'); if (cls) sp.className = cls; sp.textContent = txt; o.appendChild(sp); o.scrollTop = o.scrollHeight;
}
function termShow(on) {
  el('drawer').hidden = !on; el('termBtn').classList.toggle('on', on);
  if (!on) return;
  const enabled = S.st && S.st.terminal && S.st.terminal.enabled;
  el('termOff').hidden = !!enabled; el('termForm').hidden = !enabled;
  el('termCwd').textContent = enabled ? S.st.terminal.cwd : '';
  if (enabled) { if (!el('termOut').childNodes.length) termPrint('Runs commands as you, inside your allowed folders. No interactive programs (no stdin). Esc stops a running command.\n', 'dim'); el('termInput').focus(); }
}
async function termRun(cmd) {
  termPrint('$ ' + cmd + '\n', 'cmd');
  if (/^(clear|cls)$/i.test(cmd)) { el('termOut').textContent = ''; return; }
  let r;
  try { r = await api('/api/terminal/run', { command: cmd }); } catch (e) { termPrint(String(e.message || e) + '\n', 'err'); return; }
  if (r.cwd) { S.st.terminal.cwd = r.cwd; el('termCwd').textContent = r.cwd; }
  if (!r.id) { if (r.output) termPrint(r.output, r.code ? 'err' : ''); return; }
  term.run = r.id; term.off = 0; el('termKill').hidden = false;
  const tick = async () => {
    let p; try { p = await api('/api/terminal/output?id=' + r.id + '&since=' + term.off); } catch { term.run = null; el('termKill').hidden = true; return; }
    if (p.output) { termPrint(p.output); term.off = p.n; }
    if (p.done) { term.run = null; el('termKill').hidden = true; if (p.code) termPrint(`exit ${p.code}\n`, 'err'); return; }
    term.timer = setTimeout(tick, 250);
  };
  tick();
}
el('termForm').addEventListener('submit', e => { e.preventDefault(); const i = el('termInput'), c = i.value.trim(); if (!c) return; term.hist.push(c); term.hi = term.hist.length; i.value = ''; termRun(c); });
el('termInput').addEventListener('keydown', e => {
  if (e.key === 'ArrowUp' && term.hist.length) { term.hi = Math.max(0, term.hi - 1); e.target.value = term.hist[term.hi]; e.preventDefault(); }
  else if (e.key === 'ArrowDown') { term.hi = Math.min(term.hist.length, term.hi + 1); e.target.value = term.hist[term.hi] || ''; e.preventDefault(); }
  else if (e.key === 'Escape' && term.run) { api('/api/terminal/kill', { id: term.run }); }
});
el('termKill').onclick = () => { if (term.run) api('/api/terminal/kill', { id: term.run }); };
el('termClear').onclick = () => { el('termOut').textContent = ''; };
el('termClose').onclick = () => termShow(false);
el('termBtn').onclick = () => termShow(el('drawer').hidden);
el('termEnable').onclick = () => openSystems('agent');
const PALETTE = () => [
  ['New chat', '', () => newChat()],
  ['Mode: Direct', 'one model answers', () => setModeUI('direct')], ['Mode: Council', 'several models compare', () => setModeUI('council')], ['Mode: Agent', 'local model with tools', () => setModeUI('agent')],
  ['Toggle terminal', 'Ctrl+`', () => termShow(el('drawer').hidden)],
  ['Toggle focus mode', 'hide side panels', async () => { await api('/api/settings', { ui_focus: !S.st.settings.ui_focus }); S.st = await api('/api/state'); applyFocus(); }],
  ['Open Providers', '', () => openSystems('providers')], ['Open Router', 'model choice and fallback', () => openSystems('router')], ['Open Council', '', () => openSystems('council')],
  ['Open Agent and terminal settings', '', () => openSystems('agent')], ['Open Web research', '', () => openSystems('web')], ['Open Voice', '', () => openSystems('voice')],
  ['Open Security', '', () => openSystems('security')], ['Open Privacy', 'what may leave this PC', () => openSystems('privacy')], ['Open Memory', '', () => openSystems('memory')],
  ['Open Video studio', '', () => el('videoBtn').click()], ['Lock the vault', '', () => lockNow()],
];
let palSel = 0;
function palRender() {
  const q = el('palInput').value.trim().toLowerCase(), list = PALETTE().filter(x => !q || x[0].toLowerCase().includes(q));
  palSel = Math.max(0, Math.min(palSel, list.length - 1)); el('palList').innerHTML = '';
  list.forEach((x, i) => { const d = document.createElement('div'); d.className = 'pal-item' + (i === palSel ? ' on' : ''); d.innerHTML = `<span>${esc(x[0])}</span><small>${esc(x[1])}</small>`; d.onclick = () => palRun(x); el('palList').appendChild(d); });
  el('palList')._items = list;
}
function palRun(x) { el('palette').hidden = true; x[2](); }
function palOpen() { if (!S.st) return; el('palInput').value = ''; palSel = 0; el('palette').hidden = false; palRender(); el('palInput').focus(); }
el('palInput').addEventListener('input', () => { palSel = 0; palRender(); });
el('palInput').addEventListener('keydown', e => {
  const items = el('palList')._items || [];
  if (e.key === 'ArrowDown') { palSel = Math.min(items.length - 1, palSel + 1); palRender(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { palSel = Math.max(0, palSel - 1); palRender(); e.preventDefault(); }
  else if (e.key === 'Enter' && items[palSel]) { palRun(items[palSel]); e.preventDefault(); }
  else if (e.key === 'Escape') el('palette').hidden = true;
});
el('palette').addEventListener('click', e => { if (e.target === el('palette')) el('palette').hidden = true; });
el('palBtn').onclick = palOpen;
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'k' || (e.shiftKey && e.key.toLowerCase() === 'p'))) { e.preventDefault(); palOpen(); }
  else if ((e.ctrlKey || e.metaKey) && e.key === '`') { e.preventDefault(); if (S.st) termShow(el('drawer').hidden); }
});
setInterval(renderSecbar, 2500);

/* ---- privacy: consent, reports (Store policy 10.5 and 11.16) ---- */
const isStore = () => !!(S.st && S.st.store && S.st.store.enabled);
const flowAllowed = f => !!(S.st && S.st.consent && S.st.consent.flows[f]);
function flowRows(box, current) {
  box.innerHTML = Object.entries(S.st.flows).map(([k, t]) => `<label class="chk flow"><input type="checkbox" data-flow="${ea(k)}"${current[k] ? ' checked' : ''}> <span>${esc(t)}</span></label>`).join('');
}
const readFlows = box => Object.fromEntries([...box.querySelectorAll('input[data-flow]')].map(i => [i.dataset.flow, !!i.checked]));
async function saveConsent(flows) {
  const r = await api('/api/consent', { flows }); S.st.consent = r.consent;
  if (S.built.privacy) buildPrivacy();
  renderModelSel(); linkNow();
  return r.consent;
}
function openConsent() {
  flowRows(el('consentFlows'), S.st.consent.flows); el('consentModal').hidden = false;
}
el('consentNone').onclick = async () => { await saveConsent({}); el('consentModal').hidden = true; toast('Everything stays local. You can change this in SYSTEMS → PRIVACY.', 'ok'); };
el('consentSave').onclick = async () => { await saveConsent(readFlows(el('consentFlows'))); el('consentModal').hidden = true; toast('Privacy choices saved.', 'ok'); };
function voiceAllowed(silent) {
  if (flowAllowed('voice_input')) return true;
  if (!silent) toast('The microphone is blocked: browser speech recognition sends audio to the browser vendor. Allow it in SYSTEMS → PRIVACY.', 'hot');
  return false;
}
let repMid = null;
function openReport(mid) {
  repMid = mid || null; el('repNote').value = ''; el('repStatus').textContent = ''; el('repStatus').className = 'modal-status';
  el('repCat').innerHTML = S.st.report_categories.map(c => `<option value="${ea(c)}">${esc({ harmful: 'Harmful or dangerous', illegal: 'Illegal activity', sexual: 'Sexual content', hateful: 'Hateful, threatening or harassing', false: 'False or misleading', other: 'Something else' }[c] || c)}</option>`).join('');
  el('reportModal').hidden = false;
}
async function sendReport(mail) {
  const body = { conv_id: S.conv, message_id: repMid, category: el('repCat').value, note: el('repNote').value, include_text: el('repText').checked };
  const r = await api('/api/report', body);
  const cfg = S.st.store || {};
  if (mail && cfg.support_email) {
    const txt = `Report #${r.id} about an AI answer in NYXOR ${S.st.version}\nCategory: ${body.category}\nDetails: ${body.note || '(none)'}\n` + (body.include_text ? '\n(The answer text is saved in the report on your PC; paste it here if you want to send it.)' : '');
    location.href = `mailto:${encodeURIComponent(cfg.support_email)}?subject=${encodeURIComponent('NYXOR report #' + r.id)}&body=${encodeURIComponent(txt)}`;
    el('repStatus').textContent = 'Saved. Your email program should open with a draft. You decide whether to send it.';
  } else {
    el('repStatus').textContent = mail ? 'Saved on this PC. No support address is set in this build, so nothing was opened. See SYSTEMS → PRIVACY.' : 'Saved on this PC. Nothing was sent.';
  }
  el('repStatus').className = 'modal-status ok';
  if (S.built.privacy) loadReports();
  setTimeout(() => { el('reportModal').hidden = true; }, 1800);
}
el('repClose').onclick = () => { el('reportModal').hidden = true; };
el('repSave').onclick = () => sendReport(false);
el('repSend').onclick = () => sendReport(true);
async function loadReports() {
  const box = el('prRep'); if (!box) return;
  const { reports } = await api('/api/reports');
  box.innerHTML = reports.length ? reports.map(x => `<div class="mi"><span>#${x.id} · ${esc(x.category)} · ${esc(x.model || '')}</span><i>${new Date(x.created_at * 1000).toLocaleString()} ${esc(x.note || '').slice(0, 80)} <button type="button" class="ghost-btn sm" data-del="${x.id}">DELETE</button></i></div>`).join('') : '<div class="empty">No reports saved.</div>';
  box.querySelectorAll('[data-del]').forEach(b => { b.onclick = async () => { await api('/api/report/delete', { id: +b.dataset.del }); loadReports(); }; });
}
function buildPrivacy() {
  const st = S.st.store || {};
  pane('privacy').innerHTML = `<p class="hint">NYXOR has no server, no account and no analytics: <b>the publisher receives nothing</b>. Chats, memories and keys are stored on this PC in the encrypted vault. These switches decide which kinds of data may leave this PC. Local models never need permission. A blocked flow is not used, and the interface says so when you try.</p>
<div class="flows" id="prFlows"></div><div class="modal-actions"><span class="sp"></span><button type="button" class="send" id="prSave">SAVE PRIVACY CHOICES</button></div>
<div class="fld"><span>Build</span><div class="mlist"><div class="mi"><span>${st.enabled ? 'Store build' : 'Standard build'}</span><i>${st.enabled ? 'stricter limits: no search scraping, the agent always asks, a safety block is added to the system prompt' : 'the Store limits are off'}</i></div><div class="mi"><span>Privacy policy</span><i><a href="/privacy" target="_blank" rel="noopener">open in a new window</a>${st.privacy_url ? ' · ' + esc(st.privacy_url) : ''}</i></div><div class="mi"><span>Support address</span><i>${esc(st.support_email || 'not set in this build')}</i></div></div></div>
<div class="fld"><span>Reports you saved about AI answers (they stay on this PC until you send one by email)</span><div class="mlist" id="prRep"><div class="empty">loading…</div></div></div>`;
  S.built.privacy = true; flowRows(el('prFlows'), S.st.consent.flows);
  el('prSave').onclick = async () => { await saveConsent(readFlows(el('prFlows'))); sysStatus('Privacy choices saved.', 'ok'); };
  loadReports().catch(() => { });
}
C.privacy = () => {
  if (!S.st) return needSt();
  const f = S.st.consent.flows;
  return 'WHAT MAY LEAVE THIS PC\n' + Object.keys(S.st.flows).map(k => `${k.padEnd(16, '.')} ${f[k] ? 'allowed' : 'blocked'}`).join('\n') + '\n\nLocal models never leave this PC. Change this in SYSTEMS → PRIVACY.';
};

/* ---- router ---- */
function routerRows(rows) { return rows.length ? rows.map((c, i) => `<div class="mi"><span>${i === 0 ? '▶ ' : ''}${esc(String(c.ref))}${c.local ? ' (local)' : ''}</span><i>${esc(String(c.score))} — ${esc(c.why.join('; '))}</i></div>`).join('') : '<div class="empty">No usable model passed the filters.</div>'; }
function buildRouter() {
  const s = S.st.settings, pool = new Set(s.router_pool || []);
  const llms = S.st.providers.filter(p => p.kind === 'llm' && p.enabled && (p.is_local || p.has_key) && p.model);
  const rd = (v, t) => `<label><input type="radio" name="rm" value="${v}"${s.router_mode === v ? ' checked' : ''}><span>${t}</span></label>`;
  pane('router').innerHTML = `<p class="hint">The router chooses which connected model answers a DIRECT message, from the <b>task</b> (short question or heavy build), <b>privacy</b> (a chat that holds AGENT output stays on local models), <b>availability</b>, <b>speed</b>, <b>cost</b> and what NYXOR has <b>learned</b> from earlier answers. Every choice is shown in the chat with its reasons. Capability and cost tiers are <b>guessed from model names</b> (a "mini" or 8B model counts as small, "sonnet" or 70B as large); the learned record corrects the guess over time. COUNCIL and AGENT modes choose their own models and are not routed yet.</p>
<div class="fld"><span>Model choice</span><div class="radio">${rd('manual', 'Manual — always the model selected in the console (fallback below still applies)')}${rd('auto', 'Auto — NYXOR picks a model for every DIRECT message and shows why')}</div></div>
<label class="chk"><input id="rFb" type="checkbox"${s.router_fallback ? ' checked' : ''}> If a model fails before it starts answering (bad key, rate limit, server down, model missing), switch to the next-best one and say so. A failure after the answer has started is never retried.</label>
<label class="chk"><input id="rFbCloud" type="checkbox"${s.router_fallback_cloud ? ' checked' : ''}> Allow a failing <b>local</b> model to fall back to a <b>cloud</b> model (your message then leaves this PC; chats holding AGENT output never do)</label>
<div class="fld"><span>Models the router may use (none ticked = every connected model)</span><div class="roster" id="rPool">${llms.map(p => { const ref = p.id + ':' + p.model; return `<label class="chk"><input type="checkbox" value="${ea(ref)}"${pool.has(ref) ? ' checked' : ''}> ${esc(p.name)} · ${esc(p.model)} ${p.is_local ? '(local)' : '(cloud)'}</label>`; }).join('') || '<div class="empty">No connected model yet. Paste a key or press DETECT in PROVIDERS.</div>'}</div></div>
<div class="fld"><span>Try a message — see which model would be picked (nothing is sent to any model)</span><div class="row2"><input id="rTest" placeholder="type a message" spellcheck="false" style="flex:1;background:var(--bg2);border:1px solid var(--line2);color:var(--text);font:inherit;font-size:11px;padding:6px 8px;outline:0"><button type="button" class="ghost-btn sm" id="rTestGo">PREVIEW</button></div><div class="mlist" id="rOut"></div></div>
<div class="fld"><span>Learned record (answers ok / runs · tokens per second) — a model that fails 3 times in a row is skipped for 5 minutes</span><div class="mlist" id="rStats"><div class="empty">loading…</div></div><div><button type="button" class="ghost-btn sm" id="rStatReset">RESET RECORD</button></div></div>`;
  S.built.router = true;
  const showStats = st => { const rows = Object.entries(st); el('rStats').innerHTML = rows.length ? rows.map(([ref, r]) => `<div class="mi"><span>${esc(ref)}</span><i>${r.ok}/${r.runs} ok${r.tps ? ' · ' + r.tps + ' tok/s' : ''}${r.streak >= 3 ? ' · COOLING DOWN' : r.streak ? ' · ' + r.streak + ' failure(s) in a row' : ''}</i></div>`).join('') : '<div class="empty">No answers recorded yet.</div>'; };
  api('/api/router/stats').then(r => showStats(r.stats)).catch(() => { });
  el('rStatReset').onclick = async () => { showStats((await api('/api/router/stats/reset', {})).stats); };
  el('rTestGo').onclick = async () => {
    const t = el('rTest').value.trim(); if (!t) return;
    const mode = (document.querySelector('input[name="rm"]:checked') || {}).value;
    try { const d = (await api('/api/router/preview', { text: t, conv_id: S.conv, mode })).decision; el('rOut').innerHTML = `<div class="mi"><span>${esc(d.line)}</span><i>${esc(d.task.reasons.join('; '))}</i></div>` + routerRows(d.ranked) + d.excluded.map(x => `<div class="mi"><span>✕ ${esc(String(x.ref))}</span><i>${esc(x.why)}</i></div>`).join(''); }
    catch (err) { el('rOut').innerHTML = `<div class="empty">${esc(String(err.message || err))}</div>`; }
  };
}

/* ---- budgets ---- */
function budgetUse(t, local) {
  if (!t.calls) return 'nothing yet';
  const money = local ? 'free' : t.nopr ? (t.usd > 0 ? fmtUsd(t.usd) + ' + ' : '') + t.nopr + ' call(s) without a price' : fmtUsd(t.usd);
  return `${fmtTok(t.tok)} tok (${fmtTok(t.in)} in · ${fmtTok(t.out)} out) · ${t.calls} call(s)${t.est ? ' · ' + t.est + ' estimated' : ''} · ${money}`;
}
function buildBudgets() {
  pane('budgets').innerHTML = `<p class="hint">Caps and a usage meter for every model call (DIRECT, COUNCIL, AGENT, research). <b>Tokens</b> are the provider's own numbers when it reports them, otherwise an <b>estimate</b> (about 4 characters per token, marked “estimated”). <b>Dollars</b> are tokens × the price <b>you enter</b> per million tokens: NYXOR has no price list because prices change and cannot be checked offline. A request is refused <b>before it is sent</b> when the spend so far plus the size of its input would reach a cap; the answer itself cannot be predicted, so one call can overshoot. Days and months follow this PC's clock. Voice, video and search APIs are not counted. The provider's own bill is the truth.</p><div id="bgBody"><div class="empty">loading…</div></div>`;
  S.built.budgets = true;
  sysStatus('Budgets save with SAVE BUDGETS. Other tabs save with APPLY.');
  loadBudgets();
}
async function loadBudgets() { try { renderBudgets((await api('/api/budget')).budget); } catch (err) { if (el('bgBody')) el('bgBody').innerHTML = `<div class="empty">${esc(String(err.message || err))}</div>`; } }
function renderBudgets(b) {
  const rd = (v, t) => `<label><input type="radio" name="bga" value="${v}"${b.action === v ? ' checked' : ''}><span>${t}</span></label>`;
  const num = (cls, key, val, ph) => `<input type="number" min="0" step="any" class="${cls}" data-key="${key}" value="${val ? ea(val) : ''}" placeholder="${ph}">`;
  const rows = b.providers.map(p => {
    const note = p.capped ? '<b> · CAPPED</b>' : p.override ? ' · override active until midnight' : '';
    const mrows = Object.keys(p.models).concat(p.model && !p.models[p.model] ? [p.model] : []).filter((m, i, a) => a.indexOf(m) === i);
    return `<div class="bg-card" data-pid="${ea(p.id)}" style="margin-top:16px;padding-top:12px;border-top:1px solid var(--line2)"><div>
<div class="mi"><span><b>${esc(p.name)}</b> ${p.local ? '· free' : '· cloud'}${note}</span><i>${p.pct != null ? p.pct + '% of the tightest cap' : 'no cap set'}</i></div>
<div class="mi"><span>Today</span><i>${esc(budgetUse(p.day, p.local))}</i></div><div class="mi"><span>This month</span><i>${esc(budgetUse(p.month, p.local))}</i></div>
${p.local ? '' : `<div class="row2" style="margin-top:8px"><label class="fld" style="flex:1 1 150px;min-width:0"><span>Price per million input tokens ($)</span>${num('bp-in', 'in', p.price && p.price.in, 'e.g. 3.00')}</label><label class="fld" style="flex:1 1 150px;min-width:0"><span>Price per million output tokens ($)</span>${num('bp-out', 'out', p.price && p.price.out, 'e.g. 15.00')}</label></div>`}
<div class="row2"><label class="fld" style="flex:1 1 150px;min-width:0"><span>Cap $ / day</span>${num('bc', 'usd_day', p.caps.usd_day, '0 = none')}</label><label class="fld" style="flex:1 1 150px;min-width:0"><span>Cap $ / month</span>${num('bc', 'usd_month', p.caps.usd_month, '0 = none')}</label><label class="fld" style="flex:1 1 150px;min-width:0"><span>Cap tokens / day</span>${num('bc', 'tok_day', p.caps.tok_day, '0 = none')}</label><label class="fld" style="flex:1 1 150px;min-width:0"><span>Cap tokens / month</span>${num('bc', 'tok_month', p.caps.tok_month, '0 = none')}</label></div>
${p.local || !mrows.length ? '' : `<details><summary class="hint">Price of one model (overrides the price above)</summary>${mrows.map(m => { const mp = p.model_prices[m] || {}; return `<div class="row2" data-model="${ea(m)}"><span style="min-width:180px;font-size:12px">${esc(m)}</span>${num('bm-in', 'in', mp.in, 'input $ per million')}${num('bm-out', 'out', mp.out, 'output $ per million')}</div>`; }).join('')}</details>`}
<div style="display:flex;align-items:center;gap:10px;margin-top:8px"><button type="button" class="ghost-btn sm bg-ov" style="width:auto;flex:none" data-on="${p.override ? 0 : 1}">${p.override ? 'END OVERRIDE' : 'OVERRIDE TODAY'}</button><span class="ss">${p.override ? 'caps are ignored for this provider until midnight' : 'ignore this provider\'s caps until midnight'}</span></div></div></div>`;
  }).join('');
  el('bgBody').innerHTML = `<div class="mlist"><div class="mi"><span>All providers · today</span><i>${esc(budgetUse(b.totals.day, false))}</i></div><div class="mi"><span>All providers · this month</span><i>${esc(budgetUse(b.totals.month, false))}</i></div></div>
<div class="fld"><span>When a cap is reached</span><div class="radio">${rd('block', 'Block — the provider refuses requests until the next day / month or until you press OVERRIDE TODAY here')}${rd('ask', 'Ask — same, and the chat shows the refusal with an ALLOW TODAY button')}</div></div>
${rows || '<div class="empty">No connected model yet. Paste a key or press DETECT in PROVIDERS.</div>'}
<div class="row2" style="margin-top:10px"><button type="button" class="send" id="bgSave">SAVE BUDGETS</button><button type="button" class="ghost-btn sm" id="bgReset">CLEAR USAGE HISTORY</button><span class="ss" id="bgSs"></span></div>`;
  const ss = t => { el('bgSs').textContent = t; };
  el('bgSave').onclick = async () => {
    const prices = {}, caps = {};
    document.querySelectorAll('#bgBody .bg-card').forEach(sl => {
      const pid = sl.dataset.pid, val = c => (c ? c.value.trim() : '');
      prices[pid] = { in: val(sl.querySelector('.bp-in')), out: val(sl.querySelector('.bp-out')) };
      caps[pid] = {}; sl.querySelectorAll('.bc').forEach(i => { caps[pid][i.dataset.key] = i.value.trim() || 0; });
      sl.querySelectorAll('[data-model]').forEach(r => { prices[pid + ':' + r.dataset.model] = { in: val(r.querySelector('.bm-in')), out: val(r.querySelector('.bm-out')) }; });
    });
    try { renderBudgets((await api('/api/budget', { action: (document.querySelector('input[name="bga"]:checked') || {}).value, prices, caps })).budget); sysStatus('Budgets saved.', 'ok'); }
    catch (err) { sysStatus('Not saved: ' + err.message, 'bad'); }
  };
  el('bgReset').onclick = async () => { try { renderBudgets((await api('/api/budget/reset', {})).budget); sysStatus('Usage history cleared.', 'ok'); } catch (err) { sysStatus(err.message, 'bad'); } };
  document.querySelectorAll('#bgBody .bg-ov').forEach(btn => { btn.onclick = async () => { try { renderBudgets((await api('/api/budget/override', { provider: btn.closest('.bg-card').dataset.pid, on: btn.dataset.on === '1' })).budget); } catch (err) { sysStatus(err.message, 'bad'); } }; });
}

/* ---- agent, persona ---- */
function buildAgent() {
  const s = S.st.settings, r = (v, t, w) => `<label><input type="radio" name="aprv" value="${v}"${s.agent_approval === v ? ' checked' : ''}><span>${t}${w ? `<br><span class="warn">${w}</span>` : ''}</span></label>`;
  pane('agent').innerHTML = `<p class="hint">AGENT mode lets a <b>local</b> model (Ollama, LM Studio, llama.cpp on this PC) operate your computer through tools. Cloud models are refused by the backend and never see local files or command output. Reading inside allowed folders runs automatically; writing, running commands, opening items and web searches need your approval.</p>
<div class="fld"><span>Approval</span><div class="radio">${r('readonly', 'Read-only — the agent can look, never change anything')}${r('ask', 'Ask — approve every write, command, open and search (recommended)')}${isStore() ? '' : r('auto', 'Auto — never ask', '⚠ DANGER: commands run without confirmation. A shell can touch anything on this PC.')}</div></div>
<label class="chk"><input id="aTerm" type="checkbox"${s.terminal_enabled ? ' checked' : ''}> <span><b>Enable the terminal panel.</b> It runs commands on this PC <b>as you</b>, inside the allowed folders below (it cannot <code>cd</code> out of them, but a command can still touch anything your account can). One-shot commands only: no interactive programs. Off by default.</span></label>
<label class="fld"><span>Allowed folders (one per line; reads/writes outside are blocked)</span><textarea id="aDirs" rows="4" spellcheck="false">${esc(s.agent_allowed_dirs.join('\n'))}</textarea></label>
<div class="fld-row"><label class="fld"><span>Max steps per task (1–15)</span><input id="aSteps" type="number" min="1" max="15" value="${s.agent_max_steps}"></label><label class="fld"><span>Command timeout (s, max 120)</span><input id="aTime" type="number" min="5" max="120" value="${s.agent_command_timeout}"></label></div>`;
  S.built.agent = true;
}
function buildPersona() {
  const s = S.st.settings;
  pane('persona').innerHTML = `<label class="fld"><span>Persona / system prompt</span><textarea id="pSys" rows="8" spellcheck="false">${esc(s.system_prompt)}</textarea></label>
<div class="fld-row"><label class="fld"><span>Temperature</span><input id="pTemp" type="number" min="0" max="2" step="0.1" value="${s.temperature}"></label><label class="fld"><span>Context (turns sent)</span><input id="pTurns" type="number" min="0" max="50" value="${s.history_turns}"></label></div>
<div class="fld-row checks"><label class="chk"><input id="pThink" type="checkbox"${s.think ? ' checked' : ''}> Deep reasoning (Qwen3 thinking)</label><label class="chk"><input id="pShow" type="checkbox"${s.show_think ? ' checked' : ''}> Show reasoning trace</label></div>
<div><button type="button" class="ghost-btn" id="pReset">RESET PERSONA</button></div>
<p class="hint">Saved memories and the file-creation rule are appended to this prompt automatically.</p>`;
  S.built.persona = true; el('pReset').onclick = () => { el('pSys').value = S.st.defaults.system_prompt; };
}

/* ---- web research ---- */
function buildWeb() {
  const s = S.st.settings, searchers = S.st.providers.filter(p => p.kind === 'search' && p.has_key), rd = (n, v, t) => `<label><input type="radio" name="${n}" value="${v}"${s[n === 'wm' ? 'web_mode' : 'web_depth'] === v ? ' checked' : ''}><span>${t}</span></label>`;
  pane('web').innerHTML = `<p class="hint">When a question needs fresh facts (news, wars, prices, scores, "what is happening…"), NYXOR searches the web, <b>opens the top pages itself</b>, strips menus and ads, and hands the readable text to whichever model you chose. The briefing appears here with a source list; every source opens in NYXOR's own reader — nothing ever sends you to another site. Pages are fetched by the local backend, never by the browser, and private-network addresses are blocked.</p>
<div class="fld"><span>When to research</span><div class="radio">${rd('wm', 'auto', 'Auto — only when the question looks time-sensitive (recommended)')}${rd('wm', 'on', 'Always — every message')}${rd('wm', 'off', 'Never')}</div></div>
<div class="fld"><span>How deep</span><div class="radio">${rd('wd', 'deep', 'Deep — several search angles, up to 10 pages, dated and de-duplicated (best briefings)')}${rd('wd', 'read', 'Read — open the top 5 pages')}${rd('wd', 'snippets', 'Snippets only (fastest)')}</div></div>
<label class="fld"><span>Preferred search provider</span><select id="wProv"><option value="">(first one with a key)</option>${searchers.map(p => `<option value="${ea(p.id)}"${s.search_provider === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
<label class="chk"><input id="wKeyless" type="checkbox"${s.web_keyless ? ' checked' : ''}> Allow keyless search when no key is set: Bing News for news, DuckDuckGo otherwise (best effort; can be rate-limited)</label>
<label class="chk"><input id="wReader" type="checkbox"${s.web_reader ? ' checked' : ''}> Reader fallback for pages that need JavaScript (sends only that page's URL to r.jina.ai)</label>
<label class="fld"><span>Text budget handed to the model (characters; local models use at most 12000)</span><input id="wChars" type="number" min="4000" max="60000" step="1000" value="${s.research_chars}"></label>
<p class="hint">${searchers.length ? 'Search keys connected: ' + searchers.map(p => esc(p.name)).join(', ') + '.' : 'No search key yet — paste a Tavily, Brave, Serper or Exa key in PROVIDERS for reliable results.'} Small local models (Ollama defaults to a 4096-token context) may need <code>num_ctx</code> raised to use long research.</p>`;
  S.built.web = true;
}

/* ---- voice ---- */
function buildVoice() {
  const s = S.st.settings, voices = NX.listVoices().filter(v => /^en/i.test(v.lang)), cloudP = S.st.providers.filter(p => p.has_key && (p.kind === 'tts' || (p.kind === 'llm' && p.protocol === 'openai')));
  pane('voice').innerHTML = `<p class="hint">NYXOR cannot copy a film actor's voice. What it can do is a deep, slowed, metallic voice: the <b>DEEP</b> preset lowers a browser voice, and cloud voices (OpenAI-compatible TTS or ElevenLabs — use a voice you have the rights to) get an extra effect chain: pitch drop, low-end boost, a detuned double and a faint echo.</p>
<div class="row2"><button type="button" class="ghost-btn sm" id="vpDeep">PRESET: DEEP</button><button type="button" class="ghost-btn sm" id="vpRobot">PRESET: ROBOT</button><button type="button" class="ghost-btn sm" id="vpNormal">PRESET: NORMAL</button><button type="button" class="ghost-btn sm" id="vpTest">▶ TEST VOICE</button></div>
<label class="fld"><span>Engine</span><select id="vEngine"><option value="browser"${s.voice_engine === 'browser' ? ' selected' : ''}>Browser voice (free, offline)</option><option value="cloud"${s.voice_engine === 'cloud' ? ' selected' : ''}>Cloud voice (uses a provider key)</option></select></label>
<label class="fld"><span>Browser voice</span><select id="vName"><option value="">(auto: deepest English male voice)</option>${voices.map(v => `<option value="${ea(v.name)}"${s.voice_name === v.name ? ' selected' : ''}>${esc(v.name)} — ${esc(v.lang)}</option>`).join('')}</select></label>
<div class="fld-row"><label class="fld"><span>Pitch (0 = deepest)</span><input id="vPitch" type="range" min="0" max="2" step="0.05" value="${s.voice_pitch}"></label><label class="fld"><span>Speed</span><input id="vRate" type="range" min="0.5" max="1.6" step="0.02" value="${s.voice_rate}"></label></div>
<div class="grp-h">CLOUD VOICE</div>
<div class="fld-row"><label class="fld"><span>Provider</span><select id="vProv"><option value="">(choose)</option>${cloudP.map(p => `<option value="${ea(p.id)}"${s.voice_provider === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>
<label class="fld"><span>Model</span><input id="vModelT" value="${ea(s.voice_model)}" placeholder="tts-1 / eleven_multilingual_v2" spellcheck="false"></label>
<label class="fld"><span>Voice id / name</span><input id="vVoiceT" value="${ea(s.voice_voice)}" placeholder="onyx / voice id" spellcheck="false"></label></div>
<label class="chk"><input id="vFx" type="checkbox"${s.voice_fx ? ' checked' : ''}> Ultron effect chain on cloud voices</label>
<label class="fld"><span>Speak at most this many characters of a long answer (0 = everything)</span><input id="vMax" type="number" min="0" max="20000" step="100" value="${s.voice_max_chars}"></label>
<div class="grp-h">VOICE CONTROL (hands-free)</div>
<label class="chk"><input id="vcOn" type="checkbox"${s.voice_control ? ' checked' : ''}> Listen for commands (Chrome / Edge; needs microphone permission)</label>
<label class="fld"><span>Wake word (empty = every sentence is a command)</span><input id="vcWake" value="${ea(s.voice_wake)}" placeholder="nyxor" spellcheck="false"></label>
<p class="hint">Say: <b>"Nyxor, what is happening in the Strait of Hormuz"</b> · "Nyxor, stop" · "new chat" · "council mode" · "web search off" · "generate a video of …" · "read that again" · "lock the vault". Replies are spoken; while NYXOR speaks, it does not listen to itself.</p>`;
  S.built.voice = true;
  const preview = () => { const v = NX.voiceCfg; v.engine = el('vEngine').value; v.voiceName = el('vName').value; v.pitch = +el('vPitch').value; v.rate = +el('vRate').value; fxOn = el('vFx').checked; v.override = v.engine === 'cloud' ? { provider: el('vProv').value, model: el('vModelT').value, voice: el('vVoiceT').value } : null; NX.chooseVoice(); };
  const deepest = () => (voices.find(v => /david|mark|guy|ryan|daniel|alex|fred|george|james|male/i.test(v.name)) || voices[0] || {}).name || '';
  el('vpDeep').onclick = () => { el('vEngine').value = 'browser'; el('vPitch').value = 0.12; el('vRate').value = 0.8; el('vName').value = deepest(); preview(); };
  el('vpRobot').onclick = () => { el('vEngine').value = 'browser'; el('vPitch').value = 0; el('vRate').value = 0.95; el('vName').value = deepest(); preview(); };
  el('vpNormal').onclick = () => { el('vEngine').value = 'browser'; el('vPitch').value = 1; el('vRate').value = 1; el('vName').value = ''; preview(); };
  el('vpTest').onclick = () => { preview(); NX.stopSpeech(); NX.resetSpeech(); const prev = state.voice; state.voice = true; NX.speak('I am NYXOR. I have been listening to everything.'); state.voice = prev; };
  pane('voice').querySelectorAll('select,input').forEach(i => i.addEventListener('change', preview));
}

/* ---- security ---- */
function buildSecurity() {
  const s = S.st.settings;
  pane('security').innerHTML = `<p class="hint"><b>nyxor.db is encrypted.</b> On disk it is an opaque blob (scrypt-derived key, BLAKE2b stream cipher, HMAC-SHA256 integrity); the readable database exists only in memory while the vault is unlocked here. Other programs on this PC cannot read the API without this browser's session. Deleting a chat, a file or a memory erases it from the database (freed pages are zeroed and the previous encrypted copy is overwritten). Files you delete from disk are dropped from the database automatically, including text NYXOR extracted from them.</p>
<div class="modenote">Honest limits: the encryption is built from standard primitives in Python's standard library (no AES available there) and is not independently audited; it protects the file at rest, not a PC that already has malware while unlocked. Generated and uploaded files in nyxor-workspace/ are ordinary files so you can open them — delete them whenever you like. Exports are plain JSON unless you use the encrypted backup.</div>
<label class="fld"><span>Auto-lock after idle minutes (0 = never)</span><input id="sLock" type="number" min="0" max="1440" value="${s.auto_lock_minutes}"></label>
<div class="grp-h">CHANGE PASSPHRASE</div>
<div class="fld-row"><label class="fld"><span>Current</span><input id="spOld" type="password" autocomplete="off"></label><label class="fld"><span>New (8+ characters)</span><input id="spNew" type="password" autocomplete="off"></label><label class="fld"><span>Repeat new</span><input id="spNew2" type="password" autocomplete="off"></label></div>
<div class="row2"><button type="button" class="ghost-btn" id="spGo">CHANGE PASSPHRASE</button><span class="ss" id="spSs"></span></div>
<div class="grp-h">RECOVERY CODE</div>
<div class="row2"><input id="srPass" type="password" placeholder="your passphrase" autocomplete="off" style="flex:1;background:var(--bg2);border:1px solid var(--line2);color:var(--text);font:inherit;font-size:12px;padding:8px 10px;outline:0"><button type="button" class="ghost-btn" id="srGo">NEW RECOVERY CODE</button></div>
<pre class="recovery" id="srCode" hidden></pre>
<div class="grp-h">BACKUP</div>
<div class="row2"><button type="button" class="ghost-btn" id="sBackup">DOWNLOAD ENCRYPTED BACKUP</button><button type="button" class="ghost-btn" id="sLockNow">LOCK NOW</button></div>`;
  S.built.security = true;
  const say = (t, c) => { const x = el('spSs'); x.textContent = t; x.className = 'ss ' + (c || ''); };
  el('spGo').onclick = async () => {
    if (el('spNew').value !== el('spNew2').value) return say('The new passphrases do not match.', 'bad');
    try { await api('/api/vault/passphrase', { old: el('spOld').value, new: el('spNew').value }); say('Passphrase changed. The old one no longer opens the vault.', 'ok'); ['spOld', 'spNew', 'spNew2'].forEach(i => (el(i).value = '')); }
    catch (e) { say(e.message, 'bad'); }
  };
  el('srGo').onclick = async () => {
    try { const r = await api('/api/vault/recovery', { passphrase: el('srPass').value }); const c = el('srCode'); c.hidden = false; c.textContent = r.recovery_code; el('srPass').value = ''; sysStatus('New recovery code created. The old one is dead. Store this one now — it is shown once.', 'ok'); }
    catch (e) { sysStatus(e.message, 'bad'); }
  };
  el('sBackup').onclick = () => { location.href = '/api/vault/backup'; };
  el('sLockNow').onclick = () => { closeSystems(); lockNow(); };
}

/* ---- memory ---- */
function memList() { return S.st.memories.length ? S.st.memories.map(m => `<div class="mi"><i>#${m.id}</i><span>${esc(m.fact)}</span><button type="button" data-id="${m.id}" title="Forget">✕</button></div>`).join('') : '<div class="empty">No long-term memories yet.</div>'; }
function buildMemory() {
  pane('memory').innerHTML = `<p class="hint">Long-term memory is injected into the system prompt of every chat. Add with /remember or here. Forgetting erases it from the encrypted file.</p>
<div class="mlist" id="mList">${memList()}</div>
<div class="row2"><input id="mNew" placeholder="Something NYXOR should always know…" style="flex:1;background:var(--bg2);border:1px solid var(--line2);color:var(--text);font:inherit;font-size:12px;padding:8px 10px;outline:0"><button type="button" class="ghost-btn" id="mAdd">REMEMBER</button></div>
<div class="fld-row checks"><button type="button" class="ghost-btn" id="mExp">EXPORT AS JSON (not encrypted)</button><label class="chk"><input type="checkbox" id="mKeys"> include API keys</label><button type="button" class="ghost-btn" id="mImp">IMPORT…</button><input type="file" id="mFile" accept=".json,application/json" hidden></div>
<p class="hint">Import replaces everything except API keys that the file leaves blank. A JSON export is readable by anyone who gets it — prefer the encrypted backup in SECURITY.</p>`;
  const refresh = r => { S.st.memories = r.memories; el('mList').innerHTML = memList(); };
  el('mAdd').onclick = async () => { const v = el('mNew').value.trim(); if (!v) return; try { refresh(await api('/api/memories', { fact: v })); el('mNew').value = ''; } catch (e) { sysStatus(e.message, 'bad'); } };
  el('mList').onclick = async e => { const b = e.target.closest('button'); if (b) refresh(await api('/api/memories/delete', { id: +b.dataset.id })); };
  el('mExp').onclick = () => { location.href = '/api/export' + (el('mKeys').checked ? '?keys=1' : ''); };
  el('mImp').onclick = () => el('mFile').click();
  el('mFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    try { const n = await api('/api/import', JSON.parse(await f.text())); sysStatus('Imported: ' + Object.entries(n.imported).map(([k, v]) => `${v} ${k}`).join(', '), 'ok'); await loadState(); newChat(true); showTab('memory'); }
    catch (err) { sysStatus('Import failed: ' + err.message, 'bad'); }
  };
}
el('sysApply').addEventListener('click', async () => {
  if (!S.online || !S.st) return closeSystems();
  const patch = {}, v = id => el(id).value, checkedRadio = n => (document.querySelector(`input[name="${n}"]:checked`) || {}).value;
  if (S.built.council) {
    patch.council_lead = v('cLead'); const rows = [...document.querySelectorAll('#cRoster .rr')].filter(r => r.querySelector('input[type=checkbox]').checked);
    patch.council_members = rows.map(r => r.querySelector('input[type=checkbox]').value);
    patch.council_roles = {}; rows.forEach(r => { const role = r.querySelector('.role').value.trim(); if (role) patch.council_roles[r.querySelector('input[type=checkbox]').value] = role; });
    patch.council_fix_rounds = +v('cFix'); patch.council_strategy = v('cStrat'); patch.council_member_timeout = +v('cTimeout') || 300; patch.council_grace = +v('cGrace');
    patch.council_auto = el('cAuto').checked; patch.council_review = el('cRev').checked;
  }
  if (S.built.router) Object.assign(patch, { router_mode: checkedRadio('rm'), router_fallback: el('rFb').checked, router_fallback_cloud: el('rFbCloud').checked, router_pool: [...document.querySelectorAll('#rPool input[type=checkbox]')].filter(i => i.checked).map(i => i.value) });
  if (S.built.agent) { patch.terminal_enabled = el('aTerm').checked; patch.agent_approval = checkedRadio('aprv'); patch.agent_allowed_dirs = v('aDirs').split('\n'); patch.agent_max_steps = +v('aSteps') || 15; patch.agent_command_timeout = +v('aTime') || 120; }
  if (S.built.persona) Object.assign(patch, { system_prompt: v('pSys'), temperature: +v('pTemp'), history_turns: +v('pTurns'), think: el('pThink').checked, show_think: el('pShow').checked });
  if (S.built.web) Object.assign(patch, { web_mode: checkedRadio('wm'), web_depth: checkedRadio('wd'), search_provider: v('wProv'), web_keyless: el('wKeyless').checked, web_reader: el('wReader').checked, research_chars: +v('wChars') });
  if (S.built.voice) Object.assign(patch, { voice_engine: v('vEngine'), voice_name: v('vName'), voice_pitch: +v('vPitch'), voice_rate: +v('vRate'), voice_provider: v('vProv'), voice_model: v('vModelT'), voice_voice: v('vVoiceT'), voice_fx: el('vFx').checked, voice_max_chars: +v('vMax'), voice_control: el('vcOn').checked, voice_wake: v('vcWake') });
  if (S.built.security) patch.auto_lock_minutes = +v('sLock');
  try {
    await saveSettings(patch); applyVoiceSettings(); renderMode(); renderModelSel(); renderWeb(); linkNow(); log('systems updated', 'hot');
    if (S.built.voice) { if (S.st.settings.voice_control) startVC(); else stopVC(); }
    el('vaultNote').innerHTML = 'Vault: <b>unlocked</b>' + (S.st.settings.auto_lock_minutes ? ' · auto-lock ' + S.st.settings.auto_lock_minutes + ' min' : '');
    if (patch.agent_approval === 'auto') toast('AUTO MODE is on: in AGENT mode I will act without asking. Switch back in SYSTEMS → AGENT.', 'hot');
    closeSystems();
  } catch (err) { sysStatus('Could not apply: ' + err.message, 'bad'); }
});

/* ================= voice: cloud engine with the Ultron effect chain ================= */
let fxOn = true, ac = null, genToken = 0, pending = 0, playing = 0, qchain = Promise.resolve(), cloudWarned = false, curSrcs = [];
const acx = () => { const A = window.AudioContext || window.webkitAudioContext; if (!ac) ac = new A(); if (ac.state === 'suspended') ac.resume(); return ac; };
function applyVoiceSettings() {
  const s = S.st.settings, v = NX.voiceCfg;
  v.engine = s.voice_engine; v.voiceName = s.voice_name; v.pitch = s.voice_pitch; v.rate = s.voice_rate; v.maxChars = s.voice_max_chars; v.override = null;
  fxOn = s.voice_fx; cloudWarned = false; NX.chooseVoice();
}
function curve(k) { const n = 2048, c = new Float32Array(n); for (let i = 0; i < n; i++) { const x = i * 2 / n - 1; c[i] = (3 + k) * x * 20 * Math.PI / 180 / (Math.PI + k * Math.abs(x)); } return c; }
async function fetchAudio(text, my) {
  try {
    const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ text }, NX.voiceCfg.override || {})) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || 'HTTP ' + r.status); }
    const buf = await acx().decodeAudioData(await r.arrayBuffer());
    return my === genToken ? buf : null;
  } catch (e) {
    if (!cloudWarned) { cloudWarned = true; addMsg('SYS', 'Cloud voice failed: ' + e.message + ' — using the browser voice instead.', 'sys'); NX.voiceCfg.engine = 'browser'; NX.chooseVoice(); }
    return null;
  }
}
function playFx(buf, my) {
  return new Promise(res => {
    if (my !== genToken) return res();
    const a = acx(), out = a.createGain(); out.gain.value = NX.voiceCfg.volume || 1;
    const src = (rate, gain, delay, dest) => { const s = a.createBufferSource(); s.buffer = buf; s.playbackRate.value = rate; const g = a.createGain(); g.gain.value = gain; const d = a.createDelay(0.5); d.delayTime.value = delay; s.connect(d); d.connect(g); g.connect(dest); curSrcs.push(s); return s; };
    let last;
    if (fxOn) {
      const mix = a.createGain(), low = a.createBiquadFilter(), pres = a.createBiquadFilter(), top = a.createBiquadFilter(), shape = a.createWaveShaper(), comp = a.createDynamicsCompressor();
      low.type = 'lowshelf'; low.frequency.value = 220; low.gain.value = 9;
      pres.type = 'peaking'; pres.frequency.value = 2400; pres.gain.value = 3; pres.Q.value = 0.9;
      top.type = 'lowpass'; top.frequency.value = 7200; shape.curve = curve(12); shape.oversample = '2x';
      mix.connect(low); low.connect(pres); pres.connect(shape); shape.connect(top); top.connect(comp);
      const echo = a.createDelay(0.5), fb = a.createGain(), wet = a.createGain(); echo.delayTime.value = 0.085; fb.gain.value = 0.22; wet.gain.value = 0.3;
      comp.connect(out); comp.connect(echo); echo.connect(fb); fb.connect(echo); echo.connect(wet); wet.connect(out);
      const s1 = src(0.84, 1, 0, mix), s2 = src(0.77, 0.55, 0.014, mix);   // detuned double = the metallic edge
      last = s2; s1.start(); s2.start();
    } else { last = src(1, 1, 0, out); last.start(); }
    out.connect(a.destination); playing++;
    last.onended = () => { playing = Math.max(0, playing - 1); res(); };
  });
}
NX.voiceCfg.hook = text => {
  const my = genToken, p = fetchAudio(text, my); pending++;
  qchain = qchain.then(async () => { const buf = await p; if (buf && my === genToken) await playFx(buf, my); }).catch(() => { }).finally(() => { pending = Math.max(0, pending - 1); });
};
NX.voiceCfg.stop = () => { genToken++; curSrcs.forEach(s => { try { s.stop(); } catch { } }); curSrcs = []; qchain = Promise.resolve(); pending = 0; playing = 0; };
NX.voiceCfg.busy = () => pending > 0 || playing > 0;

/* ================= voice control: "Nyxor, what is happening in …" ================= */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const VC = { on: false, rec: null, lastSpeak: 0 };
const wakeWord = () => ((S.st && S.st.settings.voice_wake) || '').trim();
function wakeRe() {
  const w = wakeWord(); if (!w) return null;
  const al = w === 'nyxor' ? ['nyxor', 'nixor', 'nyxer', 'nix or', 'nicks or', 'nyx or', 'knicks or', 'nix are', 'nyx are'] : [w];
  return new RegExp('^\\s*(?:hey|ok|okay)?\\s*(?:' + al.map(x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b[\\s,.:!?-]*', 'i');
}
function startVC(silent) {
  if (!voiceAllowed(silent)) return false;
  if (!SR) { if (!silent) toast('Voice control needs speech recognition: use Chrome or Edge.', 'hot'); return false; }
  if (VC.on) return true;
  VC.on = true; el('vcBtn').classList.add('vc-on'); el('vcBtn').setAttribute('aria-pressed', 'true');
  if (!state.voice) el('voiceBtn').click();
  const r = new SR(); r.continuous = true; r.interimResults = false; r.lang = navigator.language || 'en-US';
  r.onresult = e => { for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) onSpeech(e.results[i][0].transcript.trim()); };
  r.onerror = e => { if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { stopVC(); toast('Microphone permission was refused, so voice control is off.', 'hot'); } };
  r.onend = () => { if (VC.on) setTimeout(() => { try { r.start(); } catch { } }, 350); };
  VC.rec = r; try { r.start(); } catch { }
  log('voice control listening' + (wakeWord() ? ' · wake word "' + wakeWord() + '"' : ''), 'ok'); return true;
}
function stopVC(silent) {
  if (!VC.on && !VC.rec) return;
  VC.on = false; try { VC.rec && VC.rec.stop(); } catch { } VC.rec = null;
  el('vcBtn').classList.remove('vc-on', 'vc-hear'); el('vcBtn').setAttribute('aria-pressed', 'false');
  if (!silent) log('voice control off', 'ok');
}
setInterval(() => { if (NX.isSpeaking()) VC.lastSpeak = Date.now(); }, 150);
function onSpeech(t) {
  if (!t || NX.isSpeaking() || Date.now() - VC.lastSpeak < 1300) return;      // never answer our own voice
  const re = wakeRe(); let cmd = t;
  if (re) { if (!re.test(t)) return; cmd = t.replace(re, ''); }
  cmd = cmd.replace(/[.!?]+$/, '').trim();
  el('vcBtn').classList.add('vc-hear'); setTimeout(() => el('vcBtn').classList.remove('vc-hear'), 900);
  log('heard: ' + (cmd || '(wake word)').slice(0, 50), 'ok');
  if (!cmd) { NX.setMode('awaiting command'); return; }
  if (!voiceCommand(cmd)) { queue.push({ text: cmd, atts: [] }); drain(); }
}
function voiceCommand(cmd) {
  let m;
  const c = cmd.toLowerCase();
  if (/^(stop|abort|cancel|be quiet|shut up|silence|enough)$/.test(c)) { abortRun(); NX.stopSpeech(); return true; }
  if (/^(stop listening|listening off|voice control off)$/.test(c)) { stopVC(); if (S.st) saveSettings({ voice_control: false }); return true; }
  if (/^(new (chat|conversation|transmission)|start over)$/.test(c)) { toast(C.new(), 'ok'); return true; }
  if (/^(mute|voice off|stop talking)$/.test(c)) { if (state.voice) el('voiceBtn').click(); return true; }
  if (/^(unmute|voice on)$/.test(c)) { if (!state.voice) el('voiceBtn').click(); return true; }
  if ((m = c.match(/^(?:switch to |use |go to )?(direct|council|agent)(?: mode)?$/))) { setModeUI(m[1]); return true; }
  if ((m = c.match(/^(?:turn )?(?:web|search|research)(?: search| research)? (on|off|auto)$/))) { C.web(m[1]).then(t => toast(t, 'ok')); return true; }
  if (/^lock(?: the)?(?: vault| down| everything)?$|^lockdown$/.test(c)) { lockNow(); return true; }
  if ((m = c.match(/^(?:open|show) (?:the )?(systems|settings|video(?: studio)?|files|memories)$/))) {
    const w = m[1]; if (/^video/.test(w)) openVideo('', false); else if (w === 'files') C.files(); else if (w === 'memories') C.memories().then(t => toast(t, 'ok')); else openSystems(); return true;
  }
  if ((m = cmd.match(/^(?:generate|make|create) (?:me )?(?:a |an )?video (?:of |about |showing )?(.+)$/i))) { openVideo(m[1], true); return true; }
  if (/^(read|say) (that|it) again$|^repeat( that)?$/.test(c)) { if (S.lastAnswer) { if (!state.voice) el('voiceBtn').click(); NX.stopSpeech(); NX.resetSpeech(); NX.speak(S.lastAnswer); } return true; }
  return false;
}
el('vcBtn').addEventListener('click', async () => {
  if (VC.on) { stopVC(); if (S.st) saveSettings({ voice_control: false }); }
  else if (startVC() && S.st) saveSettings({ voice_control: true });
});

/* ================= video studio ================= */
const vid = { timer: null, sig: new Map() };
const vstat = (t, c) => { const s = el('vStatus'); s.textContent = t; s.className = 'modal-status' + (c ? ' ' + c : ''); };
function openVideo(prefill, auto) {
  if (!S.st) return toast(S.online ? 'Unlock the vault first.' : OFFLINE, 'hot');
  el('vidModal').hidden = false; fillBackends(); if (prefill) el('vPrompt').value = prefill;
  vid.sig.clear(); el('vJobs').innerHTML = ''; refreshJobs(); clearInterval(vid.timer); vid.timer = setInterval(refreshJobs, 2500);
  if (auto) el('vGo').click(); else el('vPrompt').focus();
}
function closeVideo() { el('vidModal').hidden = true; clearInterval(vid.timer); }
function fillBackends() {
  const list = S.st.providers.filter(p => p.kind === 'video'), sel = el('vBackend'), cur = sel.value;
  const ready = p => p.has_key || (p.id === 'colab' && p.base_url);
  sel.innerHTML = list.map(p => `<option value="${ea(p.id)}">${esc(p.name)}${ready(p) ? '' : ' — not set up'}</option>`).join('');
  sel.value = cur && list.some(p => p.id === cur) ? cur : (list.find(ready) || list[0]).id; onBackend();
}
function onBackend() {
  const p = prov(el('vBackend').value); el('vColab').hidden = p.id !== 'colab';
  let row = el('vModelRow');
  if (!row) { row = document.createElement('div'); row.id = 'vModelRow'; row.className = 'fld'; el('vBackend').parentElement.after(row); }
  row.hidden = p.id === 'colab';
  row.innerHTML = p.id === 'colab' ? '' : `<span>Model id</span><input id="vModel" value="${ea(p.model)}" placeholder="${p.protocol === 'fal' ? 'fal-ai/ltx-video' : 'owner/model-name from replicate.com/collections/text-to-video'}" spellcheck="false"><span class="ss ${p.has_key ? 'ok' : 'bad'}">${p.has_key ? 'API key saved.' : 'No key yet — paste it in SYSTEMS → PROVIDERS (quick-add box).'}</span>`;
  if (p.id !== 'colab') el('vModel').onchange = async e => { try { const r = await api('/api/providers', { id: p.id, model: e.target.value }); p.model = r.provider.model; } catch (err) { vstat(err.message, 'bad'); } };
  el('vColabSs').textContent = p.id === 'colab' ? (p.base_url ? 'Connected URL saved. Press GENERATE (or reconnect if the Colab session ended).' : 'Not connected yet.') : '';
}
el('vBackend').addEventListener('change', onBackend);
el('vConnectGo').addEventListener('click', async () => {
  const v = el('vConnect').value.trim(); if (!v) return;
  el('vColabSs').textContent = 'connecting…';
  try {
    const r = await api('/api/keys/add', { key: v, provider: 'colab' });
    S.st = await api('/api/state'); fillBackends(); refreshMatrix();
    el('vColabSs').textContent = r.ok ? '✓ Connected: ' + ((r.test && r.test.message) || 'Colab online') : '✗ ' + (r.error || 'could not connect'); if (r.ok) el('vConnect').value = '';
  } catch (e) { el('vColabSs').textContent = '✗ ' + e.message; }
});
el('vGo').addEventListener('click', async () => {
  const body = { backend: el('vBackend').value, prompt: el('vPrompt').value.trim(), steps: +el('vSteps').value || undefined, frames: +el('vFrames').value || undefined, seed: el('vSeed').value === '' ? undefined : +el('vSeed').value };
  if (!body.prompt) return vstat('Write a prompt first.', 'bad');
  try { await api('/api/video/generate', body); vstat('Queued. You can close this window; the clip will appear in the FILES-style list here when done.', 'ok'); log('video job queued', 'hot'); refreshJobs(); }
  catch (e) { vstat(e.message, 'bad'); }
});
async function refreshJobs() {
  if (el('vidModal').hidden || !S.st) return;
  let jobs; try { jobs = (await api('/api/video/jobs')).jobs; } catch { return; }
  const box = el('vJobs'), keep = new Set(jobs.map(j => j.id));
  [...box.children].forEach(ch => { if (!keep.has(ch.dataset.id)) { ch.remove(); vid.sig.delete(ch.dataset.id); } });
  if (!jobs.length) { box.innerHTML = '<div class="empty">No videos yet.</div>'; return; }
  box.querySelector('.empty')?.remove();
  jobs.forEach((j, idx) => {
    const sig = j.status + ':' + Math.round(j.progress * 100) + ':' + j.error;
    let d = box.querySelector(`.vjob[data-id="${j.id}"]`);
    if (d && vid.sig.get(j.id) === sig) return;
    const fresh = !d; if (fresh) { d = document.createElement('div'); d.dataset.id = j.id; }
    vid.sig.set(j.id, sig); d.className = 'vjob ' + j.status;
    const active = j.status === 'queued' || j.status === 'running';
    d.innerHTML = `<div class="vh"><span>${esc(j.backend.toUpperCase())}</span><span class="vs">${esc(j.status.toUpperCase())}${active ? ' · ' + Math.round(j.progress * 100) + '%' : ''}</span></div><div class="vp">${esc(j.prompt)}</div>` +
      (active ? `<div class="bar"><i style="width:${Math.max(3, Math.round(j.progress * 100))}%"></i></div>` : '') +
      (j.status === 'done' ? `<video controls preload="metadata" src="${ea(j.url)}"></video>` : '') + (j.error ? `<div class="ss bad">${esc(j.error)}</div>` : '') +
      `<div class="vb">${j.status === 'done' ? `<a class="ghost-btn sm" href="${ea(j.url)}" download="nyxor-${j.id}.mp4">DOWNLOAD</a>` : ''}${active ? '<button type="button" class="ghost-btn sm" data-a="cancel">CANCEL</button>' : '<button type="button" class="ghost-btn sm" data-a="del">DELETE</button>'}</div>`;
    if (fresh) box.insertBefore(d, box.children[idx] || null);
  });
}
el('vJobs').addEventListener('click', async e => {
  const b = e.target.closest('button'); if (!b) return;
  const id = b.closest('.vjob').dataset.id;
  try {
    if (b.dataset.a === 'cancel') await api('/api/video/cancel', { id });
    if (b.dataset.a === 'del') { if (!confirm('Delete this video from disk and from the database?')) return; await api('/api/video/delete', { id }); }
    vid.sig.clear(); refreshJobs();
  } catch (err) { vstat(err.message, 'bad'); }
});
el('videoBtn').addEventListener('click', () => openVideo('', false));
el('vClose').addEventListener('click', closeVideo);
el('vidModal').addEventListener('click', e => { if (e.target === el('vidModal')) closeVideo(); });

/* ================= keys + start ================= */
addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!el('prevModal').hidden) closePreview();
  else if (!el('viewModal').hidden) el('viewModal').hidden = true;
  else if (!el('vidModal').hidden) closeVideo();
  else if (!sysModal.hidden) closeSystems();
  else abortRun();
});
setInterval(() => { if (S.online && !S.locked && !document.hidden) refreshMatrix(); }, 20000);
init();
})();
