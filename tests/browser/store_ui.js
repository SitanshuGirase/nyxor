// Optional real-browser walk-through of the privacy / report / real-telemetry interface. Needs Playwright + Chromium.
// usage: node tests/browser/store_ui.js [path-to-nyxor-folder]    (screenshots are written to the current folder)
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
  const external = []; p.on('request', r => { if (!r.url().startsWith(base) && !r.url().startsWith('data:') && !r.url().startsWith('blob:')) external.push(r.url()); });
  const skip = () => p.evaluate(() => { const x = document.getElementById('bootSkip'); if (x) x.click(); });
  const out = {}; const check = (k, v) => { out[k] = v; };
  await p.goto(base + '/'); await p.waitForTimeout(500); await skip();
  await p.waitForSelector('#lockPass'); await p.fill('#lockPass', 'passphrase-123'); await p.fill('#lockPass2', 'passphrase-123'); await p.click('#lockGo');
  await p.waitForSelector('#recoveryCard:not([hidden])'); await p.check('#recOk'); await p.click('#recGo'); await p.waitForTimeout(1200);
  const api = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json()), [path, body]);
  await api('/api/providers', { id: 'ollama', base_url: mock + '/local/v1', enabled: true, model: 'mock-ok' });
  await api('/api/settings', { mode: 'direct', active_provider: 'ollama', active_model: 'mock-ok', web_mode: 'off' });
  await p.reload(); await p.waitForTimeout(500); await skip(); await p.waitForTimeout(1500);
  check('consentShown', await p.evaluate(() => !document.getElementById('consentModal').hidden));
  check('anyTickedByDefault', await p.evaluate(() => [...document.querySelectorAll('#consentFlows input')].some(i => i.checked)));
  await p.screenshot({ path: 'ui-1-consent.png' });
  await p.click('#consentNone'); await p.waitForTimeout(400);
  await p.fill('#input', 'hello'); await p.keyboard.press('Enter'); await p.waitForTimeout(1800);
  await p.waitForTimeout(3000);
  check('meters', await p.evaluate(() => [...document.querySelectorAll('#meters .meter')].map(m => m.querySelector('span').textContent + '=' + m.querySelector('b').textContent)));
  check('topStats', await p.evaluate(() => ({ state: stateLabel.textContent, uptime: uptime.textContent, models: nodeCount.textContent, alerts: threat.textContent })));
  check('events', await p.evaluate(() => [...document.querySelectorAll('#log li')].map(l => l.textContent).slice(0, 6)));
  check('reportButtons', await p.evaluate(() => document.querySelectorAll('button.rep').length));
  await p.screenshot({ path: 'ui-2-main.png' });
  await p.click('button.rep'); await p.waitForTimeout(300); await p.screenshot({ path: 'ui-3-report.png' }); await p.click('#repClose');
  await p.click('#systemsBtn'); await p.click('.tabs [data-tab="privacy"]'); await p.waitForTimeout(500); await p.screenshot({ path: 'ui-4-privacy.png' });
  check('privacyTicked', await p.evaluate(() => [...document.querySelectorAll('#prFlows input')].filter(i => i.checked).length));
  check('externalRequests', external);
  check('errors', errs);
  console.log(JSON.stringify(out, null, 1));
  await b.close(); py.kill(); process.exit(0);
})().catch(e => { console.log('CRASH', e.stack); process.exit(2); });
