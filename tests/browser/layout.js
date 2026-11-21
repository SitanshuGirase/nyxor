// Optional real-browser checks (NOT part of the two required suites). Needs Playwright + Chromium: npm i playwright && npx playwright install chromium
// usage: node tests/browser/{layout,scroll}.js [path-to-nyxor-folder]
// usage: node shoot.js <srcdir> <tag> ; takes screenshots of the real UI in headless Chromium
let pw; try { pw = require('playwright'); } catch { pw = require('/opt/npm-tools/node_modules/playwright'); }
const { chromium } = pw;
const { spawn } = require('child_process');
const src = require('path').resolve(process.argv[2] || require('path').join(__dirname, '..', '..')), tag = process.argv[3] || 'x';
(async () => {
  const py = spawn(process.platform === 'win32' ? 'python' : 'python3', [require('path').join(__dirname, 'browser_server.py')], { env: Object.assign({}, process.env, { NYXOR_SRC: src }) });
  let buf = ''; const ports = await new Promise(r => py.stdout.on('data', d => { buf += d; if (buf.includes('\n')) r(JSON.parse(buf.split('\n')[0])); }));
  const base = 'http://127.0.0.1:' + ports.app, mock = 'http://127.0.0.1:' + ports.mock;
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1919, height: 1018 } }); const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error' && !/fontshare|googleapis|gstatic|Failed to load resource/.test(m.text())) errs.push(m.text()); });
  await p.route(/fontshare|googleapis|gstatic/, r => r.abort());
  const skip = () => p.evaluate(() => { const b = document.getElementById('bootSkip'); if (b) b.click(); });
  await p.goto(base + '/'); await p.waitForTimeout(500); await skip();
  await p.waitForSelector('#lockPass'); await p.fill('#lockPass', 'passphrase-123'); await p.fill('#lockPass2', 'passphrase-123'); await p.click('#lockGo');
  await p.waitForSelector('#recoveryCard:not([hidden])'); await p.check('#recOk'); await p.click('#recGo'); await p.waitForTimeout(600);
  const api = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()), [path, body]);
  await api('/api/consent', { flows: {} });      // answer the first-run privacy question (everything stays local) so the screen does not cover the page
  await api('/api/providers', { id: 'ollama', base_url: mock + '/local/v1', enabled: true, model: 'mock-ok' });
  await api('/api/providers', { id: 'openrouter', base_url: mock + '/cloud/v1', api_key: 'sk-mock-SECRET-KEY-0001', enabled: true, model: 'mock-ok' });
  await api('/api/providers/models', { id: 'ollama' }); await api('/api/providers/models', { id: 'openrouter' });
  await api('/api/settings', { mode: 'council', council_lead: 'openrouter:mock-ok', council_members: ['ollama:mock-ok', 'openrouter:mock-ok'], active_provider: 'openrouter', active_model: 'mock-ok' });
  await p.reload(); await p.waitForTimeout(500); await skip(); await p.waitForTimeout(1500);
  const sizes = (process.argv[4] || '1919x1018,1366x768,1100x800,390x800').split(',');
  const report = {};
  for (const sz of sizes) {
    const [w, h] = sz.split('x').map(Number); await p.setViewportSize({ width: w, height: h }); await p.waitForTimeout(500);
    await p.screenshot({ path: `./${tag}-${sz}.png` });
    report[sz] = await p.evaluate(() => {
      const r = id => { const e = document.querySelector(id); if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), b: Math.round(b.bottom), r: Math.round(b.right) }; };
      const hit = (a, b) => a && b && a.w && b.w && !(a.r <= b.x || b.r <= a.x || a.b <= b.y || b.b <= a.y);
      const seg = r('#modeSeg'), sel = r('#modelSel'), chips = r('#chips'), head = r('.console-head'), tx = r('#transcript'), core = r('.core-wrap'), con = r('#console'), roster = r('#rosterBtn'), foc = r('#focusBtn');
      const kids = [...document.querySelectorAll('.modebar > *')].map(e => [e.id || e.className || e.tagName, Math.round(e.getBoundingClientRect().x), Math.round(e.getBoundingClientRect().y), Math.round(e.getBoundingClientRect().width), getComputedStyle(e).display, getComputedStyle(e).flex]); const mb = r('.modebar'); return { kids, mb, head, seg, sel, chips, tx, core, con, overlaps: { segSel: hit(seg, sel), selChips: hit(sel, chips), segChips: hit(seg, chips), selRoster: hit(sel, roster), rosterFocus: hit(roster, foc), headChips: hit(head, chips) }, docScrollX: document.documentElement.scrollWidth > innerWidth, sw: document.documentElement.scrollWidth, wide: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0).slice(0, 8).map(e => (e.id ? '#' + e.id : '') + '.' + String(e.className).slice(0, 40) + ' ' + e.tagName + ' r=' + Math.round(e.getBoundingClientRect().right)) };
    });
  }
  console.log(JSON.stringify({ report, errs }, null, 1));
  await b.close(); py.kill(); process.exit(0);
})().catch(e => { console.log('SHOOT CRASH', e.stack); process.exit(2); });
