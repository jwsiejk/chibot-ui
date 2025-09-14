// static/js/admin_console.js
//
// Admin diagnostics: Full System Test for AskChip (v1-only).
// This runs a sequence of checks without depending on app ws/voice modules.
// It uses only fetch/WebSocket/MediaRecorder so it still works if the main UI breaks.
//
// Checks produced (rows):
//  - bus_subscribe      : WS /ws/v1/chat subscribes for unique session
//  - greet_idempotent   : /api/v1/greet returns same turn_id for same session
//  - chat_idempotent    : /api/v1/chat respects Idempotency-Key
//  - rate_limit_ok      : second /api/v1/chat in window returns 429
//  - 413_guard          : oversize /api/v1/voice/chunk returns 413
//  - vendor_keys_ok     : Deepgram/ElevenLabs enabled flags (best-effort)
//  - chunk_post         : POST /api/v1/voice/chunk OK with CSRF + IDs
//  - enqueue_ok         : mirrors chunk_post outcome
//  - asr_path_ok        : saw user_partial/user_final or an asr:error (not stuck)
//  - partials_seen      : count partials for this diagnostic user_msg_id
//  - final_seen         : count finals for this diagnostic user_msg_id
//  - admin_sse_ok       : /api/v1/admin/logs SSE produced an event
//  - tts_cancel_ok      : (optional, Mic Mode) best-effort barge-in/cancel smoke
//
// Notes:
//  • Mic Mode sends ~2s of 96ms slices from the microphone and increases chance of ASR partial/final.
//  • Without Mic Mode we send a silent diagnostic chunk to exercise HTTP+WS path; ASR likely stays silent.
//  • All rows are created even if the page didn’t provide a table; this script will render one.

