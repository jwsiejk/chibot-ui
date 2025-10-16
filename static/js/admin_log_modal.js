
(function(){
  let modal = null;
  let paused = false;
  let active = false;
  let timer = null;
  let lastStep = 0;

  function build(){
    if(modal) return modal;
    modal = document.createElement('div');
    modal.innerHTML = `
      <div class="ac-overlay" style="position:fixed;inset:0;display:none;place-items:center;background:rgba(0,0,0,.55);z-index:2000">
        <div class="ac-modal" style="background:#111419;color:#eaeef5;border:1px solid rgba(255,255,255,.12);border-radius:12px;min-width:60vw;max-width:94vw;max-height:86vh;display:flex;flex-direction:column">
          <header style="display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.12)">
            <strong>Real‑Time Call Log</strong>
            <span style="opacity:.7;font-size:.9rem">/api/v1/admin/logs (JSON poll)</span>
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

    if (timer) { clearTimeout(timer); timer = null; }
    active = true;
    lastStep = 0;

    const schedule = (ms) => {
      if (!active) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, Math.max(250, ms));
    };

    const drain = async () => {
      const params = new URLSearchParams();
      if (lastStep) params.set('after', String(lastStep));
      const url = params.toString() ? `/api/v1/admin/logs?${params.toString()}` : '/api/v1/admin/logs';

      try {
        const resp = await fetch(url, { credentials: 'include' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const payload = await resp.json();
        const events = Array.isArray(payload?.events) ? payload.events : [];

        if (!events.length && !lastStep) {
          lastStep = Number(payload?.latest_step || 0) || 0;
        }

        for (const evt of events) {
          const step = Number(evt?.step || 0);
          if (step > lastStep) lastStep = step;
          if (!paused) out.textContent += JSON.stringify(evt) + "\n";
        }
        if (!paused && events.length) {
          out.scrollTop = out.scrollHeight;
        }

        return events.length ? 300 : 1400;
      } catch (err) {
        if (!paused) out.textContent += `poll error: ${err?.message || err}\n`;
        return 2500;
      }
    };

    async function run() {
      if (!active) return;
      const ms = await drain();
      schedule(ms || 1200);
    }

    run();

    overlay.querySelector('#ac-log-close').onclick = close;
    overlay.querySelector('#ac-log-clear').onclick = ()=>{ out.textContent=''; };
    overlay.querySelector('#ac-log-pause').onclick = (ev)=>{ paused = !paused; ev.target.textContent = paused ? 'Resume' : 'Pause'; };
  }
  function close(){
    const overlay = modal && modal.firstElementChild;
    if(overlay) overlay.style.display = 'none';
    active = false;
    if (timer) { clearTimeout(timer); timer = null; }
  }

  window.addEventListener('ac:open-admin-log', open);
  window.__acOpenAdminLog = open;
})();
