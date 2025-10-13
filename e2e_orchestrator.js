// e2e_orchestrator.js
// Run:
//   npm i -D playwright
//   npx playwright install chromium
//   # optional: npx playwright install chrome
//   node e2e_orchestrator.js askchip_e2e_tests.json --base https://chibot-ui.onrender.com/ [--chrome] [--dump-ui] [--start "<selector|text|xpath>"] [--start-eval "window.startCall()"] [--login-email you@example.com]
//     (auto-login defaults to AskChip admin; override via --login-email or ASKCHIP_E2E_LOGIN_EMAIL, disable with "none")

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

function readUInt32LE(buf, offset) { return buf.readUInt32LE(offset); }
function readUInt16LE(buf, offset) { return buf.readUInt16LE(offset); }

function decodeWav(buffer) {
  if (buffer.toString('ascii', 0, 4) !== 'RIFF') throw new Error('Invalid WAV: missing RIFF header');
  if (buffer.toString('ascii', 8, 12) !== 'WAVE') throw new Error('Invalid WAV: missing WAVE header');

  let offset = 12;
  let audioFormat = null;
  let numChannels = null;
  let sampleRate = null;
  let bitsPerSample = null;
  let dataStart = null;
  let dataLength = null;

  while (offset + 8 <= buffer.length) {
    const chunkId = buffer.toString('ascii', offset, offset + 4);
    const chunkSize = readUInt32LE(buffer, offset + 4);
    const chunkDataStart = offset + 8;
    if (chunkId === 'fmt ') {
      audioFormat = readUInt16LE(buffer, chunkDataStart);
      numChannels = readUInt16LE(buffer, chunkDataStart + 2);
      sampleRate = readUInt32LE(buffer, chunkDataStart + 4);
      bitsPerSample = readUInt16LE(buffer, chunkDataStart + 14);
    } else if (chunkId === 'data') {
      dataStart = chunkDataStart;
      dataLength = chunkSize;
      break;
    }
    offset = chunkDataStart + chunkSize + (chunkSize % 2);
  }

  if (audioFormat !== 1) throw new Error('Only PCM WAV files are supported');
  if (!numChannels || !sampleRate || !bitsPerSample) throw new Error('Malformed WAV header');
  if (dataStart == null || dataLength == null) throw new Error('WAV missing data chunk');

  const bytesPerSample = bitsPerSample / 8;
  if (![1, 2, 3, 4].includes(bytesPerSample)) throw new Error(`Unsupported sample size: ${bitsPerSample}`);
  if (bitsPerSample !== 16 && bitsPerSample !== 32) throw new Error('Only 16-bit or 32-bit PCM supported');

  const sampleCount = Math.floor(dataLength / bytesPerSample);
  const samples = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    const byteOffset = dataStart + i * bytesPerSample;
    let value;
    if (bitsPerSample === 16) {
      value = buffer.readInt16LE(byteOffset) / 32768;
    } else {
      value = buffer.readInt32LE(byteOffset) / 2147483648;
    }
    samples[i] = Math.max(-1, Math.min(1, value));
  }

  return { samples, sampleRate, channels: numChannels };
}

function parseSilenceDuration(entry) {
  if (entry == null) return null;
  if (typeof entry === 'number') return entry;
  if (typeof entry === 'object') {
    if (typeof entry.silence_ms === 'number') return entry.silence_ms;
    if (typeof entry.wait_ms === 'number') return entry.wait_ms;
    if (typeof entry.duration_ms === 'number') return entry.duration_ms;
  }
  if (typeof entry !== 'string') return null;
  const str = entry.trim().toLowerCase();
  const match = str.match(/^(silence|wait|pause)[:_]?([0-9]+(?:\.[0-9]+)?)(ms|s|sec|seconds)?$/);
  if (!match) return null;
  const value = parseFloat(match[2]);
  const unit = match[3] || 'ms';
  if (Number.isNaN(value)) return null;
  if (unit.startsWith('s') && !unit.startsWith('ms')) return value * 1000;
  return value;
}

