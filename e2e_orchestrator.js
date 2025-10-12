// e2e_orchestrator.js
// Run:
//   npm i -D playwright
//   npx playwright install chromium
//   # optional: npx playwright install chrome
//   node e2e_orchestrator.js askchip_e2e_tests.json --base https://chibot-ui.onrender.com/ [--chrome] [--dump-ui] [--start "<selector|text|xpath>"] [--start-eval "window.startCall()"]

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
function ensureDir(p){ fs.mkdirSync(p,{recursive:true}); }
function readJSON(p){ return JSON.parse(fs.readFileSync(p,'utf-8')); }
function hasStr(objs,s){ return objs.some(x=>JSON.stringify(x).includes(s)); }
function countStr(objs,s){ return objs.filter(x=>JSON.stringify(x).includes(s)).length; }

function parseCLI(){
  const specPath = process.argv[2] || 'askchip_e2e_tests.json';
  let base = process.env.ASKCHIP_BASE_URL || '';
  const i = process.argv.indexOf('--base');
  if (i !== -1 && process.argv[i+1]) base = process.argv[i+1];
  if (!base) { console.error('Provide base URL, e.g. --base https://chibot-ui.onrender.com/'); process.exit(1); }
  const useChrome = process.argv.includes('--chrome') || process.env.BROWSER === 'chrome';
  const dumpUI = process.argv.includes('--dump-ui');
  const startIdx = process.argv.indexOf('--start');
  const startOverride = startIdx !== -1 ? process.argv[startIdx+1] : (process.env.START_SELECTOR || '');
  const evalIdx = process.argv.indexOf('--start-eval');
  const startEval = evalIdx !== -1 ? process.argv[evalIdx+1] : (process.env.START_EVAL || '');
  return { specPath, base, useChrome, dumpUI, startOverride, startEval };
}

// ---- IN-PAGE SSE collector (uses cookies/session) ----
async function collectAdminSSEInPage(page, { url, ms }) {
  return await page.evaluate(({ url, ms }) => {
    return new Promise(resolve => {
      const logs = [];
      let es;
      try {
        es = new EventSource(url, { withCredentials: true });
        es.onmessage = (e) => { try { logs.push(JSON.parse(e.data)); } catch {} };
        es.onerror = () => { /* ignore; resolve on timeout */ };
      } catch { resolve(logs); return; }
      setTimeout(() => { try { es && es.close(); } catch {} resolve(logs); }, ms);
      window.addEventListener('beforeunload', () => { try { es && es.close(); } catch {} });
    });
  }, { url, ms });
}

async function startAdminCollector(page, url) {
  await page.evaluate((adminUrl) => {
    if (window.__adminCollector?.source) {
      try { window.__adminCollector.source.close(); } catch {}
    }

    const logs = [];
    const collector = {
      startedAt: Date.now(),
      source: null,
      logs,
      lastError: null
    };

    window.__adminLogs = logs;
    window.__adminCollector = collector;

    try {
      const es = new EventSource(adminUrl, { withCredentials: true });
      collector.source = es;
      es.onmessage = (event) => {
        if (!window.__adminLogs) window.__adminLogs = logs;
        try {
          logs.push(JSON.parse(event.data));
        } catch {
          logs.push({ raw: event.data });
        }
      };
      es.onerror = () => { collector.lastError = 'eventsource_error'; };
      window.addEventListener('beforeunload', () => { try { es.close(); } catch {}; });
    } catch (err) {
      collector.lastError = err?.message || String(err);
    }
  }, url);
}

async function harvestAdminLogs(page) {
  const logs = await page.evaluate(() => {
    const data = Array.isArray(window.__adminLogs) ? window.__adminLogs.slice() : [];
    return { data, error: window.__adminCollector?.lastError || null };
  });
  if (logs.error) {
    return logs.data.concat([{ event: 'collector_error', message: logs.error }]);
  }
  return logs.data;
}

async function stopAdminCollector(page) {
  await page.evaluate(() => {
    const collector = window.__adminCollector;
    if (!collector) return;
    if (collector.source) {
      try { collector.source.close(); } catch {}
    }
    collector.stoppedAt = Date.now();
    collector.source = null;
  });
}

