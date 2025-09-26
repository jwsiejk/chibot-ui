// WS-only Diagnostics for Phase 6
import { openWS, waitWSOpen, closeWS, sendCloseStream } from './ws.js';
import { initMic, armVAD, disarmVAD } from './voice.js';
import { getSID } from './util/sid.js';

const $ = (s)=>document.querySelector(s);
const logEl = ()=> $('#admin-log');
const sid = getSID();

function log(line){
  const el = logEl(); if(!el) return;
  el.textContent += line + "\n";
  el.scrollTop = el.scrollHeight;
}

function setKPI(id, val, cls=''){
  const el = $('#'+id); if(!el) return;
  el.textContent = val;
  el.classList.remove('ok','warn','fail');
  if (cls) el.classList.add(cls);
}

async function watchAdminSSE(onEvent){
  try{
    const sse = new EventSource('/api/v1/admin/logs', { withCredentials:true });
    sse.onmessage = (e)=>{
      try{
        const j = JSON.parse(e.data);
        if (j && j.kind){
          log(e.data);
          onEvent?.(j);
        }
      }catch(_){}
    };
    return { close: ()=>{ try{sse.close();}catch(_){}} };
  }catch(e){
    log('SSE open failed: '+e.message);
    return { close: ()=>{} };
  }
}

async function recordFiveSeconds(){
  const btnRec = $('#btn-record');
  const btnStop = $('#btn-stop');
  const status = $('#status');
  setKPI('kpi-partials','0'); setKPI('kpi-finals','0');
  setKPI('kpi-pipe','—');

  let partials = 0, finals = 0, sawOpen = false;
  const watcher = await watchAdminSSE((ev)=>{
    try{
      const k = String(ev.kind||''); const lbl = String(ev.label||'');
      const evSid = String(ev.session_id || ev.sid || '');
      if (evSid && evSid !== sid) return; // filter to our session only
      if (k === 'asr' && lbl === 'asr_open'){ sawOpen = true; setKPI('kpi-pipe','open','ok'); }
      if (k === 'asr' && lbl === 'asr_partial'){ partials++; setKPI('kpi-partials', String(partials)); }
      if (k === 'asr' && lbl === 'asr_final'){ finals++; setKPI('kpi-finals', String(finals)); }
      if (k === 'asr' && lbl === 'asr_error'){ setKPI('kpi-pipe','error','fail'); }
    }catch(_){}
  });

  try{
    status.textContent = 'Opening WebSocket…';
    await openWS({ reconnect: false });
    await waitWSOpen();
    status.textContent = 'Requesting microphone…';
    const stream = await initMic();
    status.textContent = 'Recording…';
    btnRec.disabled = true; btnStop.disabled = false;

    await armVAD(stream);
    // stop after 5 seconds or when Stop clicked
    const stopPromise = new Promise((resolve)=>{
      btnStop.onclick = ()=> resolve('manual');
      setTimeout(()=> resolve('timer'), 8000);
    });
    const reason = await stopPromise;
    btnStop.disabled = true;

    status.textContent = 'Stopping…';
    disarmVAD();
status.textContent = 'Audio captured (sending)…';

    // wait briefly for finals to land
    await new Promise(r=> setTimeout(r, 2000));
    if (sawOpen && (partials+finals)>=1){
      setKPI('kpi-pipe','ok','ok');
    }else{
      setKPI('kpi-pipe', sawOpen ? 'no results' : 'no open', 'warn');
    }
    status.textContent = 'Done.';
  }catch(e){
    status.textContent = 'Error: ' + e.message;
    setKPI('kpi-pipe','error','fail');
  }finally{
    try{ closeWS(); }catch(_){}
    try{ watcher.close(); }catch(_){}
    btnRec.disabled = false; btnStop.disabled = true;
  }
}

function init(){
  const btnRec = $('#btn-record');
  const btnStop = $('#btn-stop');
  if (btnRec) btnRec.addEventListener('click', recordFiveSeconds);
  if (btnStop) btnStop.disabled = true;
  log('Session: '+sid);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
