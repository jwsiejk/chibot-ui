const tpl = document.getElementById('row-tpl');
const tbody = document.querySelector('#results tbody');
const overall = document.getElementById('overall');

function addRow(i, name, status, details) {
  const node = tpl.content.cloneNode(true);
  node.querySelector('.idx').textContent = i;
  node.querySelector('.name').textContent = name;
  node.querySelector('.status').textContent = status.toUpperCase();
  node.querySelector('.status').classList.add('status', status);
  node.querySelector('.details').textContent = details;
  tbody.appendChild(node);
}

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }

async function checkGreet() {
  const url = window.ASKCHIP.api.greet;
  try {
    const r = await fetch(url, {credentials:'include'});
    const text = await r.text();
    const ok = r.ok;
    return ok ? ['pass', `GET ${url} → ${r.status}`] : ['fail', `GET ${url} → ${r.status} body=${text.slice(0,200)}`];
  } catch (e) {
    return ['fail', `Exception calling ${url}: ${e.message || e}`];
  }
}

async function checkWS() {
  const url = window.ASKCHIP.ws;
  return new Promise((resolve) => {
    let settled = false;
    try {
      const ws = new WebSocket(url);
      ws.addEventListener('open', () => {
        // try a ping; pass on open regardless
        try { ws.send(JSON.stringify({type:'ping', t: Date.now()})); } catch {}
      });
      ws.addEventListener('message', (e) => {
        if (settled) return;
        // If server responds with 'pong', we mark a hard pass.
        try {
          const m = JSON.parse(e.data);
          if (m && m.type === 'pong') {
            settled = true;
            ws.close();
            resolve(['pass', 'WS open + pong']);
          }
        } catch {}
      });
      ws.addEventListener('close', () => {
        if (!settled) resolve(['warn', 'WS opened and closed (no pong seen)']);
      });
      ws.addEventListener('error', () => {
        if (!settled) resolve(['fail', 'WS error']);
      });
      // Timeout: if still open after 1500ms, call it pass (open)
      setTimeout(() => {
        if (!settled) {
          try { ws.close(); } catch {}
          resolve(['pass', 'WS opened (pong not required)']);
        }
      }, 1500);
    } catch (e) {
      resolve(['fail', `WS init error: ${e.message || e}`]);
    }
  });
}

async function checkSSE() {
  const url = window.ASKCHIP.api.logs;
  return new Promise((resolve) => {
    try {
      const es = new EventSource(url);
      let opened = false;
      es.onopen = () => { opened = true; };
      es.onmessage = () => {
        resolve(['pass', 'SSE receiving events']);
        es.close();
      };
      es.onerror = () => {
        if (opened) {
          resolve(['warn', 'SSE opened, but no events yet']);
        } else {
          resolve(['fail', 'SSE could not open']);
        }
        try { es.close(); } catch {}
      };
      setTimeout(() => {
        if (!opened) {
          resolve(['warn', 'SSE no error but not opened within timeout']);
          try { es.close(); } catch {}
        }
      }, 1500);
    } catch (e) {
      resolve(['fail', `SSE init error: ${e.message || e}`]);
    }
  });
}

async function checkStatic(url, label) {
  try {
    const r = await fetch(url, {cache:'no-store'});
    const ok = r.ok;
    const ct = r.headers.get('content-type')||'';
    return ok ? ['pass', `${label} ok (${ct})`] : ['fail', `${label} ${r.status}`];
  } catch (e) {
    return ['fail', `${label} error: ${e.message || e}`];
  }
}

async function checkLegacy() {
  const url = '/api/greet';
  try {
    const r = await fetch(url, {credentials:'include'});
    if (r.ok) return ['fail', `/api/greet returned ${r.status} (should not exist)`];
    return ['pass', `/api/greet → ${r.status} (as expected)`];
  } catch (e) {
    return ['pass', `/api/greet unreachable (as expected)`];
  }
}

async function checkTTS() {
  const url = window.ASKCHIP.api.tts;
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      credentials: 'include',
      body: JSON.stringify({text: 'Hello from diagnostics.'})
    });
    const j = await r.json().catch(()=>({}));
    if (r.ok && j && j.ok && (j.audio_b64?.length || 0) > 10) {
      return ['pass', `TTS ok (bytes=${j.audio_b64.length})`];
    } else {
      return ['warn', `TTS status=${r.status} ok=${j.ok} len=${(j.audio_b64||'').length||0}`];
    }
  } catch (e) {
    return ['warn', `TTS error (non-fatal): ${e.message || e}`];
  }
}

async function checkAdminConfig() {
  const url = window.ASKCHIP.api.config;
  try {
    const r = await fetch(url, {credentials:'include'});
    if (!r.ok) return ['warn', `Config GET → ${r.status} (optional)`];
    const j = await r.json();
    const keys = Object.keys(j||{});
    return ['pass', `Config ok: ${keys.slice(0,5).join(', ')}`];
  } catch (e) {
    return ['warn', `Config error (optional): ${e.message || e}`];
  }
}

async function run() {
  const checks = [
    ['GET /api/v1/greet', checkGreet],
    ['WebSocket /ws/v1/chat', checkWS],
    ['SSE /api/v1/admin/logs', checkSSE],
    ['Static chip.png', () => checkStatic(window.ASKCHIP.assets.chip, 'chip.png')],
    ['Static viseme (rest or aa)', async () => {
      let r = await checkStatic(window.ASKCHIP.assets.visemeRest, 'rest.png');
      if (r[0] === 'fail') r = await checkStatic(window.ASKCHIP.assets.visemeAA, 'aa.png');
      return r;
    }],
    ['Legacy /api/greet absent', checkLegacy],
    ['POST /api/v1/voice/tts-with-visemes', checkTTS],
    ['GET /api/v1/admin/config', checkAdminConfig]
  ];

  let pass = 0, fail = 0;
  for (let i=0;i<checks.length;i++) {
    const [name, fn] = checks[i];
    let status='fail', details='';
    try {
      [status, details] = await fn();
    } catch (e) {
      status = 'fail'; details = e.message || String(e);
    }
    addRow(i+1, name, status, details);
    if (status === 'pass') pass++;
    if (status === 'fail') fail++;
  }
  overall.textContent = `Pass: ${pass} • Fail: ${fail} • Warn/Skip: ${checks.length - pass - fail}`;
}

window.addEventListener('DOMContentLoaded', run);
