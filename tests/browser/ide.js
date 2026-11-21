// Optional real-browser walk-through of the terminal, command palette and agent settings. Needs Playwright + Chromium.
// usage: node tests/browser/ide.js [path-to-nyxor-folder]    (screenshots go to the current folder)
let pw; try { pw = require('playwright'); } catch { pw = require('/opt/npm-tools/node_modules/playwright'); }
const { chromium } = pw;
const { spawn } = require('child_process');
const src = require('path').resolve(process.argv[2] || require('path').join(__dirname, '..', '..'));
(async () => {
  const py = spawn(process.platform === 'win32' ? 'python' : 'python3', [require('path').join(__dirname, 'browser_server.py')], { env: Object.assign({}, process.env, { NYXOR_SRC: src }) });
  let buf = ''; const ports = await new Promise(r => py.stdout.on('data', d => { buf += d; if (buf.includes('\n')) r(JSON.parse(buf.split('\n')[0])); }));
  const base = 'http://127.0.0.1:' + ports.app, mock = 'http://127.0.0.1:' + ports.mock;
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1600, height: 900 } }); const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()); });
  const skip = () => p.evaluate(() => { const x = document.getElementById('bootSkip'); if (x) x.click(); });
  const out = {};
  await p.goto(base + '/'); await p.waitForTimeout(500); await skip();
  await p.waitForSelector('#lockPass'); await p.fill('#lockPass', 'passphrase-123'); await p.fill('#lockPass2', 'passphrase-123'); await p.click('#lockGo');
  await p.waitForSelector('#recoveryCard:not([hidden])'); await p.check('#recOk'); await p.click('#recGo'); await p.waitForTimeout(1000);
  const api = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()), [path, body]);
  await api('/api/consent', { flows: {} });
  await api('/api/providers', { id: 'ollama', base_url: mock + '/local/v1', enabled: true, model: 'mock-ok' });
  await api('/api/settings', { mode: 'direct', active_provider: 'ollama', active_model: 'mock-ok', web_mode: 'off' });
  await p.reload(); await p.waitForTimeout(500); await skip(); await p.waitForTimeout(1500);
  // terminal is off: the drawer explains it
  await p.keyboard.press('Control+`'); await p.waitForTimeout(300);
  out.drawerWhenOff = await p.evaluate(() => ({ open: !document.getElementById('drawer').hidden, offNotice: !document.getElementById('termOff').hidden, inputHidden: document.getElementById('termForm').hidden }));
  await p.screenshot({ path: 'ide-1-terminal-off.png' });
  await p.keyboard.press('Control+`');
  // enable it through the real settings route, as the AGENT tab does
  await api('/api/settings', { terminal_enabled: true });
  await p.reload(); await p.waitForTimeout(500); await skip(); await p.waitForTimeout(1500);
  await p.keyboard.press('Control+`'); await p.waitForTimeout(300);
  for (const c of ['echo hello from the terminal', 'pwd', 'cd ..', 'cd /', 'ls | head -3']) { await p.fill('#termInput', c); await p.keyboard.press('Enter'); await p.waitForTimeout(900); }
  out.terminalText = await p.evaluate(() => document.getElementById('termOut').textContent);
  await p.screenshot({ path: 'ide-2-terminal.png' });
  await p.keyboard.press('Control+k'); await p.waitForTimeout(200); await p.keyboard.type('priv'); await p.waitForTimeout(200);
  out.palette = await p.evaluate(() => [...document.querySelectorAll('.pal-item span')].map(x => x.textContent));
  await p.screenshot({ path: 'ide-3-palette.png' }); await p.keyboard.press('Enter'); await p.waitForTimeout(500);
  out.opened = await p.evaluate(() => document.querySelector('.tabs button.on').textContent);
  await p.click('#sysClose'); await p.click('.chip-s[data-open="agent"]'); await p.waitForTimeout(600); await p.screenshot({ path: 'ide-4-agent.png' });
  out.errors = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close(); py.kill(); process.exit(0);
})().catch(e => { console.log('CRASH', e.stack); process.exit(2); });