function normalizeInputs(inputs, audioDir) {
  const items = Array.isArray(inputs) && inputs.length ? inputs.slice() : ['sample_sentence.wav'];
  const timeline = [];
  const audioFiles = [];
  let defaultSampleRate = null;
  let defaultChannels = null;
  let audioClipCount = 0;
  let totalDurationMs = 0;

  const pushSilence = (durationMs, sampleRateHint, channelHint) => {
    const duration = Math.max(0, Number(durationMs || 0));
    if (!duration) return;
    const sr = sampleRateHint || defaultSampleRate || 16000;
    const ch = channelHint || defaultChannels || 1;
    timeline.push({ kind: 'silence', durationMs: duration, sampleRate: sr, channels: ch });
    totalDurationMs += duration;
  };

  for (const entry of items) {
    if (typeof entry === 'object' && entry && entry.file && typeof entry.file === 'string' && entry.file.endsWith('.wav')) {
      const filePath = path.resolve(path.join(audioDir, entry.file));
      if (!fs.existsSync(filePath)) throw new Error(`Audio fixture missing: ${entry.file}`);
      const clip = decodeWav(fs.readFileSync(filePath));
      timeline.push({ kind: 'audio', sampleRate: clip.sampleRate, channels: clip.channels, samples: Array.from(clip.samples) });
      audioFiles.push(filePath);
      defaultSampleRate = defaultSampleRate || clip.sampleRate;
      defaultChannels = defaultChannels || clip.channels;
      audioClipCount += 1;
      const frames = clip.samples.length / clip.channels;
      totalDurationMs += (frames / clip.sampleRate) * 1000;
      const extraSilence = entry.silence_after_ms ?? entry.silenceAfterMs ?? entry.pause_after_ms ?? entry.pauseAfterMs;
      if (extraSilence) pushSilence(extraSilence, clip.sampleRate, clip.channels);
      continue;
    }
    if (typeof entry === 'string' && entry.endsWith('.wav')) {
      const filePath = path.resolve(path.join(audioDir, entry));
      if (!fs.existsSync(filePath)) throw new Error(`Audio fixture missing: ${entry}`);
      const clip = decodeWav(fs.readFileSync(filePath));
      timeline.push({ kind: 'audio', sampleRate: clip.sampleRate, channels: clip.channels, samples: Array.from(clip.samples) });
      audioFiles.push(filePath);
      defaultSampleRate = defaultSampleRate || clip.sampleRate;
      defaultChannels = defaultChannels || clip.channels;
      audioClipCount += 1;
      const frames = clip.samples.length / clip.channels;
      totalDurationMs += (frames / clip.sampleRate) * 1000;
      continue;
    }
    const silence = parseSilenceDuration(entry);
    if (silence != null) {
      pushSilence(silence);
      continue;
    }
    throw new Error(`Unsupported probe input: ${JSON.stringify(entry)}`);
  }

  if (!audioClipCount) throw new Error('At least one audio input is required');

  return {
    inputs: items,
    timeline,
    audioFiles,
    defaultSampleRate: defaultSampleRate || 16000,
    defaultChannels: defaultChannels || 1,
    audioClipCount,
    totalDurationMs
  };
}

async function installCustomMicrophone(context, spec) {
  if (!spec || !spec.timeline?.length) return;
  await context.addInitScript(({ timeline, defaultSampleRate, defaultChannels }) => {
    if (typeof window === 'undefined') return;
    const TrackGenerator = window.MediaStreamTrackGenerator;
    const AudioDataCtor = window.AudioData;
    if (typeof TrackGenerator !== 'function' || typeof AudioDataCtor !== 'function') {
      console.warn('Custom microphone unavailable: MediaStreamTrackGenerator/AudioData missing');
      return;
    }

    const baseTimeline = Array.isArray(timeline) ? timeline : [];
    const fallbackSampleRate = defaultSampleRate || 16000;
    const fallbackChannels = defaultChannels || 1;

    const originalGetUserMedia = window.navigator?.mediaDevices?.getUserMedia?.bind(window.navigator.mediaDevices);
    if (!window.navigator.mediaDevices) window.navigator.mediaDevices = {};

    const runTimeline = async (writer) => {
      let timestampUs = 0;
      try {
        for (const event of baseTimeline) {
          const sr = event.sampleRate || fallbackSampleRate;
          const ch = event.channels || fallbackChannels;
          if (event.kind === 'audio') {
            const data = new Float32Array(event.samples);
            const frames = data.length / ch;
            if (frames) {
              const audioData = new AudioDataCtor({
                format: 'f32',
                sampleRate: sr,
                numberOfChannels: ch,
                numberOfFrames: frames,
                timestamp: timestampUs,
                data: data.buffer
              });
              await writer.write(audioData);
              audioData.close();
              timestampUs += Math.round((frames / sr) * 1e6);
            }
          } else if (event.kind === 'silence') {
            const frames = Math.max(0, Math.round((Math.max(0, event.durationMs || 0) / 1000) * sr));
            if (frames) {
              const silent = new Float32Array(frames * ch);
              const audioData = new AudioDataCtor({
                format: 'f32',
                sampleRate: sr,
                numberOfChannels: ch,
                numberOfFrames: frames,
                timestamp: timestampUs,
                data: silent.buffer
              });
              await writer.write(audioData);
              audioData.close();
              timestampUs += Math.round((frames / sr) * 1e6);
            }
          }
        }
      } finally {
        try { await writer.close(); } catch {}
      }
    };

    const createAudioStream = () => {
      const generator = new TrackGenerator({ kind: 'audio' });
      const writer = generator.writable.getWriter();
      runTimeline(writer).catch(err => console.error('Custom microphone timeline failed', err));
      return generator;
    };

    window.navigator.mediaDevices.getUserMedia = async (constraints = {}) => {
      const wantsAudio = !!constraints.audio;
      const wantsVideo = !!constraints.video;
      if (!wantsAudio) {
        if (originalGetUserMedia) return originalGetUserMedia(constraints);
        return new MediaStream();
      }

      let videoStream = null;
      if (wantsVideo && originalGetUserMedia) {
        try {
          const clone = { ...constraints, audio: false };
          videoStream = await originalGetUserMedia(clone);
        } catch (err) {
          console.warn('Video capture via original getUserMedia failed', err);
        }
      }

      const generator = createAudioStream();
      const stream = new MediaStream([generator]);
      if (videoStream) {
        for (const track of videoStream.getVideoTracks()) stream.addTrack(track);
      }
      return stream;
    };
  }, { timeline: spec.timeline, defaultSampleRate: spec.defaultSampleRate, defaultChannels: spec.defaultChannels });
}

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
  const loginIdx = process.argv.indexOf('--login-email');
  let autoLoginEmail = process.env.ASKCHIP_E2E_LOGIN_EMAIL || process.env.ASKCHIP_AUTO_LOGIN_EMAIL || '';
  if (loginIdx !== -1 && process.argv[loginIdx+1]) autoLoginEmail = process.argv[loginIdx+1];
  let disableAutoLogin = false;
  if (typeof autoLoginEmail === 'string') {
    const trimmed = autoLoginEmail.trim();
    const lower = trimmed.toLowerCase();
    if (!trimmed) {
      autoLoginEmail = '';
    } else if (['none', 'off', 'false', 'no'].includes(lower)) {
      disableAutoLogin = true;
      autoLoginEmail = '';
    } else {
      autoLoginEmail = trimmed;
    }
  }
  if (!autoLoginEmail && !disableAutoLogin) autoLoginEmail = 'jwsiejk@purestorage.com';
  return { specPath, base, useChrome, dumpUI, startOverride, startEval, autoLoginEmail };
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
      lastError: null,
      captureState: 'active'
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
      es.onerror = (event) => {
        collector.lastError = 'eventsource_error';
        const status = event?.status ?? event?.target?.status ?? event?.currentTarget?.status ?? null;
        const message = event?.message || '';
        if (status === 403 || /403/.test(String(message))) {
          collector.lastError = 'forbidden';
          collector.captureState = 'disabled';
        }
      };
      window.addEventListener('beforeunload', () => { try { es.close(); } catch {}; });
    } catch (err) {
      collector.lastError = err?.message || String(err);
      if (/403/.test(String(collector.lastError))) {
        collector.captureState = 'disabled';
      }
    }
  }, url);
}

