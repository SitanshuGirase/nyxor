// Optional real-browser checks (NOT part of the two required suites). Needs Playwright + Chromium: npm i playwright && npx playwright install chromium
// usage: node tests/browser/{layout,scroll}.js [path-to-nyxor-folder]
let pw; try { pw = require('playwright'); } catch { pw = require('/opt/npm-tools/node_modules/playwright'); }
const { chromium } = pw;
const { spawn } = require('child_process');
const src = require('path').resolve(process.argv[2] || require('path').join(__dirname, '..', '..'));
(async () => {
  const py = spawn(process.platform === 'win32' ? 'python' : 'python3', [require('path').join(__dirname, 'browser_server.py')], { env: Object.assign({}, process.env, { NYXOR_SRC: src }) });
  let buf = ''; const ports = await new Promise(r => py.stdout.on('data', d => { buf += d; if (buf.includes('\n')) r(JSON.parse(buf.split('\n')[0])); }));
  const base = 'http://127.0.0.1:' + ports.app, mock = 'http://127.0.0.1:' + ports.mock;
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1366, height: 768 } }); const p = await ctx.newPage();
  await p.route(/fontshare|googleapis|gstatic/, r => r.abort());
  const skip = () => p.evaluate(() => { const x = document.getElementById('bootSkip'); if (x) x.click(); });
  await p.goto(base + '/'); await p.waitForTimeout(500); await skip();
  await p.waitForSelector('#lockPass'); await p.fill('#lockPass', 'passphrase-123'); await p.fill('#lockPass2', 'passphrase-123'); await p.click('#lockGo');
  await p.waitForSelector('#recoveryCard:not([hidden])'); await p.check('#recOk'); await p.click('#recGo'); await p.waitForTimeout(600);
  const api = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()), [path, body]);
  await api('/api/consent', { flows: {} });      // answer the first-run privacy question (everything stays local) so the screen does not cover the page
  await api('/api/providers', { id: 'ollama', base_url: mock + '/local/v1', enabled: true, model: 'mock-slow' });
  await api('/api/settings', { mode: 'direct', active_provider: 'ollama', active_model: 'mock-slow', web_mode: 'off' });
  await p.reload(); await p.waitForTimeout(500); await skip(); await p.waitForTimeout(1500);
  const st = () => p.evaluate(() => { const t = document.getElementById('transcript'); const j = document.getElementById('jumpBtn'); return { top: Math.round(t.scrollTop), max: Math.round(t.scrollHeight - t.clientHeight), jump: j ? !j.hidden : null }; });
  await p.fill('#input', 'write a very long answer'); await p.keyboard.press('Enter');
  // wait until the answer overflows the chat
  for (let i = 0; i < 60; i++) { const s = await st(); if (s.max > 300) break; await p.waitForTimeout(250); }
  const before = await st();
  await p.mouse.move(700, 400); await p.mouse.wheel(0, -500); await p.waitForTimeout(150);
  const afterWheel = await st();
  await p.waitForTimeout(2500);                       // keep streaming while the reader looks at older text
  const later = await st();
  const drifted = later.max - later.top;              // distance from the bottom after more text arrived
  // press LATEST (new build) or wheel back down (old build) and check the pin re-engages
  const jumpShown = later.jump;
  if (later.jump) await p.click('#jumpBtn'); else await p.mouse.wheel(0, 100000);
  await p.waitForTimeout(1200);
  const pinned = await st();
  const result = { before, afterWheel, later, readerStayedPut: later.top <= afterWheel.top + 5, gapAfterMoreText: drifted, jumpShown, afterJump: pinned, pinnedBottom: pinned.max - pinned.top < 12 };
  console.log(JSON.stringify(result));
  await b.close(); py.kill(); process.exit(0);
})().catch(e => { console.log('CRASH', e.stack); process.exit(2); });