// ---- UI dump (buttons + clickables + DOM) ----
async function dumpUI(page, dumpDir){
  const out = [];
  const frames = page.frames();
  for (const f of frames){
    try {
      const buttons = await f.locator('button').all();
      for (const loc of buttons) {
        try {
          const text = (await loc.textContent())?.trim() || '';
          const id = await loc.getAttribute('id');
          const testid = await loc.getAttribute('data-testid');
          const role = await loc.getAttribute('role');
          const aria = await loc.getAttribute('aria-label');
          out.push({ frameUrl: f.url(), tag: 'button', text, id, testid, role, aria });
        } catch {}
      }
      const clickables = await f.locator('a, [role="button"], div[onclick], [class*="btn"]').all();
      for (const loc of clickables) {
        try {
          const text = (await loc.textContent())?.trim() || '';
          const id = await loc.getAttribute('id');
          const testid = await loc.getAttribute('data-testid');
          const role = await loc.getAttribute('role');
          const aria = await loc.getAttribute('aria-label');
          out.push({ frameUrl: f.url(), tag: 'clickable', text, id, testid, role, aria });
        } catch {}
      }
    } catch {}
  }
  fs.writeFileSync(path.join(dumpDir, 'ui_buttons.json'), JSON.stringify(out,null,2));
  fs.writeFileSync(path.join(dumpDir, 'dom.html'), await page.content());
}

async function checkDomExpectations(page, expectDom, artifactsDir, probeId){
  if (!expectDom || !Array.isArray(expectDom.any_of) || !expectDom.any_of.length) return [];
  const selectors = expectDom.any_of;
  const timeout = expectDom.timeout_ms ?? 5000;

  try {
    await page.waitForFunction((sels) => {
      return sels.some(sel => {
        try {
          const el = document.querySelector(sel);
          if (!el) return false;
          const style = window.getComputedStyle(el);
          if (style && (style.visibility === 'hidden' || style.display === 'none')) return false;
          const rect = el.getBoundingClientRect();
          return !!(rect.width || rect.height);
        } catch {
          return false;
        }
      });
    }, selectors, { timeout });
    return [];
  } catch (err) {
    const dir = path.join(artifactsDir, probeId);
    ensureDir(dir);
    try { await page.screenshot({ path: path.join(dir, 'expect_dom_failure.png'), fullPage: true }); } catch {}
    try { fs.writeFileSync(path.join(dir, 'expect_dom_failure.html'), await page.content()); } catch {}
    return [`DOM expectation not met: none of [${selectors.join(', ')}] appeared within ${timeout}ms`];
  }
}

// ---- Start clickers ----
const TEXT_VARIANTS = [
  /^start$/i, /start session/i, /start call/i, /start chat/i, /begin/i, /join/i, /connect/i,
  /let's go/i, /enable mic/i, /enable microphone/i, /talk/i, /call/i
];
const ATTR_VARIANTS = [
  '#startButton', '[data-role="start-btn"]', '[data-testid="start"]', '[data-test="start"]',
  '[data-qa="start"]', '[id*="start"]', '[class*="start"]', '[aria-label*="start" i]',
  '[aria-label*="join" i]', '.start', 'button.start'
];

async function tryClick(locator, meta, attempts){
  try{
    const loc = locator.first();
    await loc.waitFor({ state: 'visible', timeout: 900 });
    await loc.click({ timeout: 1500 });
    attempts.push({ ok:true, ...meta });
    return true;
  }catch(e){
    attempts.push({ ok:false, error: e.message?.slice(0,120), ...meta });
    return false;
  }
}

// JS-level scan (pierces shadow roots, clicks even if overlayed)
async function clickViaEvaluate(target, rxSource, attempts){
  try{
    const ok = await target.evaluate((rxSrc) => {
      const rx = new RegExp(rxSrc, 'i');
      function* allNodes(root){
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null);
        while (walker.nextNode()) yield walker.currentNode;
        // open shadow roots
        for (const el of root.querySelectorAll('*')) {
          const sr = el.shadowRoot;
          if (sr) yield* allNodes(sr);
        }
      }
      for (const el of allNodes(document)){
        const txt = (el.innerText || el.textContent || '').trim();
        const aria = el.getAttribute?.('aria-label') || '';
        if (rx.test(txt) || rx.test(aria)) {
          if (typeof el.click === 'function') { el.click(); return true; }
        }
      }
      return false;
    }, rxSource);
    attempts.push({ ok: ok, selector: 'evaluate:text', text: rxSource });
    return ok;
  } catch(e){
    attempts.push({ ok:false, selector:'evaluate:text', error:e.message?.slice(0,120) });
    return false;
  }
}

