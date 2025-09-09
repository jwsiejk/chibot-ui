
(function(){
  let modal = null, es = null, paused=false;
  function build(){
    if(modal) return modal;
    modal = document.createElement('div');
    modal.innerHTML = `
      <div class="ac-overlay" style="position:fixed;inset:0;display:none;place-items:center;background:rgba(0,0,0,.55);z-index:2000">
        <div class="ac-modal" style="background:#111419;color:#eaeef5;border:1px solid rgba(255,255,255,.12);border-radius:12px;min-width:60vw;max-width:94vw;max-height:86vh;display:flex;flex-direction:column">
          <header style="display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.12)">
            <strong>Real‑Time Call Log</strong>
            <span style="opacity:.7;font-size:.9rem">SSE /api/v1/admin/logs</span>
            <span style="flex:1"></span>
            <button id="ac-log-pause" class="btn">Pause</button>
            <button id="ac-log-clear" class="btn secondary">Clear</button>
            <button id="ac-log-close" class="btn secondary">Close</button>
          </header>
          <pre id="ac-log-out" style="margin:0;padding:10px;white-space:pre-wrap;overflow:auto;flex:1;background:#0b0e13"></pre>
        </div>
      </div>`;
    document.body.appendChild(modal);
    return modal;
  }
  function open(){
    build();
    const overlay = modal.firstElementChild;
    overlay.style.display = 'grid';
    const out = overlay.querySelector('#ac-log-out');
    out.textContent += '';

    if(es) { try{ es.close(); }catch{} es=null; }
    try{
      es = new EventSource('/api/v1/admin/logs');
      es.addEventListener('heartbeat', e => { if(!paused) out.textContent += e.data + "\n"; });
      es.onmessage = e => { if(!paused) out.textContent += e.data + "\n"; };
      es.onerror = () => { if(!paused) out.textContent += 'SSE error (maybe 403 — admin only).\n'; };
    }catch(e){
      out.textContent += 'SSE error: ' + (e && e.message || String(e)) + "\n";
    }

    overlay.querySelector('#ac-log-close').onclick = close;
    overlay.querySelector('#ac-log-clear').onclick = ()=>{ out.textContent=''; };
    overlay.querySelector('#ac-log-pause').onclick = (ev)=>{ paused = !paused; ev.target.textContent = paused ? 'Resume' : 'Pause'; };
  }
  function close(){
    const overlay = modal && modal.firstElementChild;
    if(overlay) overlay.style.display = 'none';
    try{ es && es.close(); }catch{} es=null;
  }

  window.addEventListener('ac:open-admin-log', open);
})();
