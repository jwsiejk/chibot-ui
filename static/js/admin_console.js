// static/js/admin_console.js
//
// Diagnostics tab script (Admin > Diagnostics only).
// IMPORTANT: This script does nothing unless an element with id="admin-diagnostics" exists.
//
// It performs a "Full System Test" with production-aligned checks without importing app ws/voice code.
// Uses only fetch/WebSocket/MediaRecorder.
//
// Rows it renders in the #full-system-results table:
//  - bus_subscribe, greet_idempotent, chat_idempotent, rate_limit_ok, 413_guard,
//    vendor_keys_ok, chunk_post, enqueue_ok, asr_path_ok, partials_seen, final_seen,
//    admin_sse_ok, tts_cancel_ok (mic mode only)

(function(){
  // ---- guard: run ONLY if diagnostics container exists ----
  function rootEl(){ return document.querySelector('#admin-diagnostics'); }
  if (!rootEl()) return;

  // ---- tiny DOM helpers ----
  function $(sel, scope){ return (scope||document).querySelector(sel); }
  function create(tag, attrs){ const el=document.createElement(tag); if(attrs) Object.assign(el, attrs); return el; }

  // ---- controls bar & table (rendered only inside #admin-diagnostics) ----
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
      micWrap.innerHTML = `<input type="checkbox" id="diag-mic-mode" /> <span>Mic Mode (send 2s of 96ms slices)</span>`;
      bar.appendChild(micWrap);
    }
    return btn;
  }

  function ensureTable(){
    const root = rootEl();
    let table = $('#full-system-results', root);
    if(!table){
      table = create('table', { id: 'full-system-results' });
      table.style.width = '100%';
      table.style.borderCollapse = 'separate';
      table.style.borderSpacing = '0';
      table.innerHTML = `
        <thead>
          <tr>
            <th style="text-align:left;padding:12px;border-bottom:1px solid #202533;">Check</th>
            <th style="text-align:left;padding:12px;border-bottom:1px solid #202533;">OK</th>
            <th style="text-align:left;padding:12px;border-bottom:1px solid #202533;">Details</th>
          </tr>
        </thead>
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
      tr.innerHTML = `
        <td style="padding:10px 12px;border-bottom:1px solid #202533;"></td>
        <td style="padding:10px 12px;border-bottom:1px solid #202533;"></td>
        <td style="padding:10px 12px;border-bottom:1px solid #202533;"></td>
      `;
      tb.appendChild(tr);
    }
    const [c0,c1,c2] = tr.children;
    c0.textContent = key;
    c1.textContent = ok ? '✔' : '✖';
    c1.style.color = ok ? '#44d07b' : '#ff5a63';
    c2.textContent = (details==null ? '' : String(details));
  }

  // ---- utils ----
  async function getCSRF(){
    try{ const r=await fetch('/api/v1/csrf', {credentials:'include'}); const t=r.headers.get('X-CSRF-Token')||r.headers.get('X-CSRFToken'); if(t) return t; }catch(_){}
    try{ const r=await fetch('/api/v1/health', {credentials:'include'}); const t=r.headers.get('X-CSRF-Token')||r.headers.get('X-CSRFToken'); if(t) return t; }catch(_){}
    const m=document.cookie.match(/(?:^|;\s*)XSRF-TOKEN=([^;]+)/); return m?decodeURIComponent(m[1]):'';
  }
  function b64OfBytes(n){ const a=new Uint8Array(n); let s=''; for(let i=0;i<n;i++) s+=String.fromCharCode(a[i]); return btoa(s); }
  function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

  async function postChunk({sid, csrf, userMsgId, seq, b64}){
    const r = await fetch('/api/v1/voice/chunk', {
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token': csrf},
      credentials:'include',
      body: JSON.stringify({ sid, user_msg_id: userMsgId, chunk_seq: seq, audio_b64: b64 })
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

  function makeMicStreamer({sid, csrf, userMsgId}){
    let stream=null, rec=null, seq=0, stopped=false;
    async function start(){
      try{
        stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true, noiseSuppression:true, channelCount:1, sampleRate:48000}});
      }catch(_){ throw new Error('mic denied'); }
      rec = new MediaRecorder(stream, { mimeType:'audio/webm;codecs=opus', audioBitsPerSecond:128000 });
      rec.ondataavailable = async ev=>{
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
      try{ if(stream) stream.getTracks().forEach(t=>t.stop()); }catch(_){}
    }
    return { start, stop };
  }

  // ---- individual checks ----
  async function greetIdempotent(sid){
    const r1=await fetch(`/api/v1/greet?session_id=${encodeURIComponent(sid)}`, {credentials:'include'}); const j1=await r1.json().catch(()=>({}));
    const r2=await fetch(`/api/v1/greet?session_id=${encodeURIComponent(sid)}`, {credentials:'include'}); const j2=await r2.json().catch(()=>({}));
    return { ok: j1.turn_id && j2.turn_id && j1.turn_id===j2.turn_id, d: `${j1.turn_id||'?'} == ${j2.turn_id||'?'}` };
  }

  async function chatIdempotent(sid){
    const csrf = await getCSRF();
    const hdrs = {'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':`diag-chat-${Math.random().toString(36).slice(2,8)}`};
    const r1=await fetch('/api/v1/chat',{method:'POST',headers:hdrs,credentials:'include',body:JSON.stringify({text:'diagnostic ping',session_id:sid})});
    const j1=await r1.json().catch(()=>({}));
    const r2=await fetch('/api/v1/chat',{method:'POST',headers:hdrs,credentials:'include',body:JSON.stringify({text:'diagnostic ping again',session_id:sid})});
    const j2=await r2.json().catch(()=>({}));
    const ok = !!(j1.turn_id && j2.turn_id && j1.turn_id===j2.turn_id && j2.idempotent===true);
    return { ok, d: ok ? `turn_id=${j1.turn_id}` : `unexpected: ${JSON.stringify(j2).slice(0,140)}` };
  }

  async function rateLimitCheck(){
    const csrf = await getCSRF();
    const h1={'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':`rl-1-${Math.random().toString(36).slice(2,6)}`};
    const h2={'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':`rl-2-${Math.random().toString(36).slice(2,6)}`};
    const r1=await fetch('/api/v1/chat',{method:'POST',headers:h1,credentials:'include',body:JSON.stringify({text:'rl-1'})});
    const r2=await fetch('/api/v1/chat',{method:'POST',headers:h2,credentials:'include',body:JSON.stringify({text:'rl-2'})});
    return { ok: r2.status===429, d:`status2=${r2.status}` };
  }

  async function guard413(sid){
    const csrf = await getCSRF();
    const big = b64OfBytes(270_000); // > default 262_144
    const r = await postChunk({sid, csrf, userMsgId:'diag-big', seq:1, b64:big});
    const ok = r.status===413; let d=`status=${r.status}`;
    try{ d += ` ${await r.text()}`; }catch(_){}
    return { ok, d: d.slice(0,180) };
  }

  async function vendorFlags(){
    try{
      let r=await fetch('/api/v1/admin/diagnostics',{credentials:'include'});
      if(!r.ok) r=await fetch('/api/v1/admin/config',{credentials:'include'});
      if(!r.ok) return { ok:false, d:'no admin diag/config' };
      const j=await r.json(); const v=j.vendors||j||{};
      const deep=!!(v.deepgram||v.has_deepgram||v.deepgram_enabled||v.asr_enabled);
      const elev=!!(v.elevenlabs||v.tts_enabled||v.elevenlabs_enabled);
      return { ok: deep && elev, d:`deepgram=${deep}, elevenlabs=${elev}` };
    }catch(_){ return { ok:false, d:'fetch error' }; }
  }

  async function adminSSE(){
    return new Promise(res=>{
      let got=false;
      try{
        const es=new EventSource('/api/v1/admin/logs');
        const t=setTimeout(()=>{ try{es.close();}catch(_){ } res({ok:got,d:got?'ok':'no events in 1.5s'}); },1500);
        es.onmessage=()=>{ got=true; };
        es.onerror=()=>{};
      }catch(_){ res({ ok:false, d:'EventSource error' }); }
    });
  }

  function attachWSListener(ws, fn){
    function onmsg(ev){ try{ fn(JSON.parse(ev.data)); }catch(_){ } }
    ws.addEventListener('message', onmsg);
    return ()=>{ try{ ws.removeEventListener('message', onmsg);}catch(_){} };
  }

  async function ttsCancelSmoke({ws, sid, csrf, micMode}){
    if(!micMode || !ws) return { ok:false, d:'skipped (mic mode only)' };
    const hdrs={'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':`cancel-${Math.random().toString(36).slice(2,6)}`};
    const body=JSON.stringify({ text:'Please say a long response so I can interrupt you.', session_id:sid });

    let turnId=null, audioChunks=0, afterInterrupt=0;
    const unlisten = attachWSListener(ws, fr=>{
      if(fr.type==='assistant_chunk' && fr.turn_id && !turnId) turnId=fr.turn_id;
      if(fr.type==='audio_chunk' && fr.turn_id===turnId){
        audioChunks++; if(afterInterrupt>0) afterInterrupt++;
      }
      if(fr.type==='assistant_end' && fr.turn_id===turnId && afterInterrupt>0){ afterInterrupt+=1000; }
    });

    try{ await fetch('/api/v1/chat',{method:'POST',headers:hdrs,credentials:'include',body}); }catch(_){ unlisten(); return { ok:false, d:'chat failed' }; }
    await sleep(500);
    const streamer = makeMicStreamer({sid, csrf, userMsgId:`barge-${Math.random().toString(36).slice(2,6)}`});
    try{ await streamer.start(); }catch(_){ unlisten(); return { ok:false, d:'mic denied' }; }
    await sleep(800); streamer.stop();
    await sleep(1200); unlisten();

    const ok = (audioChunks>0) && (afterInterrupt<2);
    return { ok, d: audioChunks===0 ? 'no audio observed' : (ok?'ok':'late audio after cancel') };
  }

  // ---- main runner ----
  async function runFullSystemTest(){
    const root = rootEl();
    if(!root) return;

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

    // open WS
    let ws=null, partials=0, finals=0, sawAsrErr=false;
    try{
      ws = await openWS(sid,(fr)=>{
        if(fr.type==='user_partial'){ if(!fr.user_msg_id || String(fr.user_msg_id).startsWith('diag')) partials++; }
        else if(fr.type==='user_final'){ if(!fr.user_msg_id || String(fr.user_msg_id).startsWith('diag')) finals++; }
        else if(fr.type==='asr_error'){ sawAsrErr=true; }
      });
      set('bus_subscribe', true, `session=${sid}`);
    }catch(_){ set('bus_subscribe', false, 'ws failed'); return; }

    // greet idempotent
    try{ const r=await greetIdempotent(sid); set('greet_idempotent', r.ok, r.d); }catch(_){ set('greet_idempotent', false, 'error'); }

    // chat idempotent
    try{ const r=await chatIdempotent(sid); set('chat_idempotent', r.ok, r.d); }catch(_){ set('chat_idempotent', false, 'error'); }

    // rate limit
    try{ const r=await rateLimitCheck(); set('rate_limit_ok', r.ok, r.d); }catch(_){ set('rate_limit_ok', false, 'error'); }

    // 413 guard
    try{ const r=await guard413(sid); set('413_guard', r.ok, r.d); }catch(_){ set('413_guard', false, 'error'); }

    // vendor flags
    try{ const r=await vendorFlags(); set('vendor_keys_ok', r.ok, r.d); }catch(_){ set('vendor_keys_ok', false, 'error'); }

    // chunk_post: silent or mic mode
    let postOk=false;
    if(!micMode){
      try{
        const r=await postChunk({sid, csrf, userMsgId:'diag-1', seq:1, b64:b64OfBytes(256)});
        postOk=r.ok; set('chunk_post', r.ok, r.ok?'ok':`HTTP ${r.status}`);
      }catch(_){ set('chunk_post', false, 'exception'); }
    }else{
      const streamer=makeMicStreamer({sid, csrf, userMsgId:'diag-mic'});
      try{ await streamer.start(); await sleep(2000); streamer.stop(); postOk=true; set('chunk_post', true, 'mic slices sent'); }
      catch(_){ try{streamer.stop();}catch(_){ } set('chunk_post', false, 'mic error'); }
    }
    set('enqueue_ok', postOk, postOk?'ok':'failed');

    // wait for ASR
    await sleep(micMode?2000:600);
    const asrOk = (partials>0 || finals>0 || sawAsrErr);
    set('asr_path_ok', asrOk, `partials=${partials}, finals=${finals}, asr_error=${sawAsrErr}`);
    set('partials_seen', partials>0, String(partials));
    set('final_seen', finals>0, finals>0?'ok':(micMode?'no final in window':'silent mode (expected)'));

    // admin SSE
    try{ const r=await adminSSE(); set('admin_sse_ok', r.ok, r.d); }catch(_){ set('admin_sse_ok', false, 'error'); }

    // tts cancel (mic mode only)
    try{ const r=await ttsCancelSmoke({ws, sid, csrf, micMode}); set('tts_cancel_ok', r.ok, r.d); }catch(_){ set('tts_cancel_ok', false, 'error'); }

    try{ ws && ws.close(); }catch(_){}
  }

  // ---- init strictly inside Diagnostics tab ----
  function init(){
    const root = rootEl();
    if(!root) return;
    const btn = ensureControls();
    ensureTable();
    if(btn){ btn.addEventListener('click', runFullSystemTest); }
    window.AdminDiagnostics = { init, runFullSystemTest };
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