// user overrides: selector/xpath/text or eval
async function clickStartWithOverrides(page, startOverride, startEval, attempts){
  if (startEval){
    try{
      const ok = await page.evaluate((js) => {
        try { return !!eval(js); } catch { return false; }
      }, startEval);
      attempts.push({ ok, selector:'--start-eval', text:startEval });
      if (ok) return true;
    }catch(e){ attempts.push({ ok:false, selector:'--start-eval', error:e.message?.slice(0,120) }); }
  }
  if (startOverride){
    if (startOverride.startsWith('//')) { // xpath
      return await tryClick(page.locator(`xpath=${startOverride}`),
        { selector:'--start xpath', text:startOverride, frameUrl:page.url() }, attempts);
    } else if (startOverride.startsWith('text=')) {
      return await tryClick(page.locator(startOverride),
        { selector:'--start text', text:startOverride, frameUrl:page.url() }, attempts);
    } else if (startOverride.startsWith('#') || startOverride.includes('[') || startOverride.includes('.')) {
      return await tryClick(page.locator(startOverride),
        { selector:'--start css', text:startOverride, frameUrl:page.url() }, attempts);
    } else {
      // plain visible text
      return await tryClick(page.getByText(new RegExp(startOverride, 'i')),
        { selector:'--start getByText', text:startOverride, frameUrl:page.url() }, attempts);
    }
  }
  return false;
}