async function harvestAdminLogs(page) {
  const logs = await page.evaluate(() => {
    const data = Array.isArray(window.__adminLogs) ? window.__adminLogs.slice() : [];
    const collector = window.__adminCollector || {};
    return {
      data,
      error: collector.lastError || null,
      captureState: collector.captureState || null
    };
  });

  const events = Array.isArray(logs.data) ? logs.data.slice() : [];
  const meta = {
    error: logs.error || null,
    captureState: logs.captureState || null
  };

  if (meta.error) {
    events.push({ event: 'collector_error', message: meta.error });
  }

  if (!meta.captureState) {
    const hadCollectorError = events.some(entry => {
      if (!entry || typeof entry !== 'object') return false;
      if (entry.event !== 'collector_error') return false;
      if (entry.status === 403) return true;
      const text = [entry.message, entry.raw, entry.error]
        .filter(Boolean)
        .map(v => String(v))
        .join(' ');
      return /403/.test(text);
    });
    if (hadCollectorError) meta.captureState = 'disabled';
  }

  return { events, meta };
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

async function performAutoLogin(page, email) {
  if (!email) return { ok: false, error: 'missing_email' };
  try {
    const result = await page.evaluate(async (emailAddr) => {
      const output = { ok: false, email: emailAddr };
      try {
        if (!emailAddr) { output.error = 'missing_email'; return output; }

        try {
          const cfg = window.__askchip_config = window.__askchip_config || {};
          const authCfg = cfg.auth = cfg.auth || {};
          authCfg.autoLoginEmail = emailAddr;
        } catch {}

        const fetchJson = async (url, init) => {
          const res = await fetch(url, { credentials: 'include', ...(init || {}) });
          let data = null;
          try {
            data = await res.clone().json();
          } catch {
            try { data = await res.text(); } catch {}
          }
          return { status: res.status, ok: res.ok, data };
        };

        let token = null;
        const csrfEndpoints = ['/api/v1/csrf', '/api/v1/auth/csrf'];
        for (const endpoint of csrfEndpoints) {
          try {
            const resp = await fetchJson(endpoint);
            if (resp.ok && resp.data && typeof resp.data === 'object') {
              token = resp.data.csrf || resp.data.token || resp.data.csrf_token || null;
            }
            if (token) break;
          } catch {}
        }

        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['X-CSRF-Token'] = token;

        const loginResp = await fetchJson('/api/v1/auth/login', {
          method: 'POST',
          headers,
          body: JSON.stringify({ email: emailAddr })
        });

        output.login = loginResp;
        if (!loginResp.ok) {
          output.error = (loginResp.data && loginResp.data.error) || `login_failed_${loginResp.status}`;
          return output;
        }

        const meResp = await fetchJson('/api/v1/auth/me');
        output.me = meResp;

        output.profile_complete = !!(
          (meResp.data && meResp.data.profile_complete) ||
          (loginResp.data && loginResp.data.profile_complete) ||
          (loginResp.data && loginResp.data.profile && loginResp.data.profile.profile_complete)
        );

        try {
          if (typeof window.evaluateAuth === 'function') {
            await window.evaluateAuth();
          } else if (typeof window.evaluateAuthGate === 'function') {
            await window.evaluateAuthGate();
          }
        } catch (err) {
          output.evaluate_error = err?.message || String(err);
        }

        output.ok = true;
        return output;
      } catch (err) {
        output.error = err?.message || String(err);
        return output;
      }
    }, email);

    return result || { ok: false, error: 'unknown_auto_login_result' };
  } catch (err) {
    return { ok: false, error: err?.message || String(err) };
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

function normalizeTimestampMs(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const candidates = [
    ['ts_ms', true],
    ['timestamp_ms', true],
    ['time_ms', true],
    ['ms', true],
    ['millis', true],
    ['ts', false],
    ['timestamp', false],
    ['time', false],
    ['at', false]
  ];
  for (const [key, isMs] of candidates) {
    if (!(key in entry)) continue;
    const raw = entry[key];
    const num = typeof raw === 'string' ? Number(raw) : raw;
    if (!Number.isFinite(num)) continue;
    if (isMs || num > 1e12) return num;
    return Math.round(num * 1000);
  }
  return null;
}

function extractStateEvent(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const tsMs = normalizeTimestampMs(entry);
  if (!Number.isFinite(tsMs)) return null;

  const nestedPhaseSources = [entry, entry.payload, entry.data, entry.detail, entry.frame];
  let phase = null;
  for (const src of nestedPhaseSources) {
    if (phase) break;
    if (!src || typeof src !== 'object') continue;
    const candidate = src.phase ?? src.state ?? src.status;
    if (typeof candidate === 'string' && candidate.trim()) {
      phase = candidate.trim();
      break;
    }
  }

  const labelCandidates = [entry.label, entry.event, entry.kind, entry.message, entry.raw];
  let display = null;
  for (const val of labelCandidates) {
    if (typeof val !== 'string') continue;
    const text = val.trim();
    if (!text) continue;
    if (!/state/i.test(text)) continue;
    const match = text.match(/state[^a-z0-9]*([a-z0-9_ -]+)/i);
    if (match && match[1]) {
      const parsed = match[1].trim();
      if (parsed && !phase) phase = parsed;
    }
    if (!display) display = text;
  }

  if (!phase) return null;
  const canonical = phase.trim();
  if (!canonical) return null;
  const key = canonical.toLowerCase();
  const label = display || `state ${canonical}`;
  return { key, label, ts: tsMs, canonical };
}

function detectStateSpam(adminLogs, windowMs) {
  const span = Math.max(0, Number(windowMs) || 0);
  if (span <= 0) return [];
  const events = [];
  for (const entry of adminLogs || []) {
    const evt = extractStateEvent(entry);
    if (evt) events.push(evt);
  }
  if (events.length < 2) return [];
  events.sort((a, b) => a.ts - b.ts);

  const buffers = new Map();
  const worst = new Map();

  for (const evt of events) {
    const buf = buffers.get(evt.key) || [];
    buf.push(evt);
    while (buf.length && evt.ts - buf[0].ts > span) buf.shift();
    buffers.set(evt.key, buf);

    if (buf.length > 1) {
      const info = worst.get(evt.key);
      const firstTs = buf[0].ts;
      const lastTs = buf[buf.length - 1].ts;
      const count = buf.length;
      if (!info || count > info.count || (count === info.count && firstTs < info.start)) {
        worst.set(evt.key, { label: evt.label, count, start: firstTs, end: lastTs });
      }
    }
  }

  const findings = [];
  for (const { label, count, start, end } of worst.values()) {
    const delta = Math.max(0, Math.round(end - start));
    const base = label.toLowerCase().includes('state') ? label : `state ${label}`;
    const parts = [`${base} emitted ${count}× within ${span} ms`];
    if (delta > 0) parts.push(`(Δ${delta} ms)`);
    findings.push(parts.join(' '));
  }
  return findings;
}

function parseBrowserConsoleWS(consoleLines) {
  const events = [];
  const latencies = {};
  const turnMap = new Map();
  const suggestions = [];
  const sessionGoalHints = [];
  const stateEvents = [];
  let nluEvent = null;

  const mergeLatencyMap = (source) => {
    if (!source || typeof source !== 'object') return;
    for (const [key, value] of Object.entries(source)) {
      const num = typeof value === 'string' ? Number(value) : value;
      if (!Number.isFinite(num)) continue;
      latencies[key] = num;
    }
  };

  const extractLatenciesFromEvent = (evt) => {
    if (!evt || typeof evt !== 'object') return;
    if (evt.latency_label && Number.isFinite(evt.latency_ms)) {
      latencies[evt.latency_label] = evt.latency_ms;
    }
    mergeLatencyMap(evt.latency_breakdown || evt.latencies);
    if (evt.metrics && typeof evt.metrics === 'object') {
      mergeLatencyMap(evt.metrics.latency_breakdown || evt.metrics.latencies);
      if (evt.metrics.latency_label && Number.isFinite(evt.metrics.latency_ms)) {
        latencies[evt.metrics.latency_label] = evt.metrics.latency_ms;
      }
    }
    if (evt.meta && typeof evt.meta === 'object') {
      mergeLatencyMap(evt.meta.latency_breakdown || evt.meta.latencies);
    }
  };

  for (const raw of consoleLines || []) {
    if (typeof raw !== 'string') continue;
    const trimmed = raw.trim();
    if (!trimmed.startsWith('[WS→UI]')) continue;
    const idx = trimmed.indexOf(']');
    if (idx === -1) continue;
    const jsonText = trimmed.slice(idx + 1).trim();
    if (!jsonText) continue;
    let evt;
    try {
      evt = JSON.parse(jsonText);
    } catch {
      continue;
    }

    events.push(evt);
    extractLatenciesFromEvent(evt);

    const type = typeof evt.type === 'string' ? evt.type.toLowerCase() : '';
    if (!nluEvent && (type === 'nlu' || evt.event === 'nlu' || evt.nlu)) {
      nluEvent = evt.nlu && typeof evt.nlu === 'object' ? evt.nlu : evt;
    }

    if (type === 'suggestions') {
      suggestions.push(evt);
    }

    if (type.includes('session_goal') || evt.session_goal || evt.goal?.session_goal) {
      const payloads = [];
      if (evt.session_goal && typeof evt.session_goal === 'object') payloads.push(evt.session_goal);
      if (evt.goal && typeof evt.goal === 'object') payloads.push(evt.goal);
      if (!payloads.length && typeof evt === 'object') payloads.push(evt);
      for (const p of payloads) {
        if (p && typeof p === 'object') sessionGoalHints.push(p);
      }
    }

    if (type === 'state' || type === 'phase') {
      stateEvents.push(evt);
    }

    const turnId = evt.turn_id || evt.turnId || evt.turn;
    if (turnId) {
      const turn = turnMap.get(turnId) || {
        turn_id: turnId,
        chunks: [],
        assistant_end_count: 0,
        policy_chips: new Set(),
        suggestions: []
      };
      if (type === 'assistant_chunk') {
        if (typeof evt.text === 'string') turn.chunks.push(evt.text);
        if (Array.isArray(evt.policy_chips)) {
          for (const chip of evt.policy_chips) {
            if (typeof chip === 'string') turn.policy_chips.add(chip);
          }
        }
      }
      if (type === 'assistant_end') {
        turn.assistant_end_count += 1;
      }
      if (type === 'suggestions' && Array.isArray(evt.items)) {
        turn.suggestions = evt.items.slice();
      }
      turnMap.set(turnId, turn);
    }
  }

  const assistantFrames = Array.from(turnMap.values()).map(turn => ({
    turn_id: turn.turn_id,
    text: turn.chunks.join(' ').trim(),
    chunk_count: turn.chunks.length,
    assistant_end_count: turn.assistant_end_count,
    policy_chips: Array.from(turn.policy_chips),
    suggestions: turn.suggestions
  }));

  const chipsMax = suggestions.reduce((max, evt) => {
    const count = Array.isArray(evt.items) ? evt.items.length : 0;
    return count > max ? count : max;
  }, 0);

  return {
    events,
    latencies,
    assistantFrames,
    suggestions,
    chipsMax,
    sessionGoalHints,
    stateEvents,
    nluEvent
  };
}

function evalProbe(probe, adminLogs, consoleLines, wsUrls, adminCaptureMeta = {}){
  const findings = [];
  const metrics = {};
  const adminDisabled = String(adminCaptureMeta.captureState || '').toLowerCase() === 'disabled'
    || adminCaptureMeta.disabled === true;
  const lat = adminDisabled ? null : getLatencies(adminLogs);
  if (lat) metrics.latency_breakdown = lat;
  if (adminDisabled) metrics.admin_stream = 'disabled';

  const wsConsole = parseBrowserConsoleWS(consoleLines);
  if (!metrics.browser_console_ws) metrics.browser_console_ws = {};
  if (Object.keys(wsConsole.latencies).length) {
    metrics.browser_console_ws.latencies = wsConsole.latencies;
  }
  if (wsConsole.assistantFrames.length) {
    metrics.browser_console_ws.assistant_frames = wsConsole.assistantFrames;
  }
  if (wsConsole.suggestions.length) {
    metrics.browser_console_ws.suggestions = wsConsole.suggestions;
  }
  if (wsConsole.sessionGoalHints.length) {
    metrics.browser_console_ws.session_goal_hints = wsConsole.sessionGoalHints;
  }

  let adminWarningLogged = false;
  const ensureAdminWarning = () => {
    if (adminWarningLogged || !adminDisabled) return;
    const probeId = probe?.id || '<unknown>';
    console.warn(`Skipping admin stream checks for probe ${probeId}: admin stream is gated (403).`);
    adminWarningLogged = true;
  };

  for (const rule of (probe.expect_signals || [])) {
    if (rule.stream === 'admin') {
      if (adminDisabled) {
        ensureAdminWarning();
        continue;
      }
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
      if (rule.must_not_spam_state) {
        const windowMs = rule.must_not_spam_state.window_ms ?? rule.must_not_spam_state.windowMs ?? 0;
        const spamFindings = detectStateSpam(adminLogs, windowMs);
        if (spamFindings.length) findings.push(...spamFindings);
      }
      if (rule.nlu_must_have_keys) {
        const nlu = adminLogs.find(x=>x.event==='nlu');
        if (!nlu) findings.push('No nlu event observed');
        else for (const k of rule.nlu_must_have_keys) if (!(k in nlu)) findings.push(`nlu missing key '${k}'`);
      }
    }
    if (rule.stream === 'browser_console_ws') {
      const wsEvents = wsConsole.events;
      if (rule.must_include) {
        for (const k of rule.must_include) {
          if (!hasStr(wsEvents, k)) findings.push(`WS console missing '${k}'`);
        }
      }
      if (rule.must_not_include) {
        for (const k of rule.must_not_include) {
          if (hasStr(wsEvents, k)) findings.push(`WS console should NOT include '${k}'`);
        }
      }
      if (rule.max_count_per_turn) {
        for (const [label, max] of Object.entries(rule.max_count_per_turn)) {
          const count = countStr(wsEvents, label);
          if (count > max) findings.push(`'${label}' count ${count} > ${max}`);
        }
      }
      if (rule.latency_lt_ms) {
        for (const [label, limit] of Object.entries(rule.latency_lt_ms)) {
          const got = wsConsole.latencies[label];
          if (got == null) findings.push(`WS latency '${label}' unavailable`);
          else if (got >= limit) findings.push(`WS latency '${label}' ${got}ms not < ${limit}ms`);
        }
      }
      if (rule.nlu_flags) {
        const nlu = wsConsole.nluEvent && wsConsole.nluEvent.nlu ? wsConsole.nluEvent.nlu : wsConsole.nluEvent;
        if (!nlu) {
          findings.push('No WS NLU event observed');
        } else {
          const flags = rule.nlu_flags;
          if (typeof flags.needs_clarification === 'boolean') {
            if (!!nlu.needs_clarification !== flags.needs_clarification) {
              findings.push(`WS nlu.needs_clarification expected ${flags.needs_clarification}, got ${nlu.needs_clarification}`);
            }
          }
          if (flags.missing_any_of) {
            const missing = Array.isArray(nlu.missing) ? nlu.missing : [];
            if (!flags.missing_any_of.some(m => missing.includes(m))) {
              findings.push(`WS nlu.missing should include one of [${flags.missing_any_of.join(', ')}], got [${missing.join(', ')}]`);
            }
          }
        }
      }
      if (rule.chips_lte != null) {
        if (!wsConsole.suggestions.length) {
          findings.push('WS suggestions payload missing for chips check');
        } else if (wsConsole.chipsMax > rule.chips_lte) {
          findings.push(`WS chips count ${wsConsole.chipsMax} > ${rule.chips_lte}`);
        }
      }
      if (rule.goal_fields) {
        if (!wsConsole.sessionGoalHints.length) {
          findings.push('WS session_goal hints missing');
        } else {
          const latest = wsConsole.sessionGoalHints[wsConsole.sessionGoalHints.length - 1];
          for (const [field, expected] of Object.entries(rule.goal_fields)) {
            if (field === 'confirmed_contains') {
              const confirmed = Array.isArray(latest.confirmed) ? latest.confirmed : [];
              for (const item of expected) {
                if (!confirmed.includes(item)) findings.push(`session_goal.confirmed missing '${item}'`);
              }
            } else if ((latest?.[field] ?? null) !== expected) {
              findings.push(`session_goal.${field} expected '${expected}', got '${latest?.[field]}'`);
            }
          }
        }
      }
      if (rule.must_not_spam_state) {
        const windowMs = rule.must_not_spam_state.window_ms ?? rule.must_not_spam_state.windowMs ?? 0;
        const spamFindings = detectStateSpam(wsConsole.events, windowMs);
        if (spamFindings.length) {
          findings.push(...spamFindings);
        } else if (!wsConsole.stateEvents.length) {
          findings.push('WS state events missing for spam check');
        } else if (!wsConsole.stateEvents.some(evt => normalizeTimestampMs(evt) != null)) {
          findings.push('WS state events missing timestamps for spam check');
        }
      }
      if (rule.nlu_must_have_keys) {
        const nlu = wsConsole.nluEvent && wsConsole.nluEvent.nlu ? wsConsole.nluEvent.nlu : wsConsole.nluEvent;
        if (!nlu) {
          findings.push('No WS NLU event observed');
        } else {
          for (const key of rule.nlu_must_have_keys) {
            if (!(key in nlu)) findings.push(`WS nlu missing key '${key}'`);
          }
        }
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
    const touchesAdmin = cond.admin_any || cond.admin_missing || cond.admin_count_gt
      || cond.latency_gte_ms || cond.chips_gt != null || cond.nlu_flags
      || cond.goal_missing || cond.nlu_missing_any;
    const touchesWs = cond.ws_console_any || cond.ws_console_missing || cond.ws_console_count_gt
      || cond.ws_console_latency_gte_ms || cond.ws_console_chips_gt != null || cond.ws_console_nlu_flags
      || cond.ws_console_goal_missing || cond.ws_console_nlu_missing_any;
    if (adminDisabled && touchesAdmin) {
      ensureAdminWarning();
      continue;
    }
    if (cond.admin_any) for (const s of cond.admin_any) if (hasStr(adminLogs,s)) findings.push(`Fail: admin contains '${s}'`);
    if (cond.admin_missing) for (const s of cond.admin_missing) if (!hasStr(adminLogs,s)) findings.push(`Fail: admin missing '${s}'`);
    if (cond.admin_count_gt) for (const [k,m] of Object.entries(cond.admin_count_gt)){ const c = countStr(adminLogs,k); if (c>m) findings.push(`Fail: '${k}' count ${c} > ${m}`); }
    const lat2 = adminDisabled ? null : getLatencies(adminLogs);
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
    if (touchesWs) {
      if (cond.ws_console_any) {
        for (const s of cond.ws_console_any) if (hasStr(wsConsole.events, s)) findings.push(`Fail: WS console contains '${s}'`);
      }
      if (cond.ws_console_missing) {
        for (const s of cond.ws_console_missing) if (!hasStr(wsConsole.events, s)) findings.push(`Fail: WS console missing '${s}'`);
      }
      if (cond.ws_console_count_gt) {
        for (const [label, max] of Object.entries(cond.ws_console_count_gt)) {
          const count = countStr(wsConsole.events, label);
          if (count > max) findings.push(`Fail: '${label}' count ${count} > ${max}`);
        }
      }
      if (cond.ws_console_latency_gte_ms) {
        for (const [label, limit] of Object.entries(cond.ws_console_latency_gte_ms)) {
          const got = wsConsole.latencies[label];
          if (got == null) findings.push(`Fail: WS latency '${label}' unavailable`);
          else if (got >= limit) findings.push(`Fail: WS latency '${label}' ${got}ms >= ${limit}ms`);
        }
      }
      if (cond.ws_console_chips_gt != null) {
        if (!wsConsole.suggestions.length) findings.push('Fail: WS suggestions payload missing for chips check');
        else if (wsConsole.chipsMax > cond.ws_console_chips_gt) findings.push(`Fail: WS chips ${wsConsole.chipsMax} > ${cond.ws_console_chips_gt}`);
      }
      if (cond.ws_console_nlu_flags) {
        const nlu = wsConsole.nluEvent && wsConsole.nluEvent.nlu ? wsConsole.nluEvent.nlu : wsConsole.nluEvent;
        if (!nlu) {
          findings.push('Fail: No WS NLU event observed');
        } else if (typeof cond.ws_console_nlu_flags.needs_clarification === 'boolean') {
          if (!!nlu.needs_clarification !== cond.ws_console_nlu_flags.needs_clarification) {
            findings.push(`Fail: WS nlu.needs_clarification expected ${cond.ws_console_nlu_flags.needs_clarification}, got ${nlu.needs_clarification}`);
          }
        }
      }
      if (cond.ws_console_goal_missing) {
        const latest = wsConsole.sessionGoalHints[wsConsole.sessionGoalHints.length - 1];
        if (!latest) findings.push('Fail: WS session_goal hints missing');
        else for (const key of cond.ws_console_goal_missing) if (!(key in latest)) findings.push(`Fail: session_goal missing '${key}'`);
      }
      if (cond.ws_console_nlu_missing_any) {
        const nlu = wsConsole.nluEvent && wsConsole.nluEvent.nlu ? wsConsole.nluEvent.nlu : wsConsole.nluEvent;
        for (const key of cond.ws_console_nlu_missing_any) {
          let cur = nlu;
          let ok = true;
          for (const part of key.split('.')) {
            if (cur && typeof cur === 'object' && part in cur) cur = cur[part];
            else { ok = false; break; }
          }
          if (!ok) findings.push(`Fail: WS nlu missing '${key}'`);
        }
      }
    }
  }

  return { passed: findings.length === 0, findings, metrics };
}

async function runProbeOnce(spec, baseUrl, probe, artifactsDir, audioDir, useChrome, dumpUI, startOverride, startEval, autoLoginEmail){
  let normalized;
  try {
    normalized = normalizeInputs(probe.inputs, audioDir);
  } catch (err) {
    return { id: probe.id, pass: false, findings: [err?.message || String(err)], metrics: {} };
  }

  const useCustomMic = normalized.inputs.length > 1;
  const firstAudioPath = normalized.audioFiles[0];
  if (!firstAudioPath) {
    return { id: probe.id, pass: false, findings: ['No audio fixtures found for probe'], metrics: {} };
  }

  const launchArgs = [
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required'
  ];
  if (!useCustomMic) {
    launchArgs.push(`--use-file-for-fake-audio-capture=${firstAudioPath}`);
  }

  const browser = await chromium.launch({
    headless: false,
    channel: useChrome ? 'chrome' : undefined,
    args: launchArgs
  });

  const context = await browser.newContext();
  if (useCustomMic) {
    await installCustomMicrophone(context, normalized);
  }
  await context.grantPermissions(['microphone'], { origin: baseUrl });
  const page = await context.newPage();

  const consoleLines = [];
  page.on('console', m => consoleLines.push(m.text()));
  const wsUrls = [];
  page.on('websocket', ws => { try { wsUrls.push(ws.url()); } catch {} });

  let adminUrl = spec.endpoints?.admin_sse_url;
  if (!adminUrl || /your_domain/i.test(adminUrl)) adminUrl = baseUrl.replace(/\/$/,'') + '/api/v1/admin/logs';

  const dir = path.join(artifactsDir, probe.id); ensureDir(dir);

  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  let autoLoginResult = null;
  if (autoLoginEmail) {
    autoLoginResult = await performAutoLogin(page, autoLoginEmail);
    try { fs.writeFileSync(path.join(dir, 'auto_login_result.json'), JSON.stringify(autoLoginResult, null, 2)); } catch {}
    if (autoLoginResult?.ok) {
      try { await page.waitForTimeout(300); } catch {}
    }
  }

  // Start admin collector before kicking off the call so we capture the whole exchange.
  await startAdminCollector(page, adminUrl);

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
      try { await stopAdminCollector(page); } catch {}
      await context.close(); await browser.close();
      return { id: probe.id, pass: false, findings: ['Start control not found/clickable'], metrics: {} };
    }
  }

  fs.writeFileSync(path.join(dir, 'start_click_attempts.json'), JSON.stringify(attempts,null,2));

  // Let any navigation settle
  try { await page.waitForLoadState('networkidle', { timeout: 4000 }); } catch {}
  
  // Let the probe’s audio play/flow
  const ua = (probe.user_action || '').toLowerCase();
  const playbackDurationMs = Math.max(0, normalized.totalDurationMs || 0);
  if (ua.includes('do not speak')) {
    await sleep(Math.max(3000, playbackDurationMs));
  } else if (ua.includes('two short back-to-back turns')) {
    const waitMs = Math.max(playbackDurationMs + 1200, 2600);
    await sleep(waitMs);
  } else if (ua.includes('start talking while chip is speaking')) {
    await sleep(Math.max(1500, Math.min(playbackDurationMs + 500, 2500)));
  } else {
    await sleep(Math.max(4200, playbackDurationMs + 800));
  }

  // Pull the admin logs after we've allowed the scripted audio to play out.
  let adminLogs = [];
  let adminMeta = { error: null, captureState: null };
  try {
    const capture = await harvestAdminLogs(page);
    adminLogs = Array.isArray(capture.events) ? capture.events : [];
    adminMeta = capture.meta || adminMeta;
  } catch (err) {
    const message = err?.message || String(err);
    adminLogs = [{ event: 'collector_error', message }];
    const disabled = /403/.test(String(message)) ? 'disabled' : adminMeta.captureState;
    adminMeta = { error: message, captureState: disabled };
  } finally {
    try { await stopAdminCollector(page); } catch {}
  }

  // Artifacts
  fs.writeFileSync(path.join(dir,'admin_logs.json'), JSON.stringify(adminLogs,null,2));
  fs.writeFileSync(path.join(dir,'admin_capture_meta.json'), JSON.stringify(adminMeta,null,2));
  fs.writeFileSync(path.join(dir,'browser_console.json'), JSON.stringify(consoleLines,null,2));
  fs.writeFileSync(path.join(dir,'network_ws.json'), JSON.stringify(wsUrls,null,2));

  const { passed, findings, metrics } = evalProbe(probe, adminLogs, consoleLines, wsUrls, adminMeta);

  // DOM expectations
  const domFindings = await checkDomExpectations(page, probe.expect_dom, artifactsDir, probe.id);
  if (domFindings.length){ findings.push(...domFindings); }

  await context.close(); await browser.close();
  return {
    id: probe.id,
    pass: passed,
    findings,
    metrics,
    admin_stream_disabled: String(adminMeta.captureState || '').toLowerCase() === 'disabled',
    admin_capture_meta: adminMeta
  };
}

async function main() {
  const { specPath, base, useChrome, dumpUI, startOverride, startEval, autoLoginEmail, disableAutoLogin } = parseCLI();

  if (autoLoginEmail) {
    console.log(`[auto-login] Using admin email ${autoLoginEmail} (set --login-email or ASKCHIP_E2E_LOGIN_EMAIL to change, use "none" to disable).`);
  } else if (disableAutoLogin) {
    console.log('[auto-login] Disabled; provide --login-email <address> or ASKCHIP_E2E_LOGIN_EMAIL to re-enable.');
  } else {
    console.log('[auto-login] No email configured; set --login-email <address> or ASKCHIP_E2E_LOGIN_EMAIL.');
  }
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
      const r = await runProbeOnce(spec, baseUrl, probe, artifactsDir, audioDir, useChrome, dumpUI, startOverride, startEval, autoLoginEmail);
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

  const adminSkipped = enriched.filter(x => x.admin_stream_disabled).map(x => x.id);
  const report = {
    generated_at: new Date().toISOString(),
    summary: {
      total: enriched.length,
      passed: enriched.filter(x => x.pass).length,
      failed: enriched.filter(x => !x.pass).map(x => x.id),
      admin_stream_skipped: adminSkipped
    },
    probes: enriched
  };
  fs.writeFileSync('report.json', JSON.stringify(report, null, 2));

  const lines = [];
  lines.push(`# AskChip E2E Findings\n`);
  lines.push(`**Passed:** ${report.summary.passed}/${report.summary.total}\n`);
  if (adminSkipped.length) {
    lines.push(`**Admin signals skipped for:** ${adminSkipped.join(', ')} (admin stream is gated)\n`);
  }
  for (const p of report.probes) lines.push(`- **${p.id}**: ${p.pass ? 'PASS' : 'FAIL'}${p.findings.length ? ' — ' + p.findings.join('; ') : ''}`);
  fs.writeFileSync('FINDINGS.md', lines.join('\n'));
  console.log('\nReport written to report.json and FINDINGS.md');
}

if (require.main === module) {
  main().catch(err => {
    console.error(err);
    process.exitCode = 1;
  });
}

module.exports = { evalProbe };
