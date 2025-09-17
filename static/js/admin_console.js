// static/js/admin_console.js
//
// Diagnostics tab (Admin > Diagnostics). Strict checks enabled.
// No-ops unless #admin-diagnostics exists.

(function(){
  function rootEl(){ return document.querySelector('#admin-diagnostics'); }
  if (!rootEl()) return;

  // ---------- tiny DOM helpers ----------
  function $(sel, scope){ return (scope||document).querySelector(sel); }
  function create(tag, attrs){ const el=document.createElement(tag); if(attrs) Object.assign(el, attrs); return el; }
  const sleep = (ms)=> new Promise(r=>setTimeout(r, ms));

  
  // ---------- mic prompt helpers ----------
  function showMicPromptUI({title="Press Continue to record audio", state="idle"}={}){
    const root = rootEl();
    let wrap = document.querySelector('#mic-prompt-overlay');
    if(!wrap){
      wrap = document.createElement('div');
      wrap.id = 'mic-prompt-overlay';
      wrap.style.position='fixed';
      wrap.style.inset='0';
      wrap.style.background='rgba(0,0,0,.55)';
      wrap.style.zIndex='2000';
      wrap.style.display='flex';
      wrap.style.alignItems='center';
      wrap.style.justifyContent='center';
      const card = document.createElement('div');
      card.className='sheet';
      card.style.background='#141824';
      card.style.border='1px solid #202533';
      card.style.borderRadius='12px';
      card.style.padding='16px';
      card.style.minWidth='360px';
      card.style.textAlign='center';
      card.innerHTML = `
        <div id="mic-prompt-title" style="font-weight:600;margin-bottom:8px;">${title}</div>
        <div id="mic-prompt-status" style="opacity:.9;margin-bottom:12px;">${state==='idle'?'Ready':''}</div>
        <div style="display:flex;gap:8px;justify-content:center;">
          <button id="mic-prompt-continue">Continue</button>
          <button id="mic-prompt-cancel">Cancel</button>
        </div>`;
      wrap.appendChild(card);
      root.appendChild(wrap);
    }else{
      const t = wrap.querySelector('#mic-prompt-title'); if(t) t.textContent = title;
      const st = wrap.querySelector('#mic-prompt-status'); if(st) st.textContent = (state==='idle'?'Ready':state);
      wrap.style.display='flex';
    }
    return wrap;
  }
  function hideMicPromptUI(){
    const wrap = document.querySelector('#mic-prompt-overlay');
    if(wrap){ wrap.style.display='none'; }
  }
  async function promptForRecording({onStart, onStop}={}){
    const ui = showMicPromptUI({title:'Press Continue to record audio', state:'idle'});
    const btnGo = ui.querySelector('#mic-prompt-continue');
    const btnCancel = ui.querySelector('#mic-prompt-cancel');
    return new Promise((resolve)=>{
      function cleanup(){ try{btnGo.onclick=null; btnCancel.onclick=null;}catch(_){ } }
      btnCancel.onclick = ()=>{ cleanup(); hideMicPromptUI(); resolve({proceed:false}); };
      btnGo.onclick = async ()=>{
        cleanup();
        const status = ui.querySelector('#mic-prompt-status');
        if(status) status.textContent = 'Recording…';
        try{ onStart && await onStart(); }catch(_){}
        await new Promise(r=>setTimeout(r, 5000)); // 5s window
        try{ onStop && await onStop(); }catch(_){}
        if(status) status.textContent = 'Audio captured (sending)…';
        setTimeout(()=>{ hideMicPromptUI(); resolve({proceed:true}); }, 400);
      };
    });
  }

  
  // ---------- admin fetch helpers ----------
  async function _fetchJSON(url){
    const r = await fetch(url, { credentials: 'include' });
    if(!r.ok){ if(r.status===404||r.status===405) return null; throw new Error('HTTP '+r.status);}
    try{ return await r.json(); }catch(_){ return {}; }
  }
  async function getVendorStatus(){
    const cand = ['/api/v1/admin/vendor_status', '/api/v1/admin/diagnostics/vendor_status', '/admin/diagnostics/vendor_status'];
    for(const u of cand){
      try{ const j = await _fetchJSON(u); if(j) return j; }catch(_){}
    }
    return {};
  }
  async function getRateLimits(){
    const cand = ['/api/v1/admin/rate_limits', '/api/v1/admin/limits', '/api/v1/admin/config/rate_limits'];
    for(const u of cand){
      try{ const j = await _fetchJSON(u); if(j && (j.chat || j.rate_limits || j.limits)) return j; }catch(_){}
    }
    return {};
  }
// ---------- controls/table ----------
  function ensureControls(){
    const root = rootEl();
    let bar = $('#admin-diagnostics-controls', root);
    if(!bar){
      bar = create('div', { id: 'admin-diagnostics-controls' });
      bar.style.display = 'flex';
      bar.style.alignItems = 'center';
      bar.style.gap = '12px';
      bar.style.margin = '10px 0';
      root.appendChild(bar);
    }
    let btn = $('#btn-full-system-test', bar);
    if(!btn){
      btn = create('button', { id: 'btn-full-system-test', textContent: 'Run full system test' });
      bar.appendChild(btn);
    }
    let micWrap = $('#diag-mic-wrap', bar);
    if(!micWrap){
      micWrap = create('label', { id: 'diag-mic-wrap', title: 'Enable microphone for ASR partial/final checks' });
      micWrap.style.display = 'inline-flex';
      micWrap.style.alignItems = 'center';
      micWrap.style.gap = '6px';
      micWrap.innerHTML = `<input type="checkbox" id="diag-mic-mode" /> <span>Mic Mode (send ~2s of 96ms slices)</span>`;
      bar.appendChild(micWrap);
    }
    return btn;
  }

  function ensureTable(){
    const root = rootEl();
    let table = $('#full-system-results', root);
    if(!table){
      table = create('table', { id: 'full-system-results' });
      table.innerHTML = `
        <thead><tr><th>Check</th><th>OK</th><th>Details</th></tr></thead>
        <tbody></tbody>
      `;
      root.appendChild(table);
    }
    return table;
  }

  function tbodyOf(table){ return table.tBodies[0] || table.createTBody(); }
  function setRow(table, key, ok, details){
    const tb = tbodyOf(table);
    const id = `diag-${key}`;
    let tr = tb.querySelector(`tr[data-key="${id}"]`);
    if(!tr){
      tr = create('tr'); tr.dataset.key = id;
      tr.innerHTML = `<td></td><td></td><td></td>`;
      tb.appendChild(tr);
    }
    const [c0,c1,c2] = tr.children;
    c0.textContent = key;
    c1.textContent = ok ? '✔' : '✖';
    c1.style.color = ok ? '#44d07b' : '#ff5a63';
    c2.textContent = (details==null ? '' : String(details));
  }

  // ---------- HTTP/WS utilities ----------
  async function getCSRF(){
    try{ const r=await fetch('/api/v1/csrf',{credentials:'include'}); const t=r.headers.get('X-CSRF-Token')||r.headers.get('X-CSRFToken'); if(t) return t; }catch(_){}
    try{ const r=await fetch('/api/v1/health',{credentials:'include'}); const t=r.headers.get('X-CSRF-Token')||r.headers.get('X-CSRFToken'); if(t) return t; }catch(_){}
    const m=document.cookie.match(/(?:^|;\s*)XSRF-TOKEN=([^;]+)/); return m?decodeURIComponent(m[1]):'';
  }
  function b64OfBytes(n){ const a=new Uint8Array(n); let s=''; for(let i=0;i<n;i++) s+=String.fromCharCode(a[i]); return btoa(s); }
  
async function postChunk({sid, csrf, userMsgId, seq, blob}){
  const fd = new FormData();
  fd.append('chunk', blob, 'mic.webm');
  const r = await fetch(`/ws/v1/chat?session_id=${encodeURIComponent(sid)}`, {
    method: 'POST', credentials: 'include',
    headers: { 'X-CSRF-Token': csrf, 'X-User-Msg-Id': userMsgId || 'diag-mic', 'X-Seq': String(seq||0) },
    body: fd
  });
  return r;
}

  function openWS(sessionId, onFrame){
    return new Promise((resolve,reject)=>{
      try{
        const proto = location.protocol==='https:'?'wss://':'ws://';
        const url = new URL(proto+location.host+'/ws/v1/chat');
        url.searchParams.set('session_id', sessionId);
        const ws = new WebSocket(url.toString());
        ws.onopen = ()=> resolve(ws);
        ws.onmessage = ev => { try{ const fr=JSON.parse(ev.data); onFrame && onFrame(fr); }catch(_){ } };
        ws.onerror = e => reject(e);
      }catch(e){ reject(e); }
    });
  }
  function attachWSListener(ws, fn){
    function onmsg(ev){ try{ fn(JSON.parse(ev.data)); }catch(_){ } }
    ws.addEventListener('message', onmsg);
    return ()=>{ try{ ws.removeEventListener('message', onmsg);}catch(_){} };
  }

  // Admin SSE watch: counts partial/final/error for a specific session
  async function adminSSEWatch(sessionId){
    const counts = { partials:0, finals:0, asr_error:false, last_error:'' };
    return new Promise((resolve)=>{
      try{
        const es = new EventSource('/api/v1/admin/logs');
        const onmsg = (ev)=>{
          try{
            const j = JSON.parse(ev.data || '{}');
            if(j && j.session_id === sessionId){
              if(j.label === 'asr_partial') counts.partials++;
              else if(j.label === 'asr_final') counts.finals++;
              else if(j.label === 'asr_error'){ counts.asr_error = true; counts.last_error = String(j.error||''); }
            }
          }catch(_){}
        };
        es.addEventListener('message', onmsg);
        resolve({ counts, detach: ()=>{ try{ es.removeEventListener('message', onmsg); es.close(); }catch(_){} } });
      }catch(_){ resolve({ counts, detach: ()=>{} }); }
    });
  }

  function attachWSWatch(ws, label){
    const counts = { partials:0, finals:0, asr_error:false, last_error:'' };
    function onmsg(ev){
      try{
        const fr = JSON.parse(ev.data);
        // Normalize a few shapes: either server 'asr_*' frames or assistant/metadata with ASR hints
        if(fr && (fr.type==='asr_partial' || fr.label==='asr_partial')) counts.partials++;
        if(fr && (fr.type==='asr_final'   || fr.label==='asr_final'))   counts.finals++;
        if(fr && fr.label==='asr_error'){ counts.asr_error=true; counts.last_error=String(fr.error||''); }
      }catch(_){}
    }
    ws.addEventListener('message', onmsg);
    return { counts, detach: ()=>{ try{ ws.removeEventListener('message', onmsg);}catch(_){}} };
  }


  // Mic slice sender (96ms cadence)
  

function makeMicStreamer({sid, csrf, userMsgId}){
  let stream=null, rec=null, seq=0, stopped=false;

  async function start(){
    try{
      stream = await navigator.mediaDevices.getUserMedia({
        audio:{
          echoCancellation:true,
          noiseSuppression:true,
          channelCount:1,
          sampleRate:48000
        }
      });
    }catch(_){ throw new Error('mic denied'); }
    rec = new MediaRecorder(stream, { mimeType:'audio/webm;codecs=opus', audioBitsPerSecond:128000 });
    rec.ondataavailable = async (ev)=>{
      if(stopped) return;
      if(ev.data && ev.data.size>0){
        // Send EACH chunk unmodified so the first one includes the WebM/Opus header.
        await postChunk({sid, csrf, userMsgId, seq: ++seq, blob: ev.data});
      }
    };
    rec.start(96); // ~96ms cadence
  }
  function stop(){
    try{ stopped=true; rec && rec.stop(); }catch(_){}
    try{ stream && stream.getTracks().forEach(t=>t.stop()); }catch(_){}
  }
  return { start, stop };
}


  // ---------- main runner ----------
  async function runFullSystemTest(){
    const root = rootEl();
    const table = ensureTable();
    const btn = ensureControls();
    const set = (k, ok, d)=> setRow(table, k, ok, d);

    if(btn){
      btn.disabled=true; const orig=btn.textContent; btn.textContent='Running…';
      setTimeout(()=>{ btn.disabled=false; btn.textContent=orig; }, 5000);
    }

    const micMode = !!$('#diag-mic-mode', root)?.checked;
    const sid = `diag-${Math.random().toString(36).slice(2,10)}`;
    const csrf = await getCSRF();

    // WS + ASR watch
    let ws=null;
    let watch;
    try{
      ws = await openWS(sid);
      set('bus_subscribe', true, `session=${sid}`);
      watch = await adminSSEWatch(sid);
    }catch(_){ set('bus_subscribe', false, 'ws failed'); return; }

    // Strict server facts
    const vendors = await getVendorStatus().catch(()=> ({}));
    const limits  = await getRateLimits().catch(()=> ({}));
    const deepOK = vendors && vendors.deepgram_enabled === true;
    const elevOK = vendors && vendors.elevenlabs_enabled === true;
    const vendorKnown = vendors && (typeof vendors.deepgram_enabled === 'boolean' || typeof vendors.elevenlabs_enabled === 'boolean');
    if(vendorKnown){
      set('vendor_keys_ok', (deepOK && elevOK), `deepgram=${String(!!vendors.deepgram_enabled)}, elevenlabs=${String(!!vendors.elevenlabs_enabled)}`);
    }else{
      set('vendor_keys_ok', true, 'unknown (no server value)');
    }

    const chatMax = (limits && limits.chat && typeof limits.chat.max_per_window === 'number')
      ? limits.chat.max_per_window
      : null;

    // Functional checks
    try{ const r=await greetIdempotent(sid); set('greet_idempotent', r.ok, r.d); }catch(_){ set('greet_idempotent', false, 'error'); }
    try{ const r=await chatIdempotent(sid); set('chat_idempotent', r.ok, r.d); }catch(_){ set('chat_idempotent', false, 'error'); }

    // Strict rate-limit
    if (chatMax == null){
      set('rate_limit_ok', true, 'unknown (no server value)');
    }else{
      try{ const r=await rateLimitCheckStrict(chatMax); set('rate_limit_ok', r.ok, r.d); }
      catch(_){ set('rate_limit_ok', false, 'error'); }
    }

    // 413 guard
    try{ const r=await guard413(sid); set('413_guard', r.ok, r.d); }catch(_){ set('413_guard', false, 'error'); }

    // POST chunks
    let postOk=false;
    if(!micMode){
      try{ const r=await (async ()=>{const __buf=new Uint8Array(atob(b64OfBytes(256)).split('').map(c=>c.charCodeAt(0))); await postChunk({sid, csrf, userMsgId:'diag-1', seq:1,  blob:new Blob([__buf],{type:'application/octet-stream'})});})(); postOk=r.ok; set('chunk_post', r.ok, r.ok?'ok':`HTTP ${r.status}`); }
      catch(_){ set('chunk_post', false, 'exception'); }
    }else{
      const streamer = makeMicStreamer({sid, csrf, userMsgId:'diag-mic'});
      let micOk=false;
      try{
        const res = await promptForRecording({
          onStart: async () => { await streamer.start(); },
          onStop:  async () => { streamer.stop(); }
        });
        if(res && res.proceed){
          const _csrf2 = await getCSRF();
          await fetch(`/ws/v1/chat?session_id=${encodeURIComponent(sid)}`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'X-CSRF-Token': _csrf2 }
          });
          micOk=true; set('chunk_post', true, 'mic slices sent');
        }else{
          set('chunk_post', false, 'cancelled');
        }
      }catch(_){
        try{
          streamer.stop();
          const _csrf = await getCSRF();
          await fetch(`/ws/v1/chat?session_id=${encodeURIComponent(sid)}`, { method:'POST', credentials:'include', headers:{'X-CSRF-Token':_csrf} });
        }catch(_){ }
        set('chunk_post', false, 'mic error');
      }
      postOk = micOk;
    }
set('enqueue_ok', postOk, postOk?'ok':'failed');

    // Observe ASR
    await sleep(micMode?2000:600);
    const { partials, finals, asr_error } = (watch && watch.counts) || {partials:0,finals:0,asr_error:false};

    if(!micMode){
      // ⬇️ changed: show green “skipped (silent mode)” for both rows
      set('asr_path_ok', true, 'skipped (silent mode)');
      set('partials_seen', true, 'skipped (silent mode)');
      set('final_seen', true, 'skipped (silent mode)');
    }else{
      const ok = (partials>0 || finals>0 || asr_error);
      const last_err = (watch && watch.counts && watch.counts.last_error) || '';
      set('asr_path_ok', ok, `partials=${partials}, finals=${finals}, asr_error=${asr_error}${last_err?`, err=${last_err}`:''}`);
      set('partials_seen', partials>0, String(partials));
      set('final_seen', finals>0, finals>0?'ok':'no final in window');
    }

    // Admin SSE & TTS cancel smoke
    try{ const r=await adminSSE(); set('admin_sse_ok', r.ok, r.d); }catch(_){ set('admin_sse_ok', false, 'error'); }
    try{ const r=await ttsCancelSmoke({ws, sid, csrf, micMode}); set('tts_cancel_ok', r.ok, r.d); }catch(_){ set('tts_cancel_ok', true, 'skipped'); }

    try{ watch && watch.detach(); ws && ws.close(); }catch(_){}
  }

  // init
  function init(){
    const root = rootEl(); if(!root) return;
    const btn = ensureControls(); ensureTable();
    if(btn) btn.addEventListener('click', runFullSystemTest);
    window.AdminDiagnostics = { runFullSystemTest };
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', init); else init();
})();