async function clickStartAnywhere(page, attempts){
  // role/text on main frame
  for (const rx of TEXT_VARIANTS) {
    if (await tryClick(page.getByRole('button', { name: rx }),
      { selector:`getByRole(button ${rx})`, text:String(rx), frameUrl:page.url() }, attempts)) return true;
    if (await tryClick(page.getByText(rx),
      { selector:`getByText(${rx})`, text:String(rx), frameUrl:page.url() }, attempts)) return true;
  }
  // attributes on main frame
  for (const css of ATTR_VARIANTS) {
    if (await tryClick(page.locator(css),
      { selector: css, text:'attr', frameUrl:page.url() }, attempts)) return true;
  }
  // heuristic: first few visible buttons
  const buttons = page.locator('button');
  const count = await buttons.count().catch(()=>0);
  for (let i=0;i<Math.min(count, 8);i++){
    const b = buttons.nth(i);
    const txt = (await b.textContent().catch(()=>'')) || '';
    if (TEXT_VARIANTS.some(rx => rx.test(txt))) {
      if (await tryClick(b, { selector:`button#${i}`, text: txt.trim(), frameUrl:page.url() }, attempts)) return true;
    }
  }
  // iframes
  for (const f of page.frames()){
    if (f === page.mainFrame()) continue;
    for (const rx of TEXT_VARIANTS) {
      if (await tryClick(f.getByRole('button', { name: rx }),
        { selector:`frame:getByRole(${rx})`, text:String(rx), frameUrl:f.url() }, attempts)) return true;
      if (await tryClick(f.getByText(rx),
        { selector:`frame:getByText(${rx})`, text:String(rx), frameUrl:f.url() }, attempts)) return true;
    }
    for (const css of ATTR_VARIANTS) {
      if (await tryClick(f.locator(css),
        { selector:`frame:${css}`, text:'attr', frameUrl:f.url() }, attempts)) return true;
    }
  }
  // JS text scan (pierces open shadow roots)
  for (const rx of [/^start$/i, /join/i, /enable mic/i, /let's go/i]) {
    if (await clickViaEvaluate(page, rx.source, attempts)) return true;
    for (const f of page.frames()){
      if (f === page.mainFrame()) continue;
      try { if (await clickViaEvaluate(f, rx.source, attempts)) return true; } catch {}
    }
  }
  return false;
}

// last-resort gesture clicks
async function bruteStart(page, attempts){
  try{
    const v = page.viewportSize() || { width: 1200, height: 800 };
    await page.mouse.click(Math.floor(v.width/2), Math.floor(v.height/2));
    attempts.push({ ok:true, selector:'mouse:center' });
    await page.waitForTimeout(400);
  } catch(e){ attempts.push({ ok:false, selector:'mouse:center', error:e.message?.slice(0,120) }); }

  const btns = page.locator('button');
  const n = await btns.count().catch(()=>0);
  for (let i=0;i<Math.min(n, 6);i++){
    const b = btns.nth(i);
    try { if (await b.isVisible()) { await b.click({ timeout: 700 }); attempts.push({ ok:true, selector:`button#${i}` }); } }
    catch(e){ attempts.push({ ok:false, selector:`button#${i}`, error:e.message?.slice(0,120) }); }
  }
}

function getLatencies(adminLogs){
  const e = adminLogs.find(x => x.event === 'latency_breakdown');
  return e?.ms || null;
}

function evalProbe(probe, adminLogs, consoleLines, wsUrls){
  const findings = [];
  const metrics = {};
  const lat = getLatencies(adminLogs);
  if (lat) metrics.latency_breakdown = lat;

  for (const rule of (probe.expect_signals || [])) {
    if (rule.stream === 'admin') {
      if (rule.must_include) for (const k of rule.must_include) if (!hasStr(adminLogs,k)) findings.push(`Admin missing '${k}'`);
      if (rule.must_not_include) for (const k of rule.must_not_include) if (hasStr(adminLogs,k)) findings.push(`Admin should NOT include '${k}'`);
      if (rule.max_count_per_turn) for (const [k,max] of Object.entries(rule.max_count_per_turn)){
        const c = countStr(adminLogs,k); if (c>max) findings.push(`'${k}' count ${c} > ${max}`);
      }
      if (rule.latency_lt_ms && lat) for (const [k,limit] of Object.entries(rule.latency_lt_ms)){
        const got = lat[k]; if (got==null || got>=limit) findings.push(`Latency '${k}' ${got ?? 'N/A'}ms not < ${limit}ms`);
      }
      if (rule.nlu_flags) {
        const nlu = adminLogs.find(x=>x.event==='nlu') || {};
        const f = rule.nlu_flags;
        if (typeof f.needs_clarification === 'boolean') {
          if (!!nlu.needs_clarification !== f.needs_clarification) findings.push(`nlu.needs_clarification expected ${f.needs_clarification}, got ${nlu.needs_clarification}`);
        }
        if (f.missing_any_of) {
          const missing = nlu.missing || [];
          if (!f.missing_any_of.some(m => missing.includes(m))) findings.push(`nlu.missing should include one of [${f.missing_any_of.join(', ')}], got [${missing.join(', ')}]`);
        }
      }
      if (rule.chips_lte != null) {
        const sm = adminLogs.find(x => String(x.event).includes('suggestions_made')) || {};
        const cnt = (sm.items?.length) ?? sm.count ?? 0;
        if (cnt > rule.chips_lte) findings.push(`chips count ${cnt} > ${rule.chips_lte}`);
      }
      if (rule.goal_fields) {
        const sg = adminLogs.find(x => String(x.event).includes('session_goal')) || {};
        for (const [k,expected] of Object.entries(rule.goal_fields)) {
          if (k === 'confirmed_contains') {
            const arr = sg.confirmed || [];
            for (const item of expected) if (!arr.includes(item)) findings.push(`session_goal.confirmed missing '${item}'`);
          } else if ((sg[k] ?? null) !== expected) {
            findings.push(`session_goal.${k} expected '${expected}', got '${sg[k]}'`);
          }
        }
      }
      if (rule.nlu_must_have_keys) {
        const nlu = adminLogs.find(x=>x.event==='nlu');
        if (!nlu) findings.push('No nlu event observed');
        else for (const k of rule.nlu_must_have_keys) if (!(k in nlu)) findings.push(`nlu missing key '${k}'`);
      }
    }
    if (rule.stream === 'browser_console') {
      if (rule.must_include) for (const s of rule.must_include) if (!consoleLines.some(l=>l.includes(s))) findings.push(`Console missing '${s}'`);
      if (rule.must_not_include) for (const s of rule.must_not_include) if (consoleLines.some(l=>l.includes(s))) findings.push(`Console should NOT include '${s}'`);
    }
    if (rule.stream === 'network') {
      if (rule.must_not_include_query_params || rule.network_has_any_params) {
        const dg = wsUrls.find(u => /deepgram\.com\/v1\/listen/.test(u));
        if (!dg) findings.push('Deepgram WS URL not observed');
        else {
          const url = new URL(dg);
          const bad = (rule.must_not_include_query_params || rule.network_has_any_params).filter(k => url.searchParams.has(k));
          if (bad.length) findings.push(`Deepgram URL contains forbidden params: ${bad.join(', ')}`);
        }
      }
    }
  }

  for (const cond of (probe.fail_if || [])) {
    if (cond.admin_any) for (const s of cond.admin_any) if (hasStr(adminLogs,s)) findings.push(`Fail: admin contains '${s}'`);
    if (cond.admin_missing) for (const s of cond.admin_missing) if (!hasStr(adminLogs,s)) findings.push(`Fail: admin missing '${s}'`);
    if (cond.admin_count_gt) for (const [k,m] of Object.entries(cond.admin_count_gt)){ const c = countStr(adminLogs,k); if (c>m) findings.push(`Fail: '${k}' count ${c} > ${m}`); }
    const lat2 = getLatencies(adminLogs);
    if (cond.latency_gte_ms && lat2) for (const [k,v] of Object.entries(cond.latency_gte_ms)){ const got = lat2[k]; if (got!=null && got>=v) findings.push(`Fail: latency '${k}' ${got}ms >= ${v}ms`); }
    if (cond.chips_gt != null) {
      const sm = adminLogs.find(x => String(x.event).includes('suggestions_made')) || {};
      const cnt = (sm.items?.length) ?? sm.count ?? 0;
      if (cnt > cond.chips_gt) findings.push(`Fail: chips ${cnt} > ${cond.chips_gt}`);
    }
    if (cond.nlu_flags) {
      const nlu = adminLogs.find(x=>x.event==='nlu') || {};
      const f = cond.nlu_flags;
      if (typeof f.needs_clarification === 'boolean') {
        if (!!nlu.needs_clarification !== f.needs_clarification) findings.push(`Fail: nlu.needs_clarification expected ${f.needs_clarification}, got ${nlu.needs_clarification}`);
      }
    }
    if (cond.goal_missing) {
      const sg = adminLogs.find(x => String(x.event).includes('session_goal')) || {};
      for (const k of cond.goal_missing) if (!(k in sg)) findings.push(`Fail: session_goal missing '${k}'`);
    }
    if (cond.nlu_missing_any) {
      const nlu = adminLogs.find(x=>x.event==='nlu') || {};
      for (const k of cond.nlu_missing_any) {
        const parts = k.split('.');
        let cur = nlu, ok = true;
        for (const p of parts) { if (cur && p in cur) cur = cur[p]; else { ok = false; break; } }
        if (!ok) findings.push(`Fail: nlu missing '${k}'`);
      }
    }
  }

  return { passed: findings.length === 0, findings, metrics };
}

async function runProbeOnce(spec, baseUrl, probe, artifactsDir, audioDir, useChrome, dumpUI, startOverride, startEval){
  const audioName = (probe.inputs && probe.inputs.find(x => x.endsWith('.wav'))) || 'sample_sentence.wav';
  const fakeAudio = path.resolve(path.join(audioDir, audioName));
  if (!fs.existsSync(fakeAudio)) {
    return { id: probe.id, pass: false, findings: [`Audio fixture missing: ${audioName}`], metrics: {} };
  }

  const browser = await chromium.launch({
    headless: false,
    channel: useChrome ? 'chrome' : undefined,
    args: [
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${fakeAudio}`,
      '--autoplay-policy=no-user-gesture-required'
    ]
  });

  const context = await browser.newContext();
  await context.grantPermissions(['microphone'], { origin: baseUrl });
  const page = await context.newPage();

  const consoleLines = [];
  page.on('console', m => consoleLines.push(m.text()));
  const wsUrls = [];
  page.on('websocket', ws => { try { wsUrls.push(ws.url()); } catch {} });

  let adminUrl = spec.endpoints?.admin_sse_url;
  if (!adminUrl || /your_domain/i.test(adminUrl)) adminUrl = baseUrl.replace(/\/$/,'') + '/api/v1/admin/logs';

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  // Start admin collector early
  const adminUrlEarly = baseUrl.replace(/\/$/, '') + '/api/v1/admin/logs';
  await startAdminCollector(page, adminUrlEarly);


  const dir = path.join(artifactsDir, probe.id); ensureDir(dir);
  await page.screenshot({ path: path.join(dir, 'after_goto.png') });
  fs.writeFileSync(path.join(dir, 'page_url.txt'), page.url());

  const attempts = [];

  // user overrides first
  if (!(await clickStartWithOverrides(page, startOverride, startEval, attempts))) {
    // heuristics across frames/shadow
    let clicked = await clickStartAnywhere(page, attempts);
    if (!clicked) {
      // brute gestures and retry heuristics
      await bruteStart(page, attempts);
      clicked = await clickStartAnywhere(page, attempts);
    }
    if (!clicked) {
      if (!dumpUI) await dumpUI(page, dir);
      await page.screenshot({ path: path.join(dir, 'start_not_found.png'), fullPage: true });
      fs.writeFileSync(path.join(dir, 'start_click_attempts.json'), JSON.stringify(attempts,null,2));
      await context.close(); await browser.close();
      return { id: probe.id, pass: false, findings: ['Start control not found/clickable'], metrics: {} };
    }
  }

  fs.writeFileSync(path.join(dir, 'start_click_attempts.json'), JSON.stringify(attempts,null,2));

  // Let any navigation settle
  try { await page.waitForLoadState('networkidle', { timeout: 4000 }); } catch {}

  // Start SSE AFTER Start
  let adminLogs = await harvestAdminLogs(page);
  await stopAdminCollector(page);

  // Let the probe’s audio play/flow
  const ua = (probe.user_action || '').toLowerCase();
  if (ua.includes('do not speak')) {
    await sleep(3000);
  } else if (ua.includes('two short back-to-back turns')) {
    await sleep(2200);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await clickStartWithOverrides(page, startOverride, startEval, attempts);
    await clickStartAnywhere(page, attempts);
    await sleep(2200);
  } else if (ua.includes('start talking while chip is speaking')) {
    await sleep(1500);
  } else {
    await sleep(4200);
  }

  // Artifacts
  fs.writeFileSync(path.join(dir,'admin_logs.json'), JSON.stringify(adminLogs,null,2));
  fs.writeFileSync(path.join(dir,'browser_console.json'), JSON.stringify(consoleLines,null,2));
  fs.writeFileSync(path.join(dir,'network_ws.json'), JSON.stringify(wsUrls,null,2));

  const { passed, findings, metrics } = evalProbe(probe, adminLogs, consoleLines, wsUrls);

  // DOM expectations
  const domFindings = await checkDomExpectations(page, probe.expect_dom, artifactsDir, probe.id);
  if (domFindings.length){ findings.push(...domFindings); }

  await context.close(); await browser.close();
  return { id: probe.id, pass: passed, findings, metrics };
}

(async () => {
  const { specPath, base, useChrome, dumpUI, startOverride, startEval } = parseCLI();
  const spec = readJSON(specPath);
  const baseUrl = (spec.endpoints?.ui_base_url && !/your_domain/i.test(spec.endpoints.ui_base_url))
    ? spec.endpoints.ui_base_url
    : base;

  const artifactsDir = path.resolve('artifacts');
  const audioDir = path.resolve('audio');
  ensureDir(artifactsDir);

  const results = [];
  for (const probe of spec.probes) {
    console.log(`\n=== Running ${probe.id} ===`);
    try {
      const r = await runProbeOnce(spec, baseUrl, probe, artifactsDir, audioDir, useChrome, dumpUI, startOverride, startEval);
      results.push(r);
      console.log(`${probe.id}: ${r.pass ? 'PASS' : 'FAIL'}${r.findings.length ? ' – ' + r.findings.join('; ') : ''}`);
    } catch (e) {
      results.push({ id: probe.id, pass: false, findings: [`Runner error: ${e.message}`], metrics: {} });
      console.error(`${probe.id}: Runner error`, e);
    }
  }

  const catalog = spec.recommendations_catalog || {};
  const enriched = results.map(r => {
    const probe = spec.probes.find(p => p.id === r.id) || {};
    const ids = r.pass ? [] : (probe.recommendations_on_fail || []);
    return { ...r, recommendations: ids.map(id => catalog[id]).filter(Boolean) };
  });

  const report = {
    generated_at: new Date().toISOString(),
    summary: {
      total: enriched.length,
      passed: enriched.filter(x => x.pass).length,
      failed: enriched.filter(x => !x.pass).map(x => x.id)
    },
    probes: enriched
  };
  fs.writeFileSync('report.json', JSON.stringify(report, null, 2));

  const lines = [];
  lines.push(`# AskChip E2E Findings\n`);
  lines.push(`**Passed:** ${report.summary.passed}/${report.summary.total}\n`);
  for (const p of report.probes) lines.push(`- **${p.id}**: ${p.pass ? 'PASS' : 'FAIL'}${p.findings.length ? ' — ' + p.findings.join('; ') : ''}`);
  fs.writeFileSync('FINDINGS.md', lines.join('\n'));
  console.log('\nReport written to report.json and FINDINGS.md');
})();