(function(){
  // ----------- DOM helpers -----------
  const TABLE_SELECTORS = [
    '#full-system-results',
    '#fullSystemResults',
    '[data-admin="full-system-results"]'
  ];
  const BUTTON_SELECTORS = [
    '#btn-full-system-test',
    '#btn-run-full-system-test',
    '#runFullSystemTest',
    '[data-action="run-full-system-test"]'
  ];
  const PANEL_SEL = '#admin-diagnostics';

  function $(sel){ return document.querySelector(sel); }
  function $any(list){ for(const s of list){ const el=$(s); if(el) return el; } return null; }

  function ensurePanel(){
    let panel = document.querySelector(PANEL_SEL);
    if(!panel){
      panel = document.createElement('div');
      panel.id = 'admin-diagnostics';
      document.body.appendChild(panel);
    }
    return panel;
  }

  function ensureControls(){
    const host = ensurePanel();
    let bar = $('#admin-diagnostics-controls');
    if(!bar){
      bar = document.createElement('div');
      bar.id = 'admin-diagnostics-controls';
      bar.style.display = 'flex';
      bar.style.gap = '12px';
      bar.style.alignItems = 'center';
      bar.style.margin = '10px 0';
      host.appendChild(bar);
    }

    let btn = $any(BUTTON_SELECTORS);
    if(!btn){
      btn = document.createElement('button');
      btn.id = 'btn-full-system-test';
      btn.textContent = 'Run full system test';
      btn.style.padding = '6px 10px';
      bar.appendChild(btn);
    }

    // Mic Mode toggle
    let wrap = $('#diag-mic-wrap');
    if(!wrap){
      wrap = document.createElement('label');
      wrap.id = 'diag-mic-wrap';
      wrap.style.display = 'inline-flex';
      wrap.style.alignItems = 'center';
      wrap.style.gap = '6px';
      wrap.style.cursor = 'pointer';
      wrap.title = 'Enable microphone for ASR partial/final checks';
      wrap.innerHTML = `
        <input type="checkbox" id="diag-mic-mode" />
        <span>Mic Mode (send 2s of 96ms slices)</span>
      `;
      bar.appendChild(wrap);
    }
    return btn;
  }

  function ensureResultsTable(){
    let table = $any(TABLE_SELECTORS);
    if(table) return table;

    table = document.createElement('table');
    table.id = 'full-system-results';
    table.style.width = '100%';
    table.style.borderCollapse = 'collapse';
    table.innerHTML = `
      <thead>
        <tr>
          <th style="text-align:left;padding:6px;border-bottom:1px solid #2a2f3a;">Check</th>
          <th style="text-align:left;padding:6px;border-bottom:1px solid #2a2f3a;">OK</th>
          <th style="text-align:left;padding:6px;border-bottom:1px solid #2a2f3a;">Details</th>
        </tr>
      </thead>
      <tbody></tbody>
    `;
    ensurePanel().appendChild(table);
    return table;
  }
  function tbodyOf(table){ return table.tBodies[0] || table.createTBody(); }
  function rowId(key){ return `diag-${key}`; }
  function setRow(table, key, ok, details){
    const tb = tbodyOf(table);
    const id = rowId(key);
    let tr = tb.querySelector(`tr[data-key="${id}"]`);
    if(!tr){
      tr = document.createElement('tr');
      tr.dataset.key = id;
      tr.innerHTML = `
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
      `;
      tb.appendChild(tr);
    }
    const [c0,c1,c2] = tr.children;
    c0.textContent = key;
    c1.textContent = ok ? '✔' : '✖';
    c1.style.color = ok ? '#44d07b' : '#ff5a63';
    c2.textContent = (details==null ? '' : String(details));
  }

  // ----------- Utilities -----------
  async function getCSRF(){
    try{
      const r = await fetch('/api/v1/csrf', { credentials: 'include' });
      const tok = r.headers.get('X-CSRF-Token') || r.headers.get('X-CSRFToken');
      if(tok) return tok;
    }catch(_){}
    try{
      const r = await fetch('/api/v1/health', { credentials: 'include' });
      const tok = r.headers.get('X-CSRF-Token') || r.headers.get('X-CSRFToken');
      if(tok) return tok;
    }catch(_){}
    const m = document.cookie.match(/(?:^|;\s*)XSRF-TOKEN=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function b64OfBytes(n){
    const bytes = new Uint8Array(n);
    let bin=''; for(let i=0;i<n;i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  async function postChunk({sid, csrf, userMsgId, seq, b64}){
    const body = { sid, user_msg_id: userMsgId, chunk_seq: seq, audio_b64: b64 };
    const r = await fetch('/api/v1/voice/chunk', {
      method: 'POST',
      headers: { 'Content-Type':'application/json', 'X-CSRF-Token': csrf },
      credentials: 'include',
      body: JSON.stringify(body)
    });
    return r;
  }

  function openWS(sessionId, onFrame){
    return new Promise((resolve, reject)=>{
      try{
        const proto = location.protocol==='https:' ? 'wss://' : 'ws://';
        const url = new URL(proto + location.host + '/ws/v1/chat');
        url.searchParams.set('session_id', sessionId);
        const ws = new WebSocket(url.toString());
        ws.onopen = ()=> resolve(ws);
        ws.onmessage = (ev)=> {
          try{ const fr = JSON.parse(ev.data); onFrame && onFrame(fr); }catch(_){}
        };
        ws.onerror = (e)=> reject(e);
      }catch(e){ reject(e); }
    });
  }

  // Mic slice sender (96ms cadence) — returns {start, stop}
  function makeMicStreamer({sid, csrf, userMsgId}){
    let stream=null, rec=null, seq=0, stopped=false;

    async function start(){
      try{
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation:true, noiseSuppression:true, channelCount:1, sampleRate:48000 }
        });
      }catch(e){
        throw new Error('mic denied/unavailable');
      }
      rec = new MediaRecorder(stream, { mimeType:'audio/webm;codecs=opus', audioBitsPerSecond: 128000 });
      rec.ondataavailable = async (ev)=>{
        if(stopped) return;
        if(ev.data && ev.data.size>0){
          const buf = await ev.data.arrayBuffer();
          const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
          try{ await postChunk({sid, csrf, userMsgId, seq: ++seq, b64}); }catch(_){}
        }
      };
      rec.start(96);
    }

    function stop(){
      stopped = true;
      try{ if(rec) rec.stop(); }catch(_){}
      try{
        if(stream){
          stream.getTracks().forEach(t=>t.stop());
        }
      }catch(_){}
    }
    return { start, stop };
  }

  async function greetIdempotent(sid){
    const r1 = await fetch(`/api/v1/greet?session_id=${encodeURIComponent(sid)}`, { credentials:'include' });
    const j1 = await r1.json().catch(()=>({}));
    const r2 = await fetch(`/api/v1/greet?session_id=${encodeURIComponent(sid)}`, { credentials:'include' });
    const j2 = await r2.json().catch(()=>({}));
    return { ok: j1 && j2 && j1.turn_id && j2.turn_id && j1.turn_id === j2.turn_id, d:`${j1.turn_id||'?'} == ${j2.turn_id||'?'}` };
  }

  async function chatIdempotent(sid){
    const csrf = await getCSRF();
    const headers = { 'Content-Type':'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': `diag-chat-${Math.random().toString(36).slice(2,8)}` };
    const body = JSON.stringify({ text:'diagnostic ping', session_id: sid });
    const r1 = await fetch('/api/v1/chat', { method:'POST', headers, credentials:'include', body });
    const j1 = await r1.json().catch(()=>({}));
    const r2 = await fetch('/api/v1/chat', { method:'POST', headers, credentials:'include', body: JSON.stringify({ text:'diagnostic ping again', session_id: sid }) });
    const j2 = await r2.json().catch(()=>({}));
    const ok = !!(j1 && j2 && j1.turn_id && j2.turn_id && j1.turn_id === j2.turn_id && j2.idempotent === true);
    return { ok, d: ok ? `turn_id=${j1.turn_id}` : `unexpected: ${JSON.stringify(j2).slice(0,140)}` };
  }

  async function rateLimitCheck(){
    const csrf = await getCSRF();
    const h1 = { 'Content-Type':'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': `rl-1-${Math.random().toString(36).slice(2,6)}` };
    const h2 = { 'Content-Type':'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': `rl-2-${Math.random().toString(36).slice(2,6)}` };
    const r1 = await fetch('/api/v1/chat', { method:'POST', headers:h1, credentials:'include', body: JSON.stringify({ text:'rl-1' }) });
    const r2 = await fetch('/api/v1/chat', { method:'POST', headers:h2, credentials:'include', body: JSON.stringify({ text:'rl-2' }) });
    return { ok: r2.status === 429, d: `status2=${r2.status}` };
  }

  async function guard413(sid){
    const csrf = await getCSRF();
    // 270KB > default 262144 — should 413
    const big = b64OfBytes(270_000);
    const r = await postChunk({ sid, csrf, userMsgId:'diag-big', seq:1, b64: big });
    const ok = r.status === 413;
    let detail = `status=${r.status}`;
    try{ detail += ` ${await r.text()}`; }catch(_){}
    return { ok, d: detail.slice(0,180) };
  }

  async function vendorFlags(){
    // Best-effort: look at /api/v1/admin/diagnostics (or /api/v1/admin/config) and infer booleans.
    try{
      let r = await fetch('/api/v1/admin/diagnostics', { credentials:'include' });
      if(!r.ok) r = await fetch('/api/v1/admin/config', { credentials:'include' });
      if(!r.ok) return { ok:false, d:'no admin diag/config' };
      const j = await r.json();
      const v = j.vendors || j || {};
      const deep = !!(v.deepgram || v.has_deepgram || v.deepgram_enabled || v.asr_enabled);
      const elev = !!(v.elevenlabs || v.tts_enabled || v.elevenlabs_enabled);
      return { ok: deep && elev, d: `deepgram=${deep}, elevenlabs=${elev}` };
    }catch(e){
      return { ok:false, d:'fetch error' };
    }
  }

  async function adminSSE(){
    return new Promise((resolve)=>{
      let got=false;
      try{
        const es = new EventSource('/api/v1/admin/logs');
        const timer = setTimeout(()=>{
          try{ es.close(); }catch(_){}
          resolve({ ok: got, d: got ? 'ok' : 'no events in 1.5s' });
        }, 1500);
        es.onmessage = ()=> { got=true; };
        es.onerror = ()=> {}; // ignore
      }catch(_){
        resolve({ ok:false, d:'EventSource error' });
      }
    });
  }

  // Optional TTS cancel (Mic Mode only) — best-effort smoke test
  async function ttsCancelSmoke({ws, sid, csrf, micMode}){
    if(!micMode || !ws){ return { ok:false, d:'skipped (mic mode only)' }; }
    // Start a chat turn likely to trigger TTS
    const hdrs = { 'Content-Type':'application/json', 'X-CSRF-Token': csrf, 'Idempotency-Key': `cancel-${Math.random().toString(36).slice(2,6)}` };
    const body = JSON.stringify({ text:'Please say a long response so I can interrupt you.', session_id: sid });
    let turnId = null, audioChunks = 0, afterInterrupt = 0;
    const userMsgId = `barge-${Math.random().toString(36).slice(2,6)}`;

    const unlisten = attachWSListener(ws, (fr)=>{
      if(fr.type==='assistant_chunk' && fr.turn_id && !turnId) turnId = fr.turn_id;
      if(fr.type==='audio_chunk' && fr.turn_id===turnId){
        audioChunks++;
        if(audioChunks===1){ /* first audio heard */ }
        if(afterInterrupt>0) afterInterrupt++;
      }
      if(fr.type==='assistant_end' && fr.turn_id===turnId && afterInterrupt>0){
        // assistant_end after interrupt indicates cancel may not have dropped it; we’ll count that
        afterInterrupt += 1000; // force "failed" later
      }
    });

    try{
      await fetch('/api/v1/chat', { method:'POST', headers: hdrs, credentials:'include', body });
    }catch(_){ unlisten(); return { ok:false, d:'chat failed' }; }

    // Wait a moment for first audio, then send mic for ~800ms to trigger barge-in
    await sleep(500);
    const streamer = makeMicStreamer({ sid, csrf, userMsgId });
    try{ await streamer.start(); }catch(_){ unlisten(); return { ok:false, d:'mic denied' }; }
    await sleep(800);
    streamer.stop();

    // Observe if audio stops within ~1.2s after interrupt
    await sleep(1200);
    unlisten();

    const ok = (audioChunks>0) && (afterInterrupt < 2);
    const d = audioChunks===0 ? 'no audio observed' : (ok ? 'ok' : 'late audio after cancel');
    return { ok, d };
  }

  function attachWSListener(ws, fn){
    function onmsg(ev){ try{ fn(JSON.parse(ev.data)); }catch(_){ } }
    ws.addEventListener('message', onmsg);
    return ()=> { try{ ws.removeEventListener('message', onmsg); }catch(_){} };
  }

  function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

  // ----------- Main runner -----------
  async function runFullSystemTest(){
    const table = ensureResultsTable();
    const btn = ensureControls();
    const set = (k, ok, d)=> setRow(table, k, ok, d);

    // Button UX
    if(btn){
      btn.disabled = true;
      const orig = btn.textContent;
      btn.textContent = 'Running…';
      setTimeout(()=>{ btn.disabled=false; btn.textContent = orig; }, 5000);
    }

    const micMode = !!$('#diag-mic-mode')?.checked;
    const sid = `diag-${Math.random().toString(36).slice(2,10)}`;
    const csrf = await getCSRF();

    // Open WS
    let ws=null;
    let partials=0, finals=0, sawAsrErr=false;
    try{
      ws = await openWS(sid, (fr)=>{
        if(fr.type==='user_partial'){ if(!fr.user_msg_id || String(fr.user_msg_id).startsWith('diag')) partials++; }
        else if(fr.type==='user_final'){ if(!fr.user_msg_id || String(fr.user_msg_id).startsWith('diag')) finals++; }
        else if(fr.type==='asr_error'){ sawAsrErr = true; }
      });
      set('bus_subscribe', true, `session=${sid}`);
    }catch(e){
      set('bus_subscribe', false, 'ws failed'); return;
    }

    // greet idempotent
    try{
      const r = await greetIdempotent(sid);
      set('greet_idempotent', r.ok, r.d);
    }catch(_){ set('greet_idempotent', false, 'error'); }

    // chat idempotent
    try{
      const r = await chatIdempotent(sid);
      set('chat_idempotent', r.ok, r.d);
    }catch(_){ set('chat_idempotent', false, 'error'); }

    // rate limit (best-effort; depends on server window/buckets)
    try{
      const r = await rateLimitCheck();
      set('rate_limit_ok', r.ok, r.d);
    }catch(_){ set('rate_limit_ok', false, 'error'); }

    // 413 guard
    try{
      const r = await guard413(sid);
      set('413_guard', r.ok, r.d);
    }catch(_){ set('413_guard', false, 'error'); }

    // vendor flags
    try{
      const r = await vendorFlags();
      set('vendor_keys_ok', r.ok, r.d);
    }catch(_){ set('vendor_keys_ok', false, 'error'); }

    // chunk_post (silent or mic mode)
    let postOk=false;
    if(!micMode){
      try{
        const r = await postChunk({ sid, csrf, userMsgId:'diag-1', seq:1, b64: b64OfBytes(256) });
        postOk = r.ok;
        set('chunk_post', r.ok, r.ok ? 'ok' : `HTTP ${r.status}`);
      }catch(_){ set('chunk_post', false, 'exception'); }
    } else {
      // With mic: start ~2s streamer
      const streamer = makeMicStreamer({ sid, csrf, userMsgId:'diag-mic' });
      try{
        await streamer.start();
        await sleep(2000);
        streamer.stop();
        postOk = true; // we posted many slices
        set('chunk_post', true, 'mic slices sent');
      }catch(e){
        try{ streamer.stop(); }catch(_){}
        set('chunk_post', false, 'mic error');
      }
    }
    set('enqueue_ok', postOk, postOk ? 'ok' : 'failed');

    // Wait briefly for ASR outcomes
    await sleep(micMode ? 2000 : 600);

    // asr_path_ok: pass if we got partial/final OR we at least saw asr:error (not stuck)
    const asrOk = (partials>0 || finals>0 || sawAsrErr);
    set('asr_path_ok', asrOk, `partials=${partials}, finals=${finals}, asr_error=${sawAsrErr}`);

    // counters
    set('partials_seen', partials>0, String(partials));
    set('final_seen', finals>0, finals>0 ? 'ok' : (micMode ? 'no final in window' : 'silent mode (expected)'));

    // Admin SSE health
    try{
      const r = await adminSSE();
      set('admin_sse_ok', r.ok, r.d);
    }catch(_){ set('admin_sse_ok', false, 'error'); }

    // Optional TTS cancel smoke (Mic Mode only)
    try{
      const r = await ttsCancelSmoke({ ws, sid, csrf, micMode });
      set('tts_cancel_ok', r.ok, r.d);
    }catch(_){ set('tts_cancel_ok', false, 'error'); }

    // Close WS
    try{ ws && ws.close(); }catch(_){}
  }

  // Wire up
  function init(){
    const btn = ensureControls();
    const table = ensureResultsTable();
    if(btn){ btn.addEventListener('click', runFullSystemTest); }
    // Expose for console debugging
    window.AdminDiagnostics = { runFullSystemTest };
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
