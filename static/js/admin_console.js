// static/js/admin_console.js
//
// Admin diagnostics helpers, including the "Full System Test" that verifies:
//   1) We can subscribe to WS /ws/v1/chat for a unique session_id.
//   2) We can POST a voice chunk to /api/v1/voice/chunk with CSRF and IDs.
//   3) (If real speech is present) we see user_partial / user_final frames.
//
// This file intentionally avoids importing app ws/voice modules so diagnostics
// can run even if the main client code is broken. It uses only basic browser APIs.

(function(){
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

  // ---------- DOM helpers ----------
  function $(sel) { return document.querySelector(sel); }
  function $any(selectors) { for(const s of selectors){ const el=$(s); if(el) return el; } return null; }
  function ensureResultsTable(){
    let table = $any(TABLE_SELECTORS);
    if (table) return table;

    // Create a simple table if page doesn't provide one
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
    const host = document.querySelector('#admin-diagnostics') || document.body;
    const wrap = document.createElement('div');
    wrap.style.marginTop = '12px';
    wrap.appendChild(table);
    host.appendChild(wrap);
    return table;
  }
  function tbodyOf(table){
    return table.tBodies[0] || table.createTBody();
  }
  function rowId(key) { return `diag-${key}`; }
  function setRow(table, key, ok, details){
    const tb = tbodyOf(table);
    const id = rowId(key);
    let tr = tb.querySelector(`tr[data-key="${id}"]`);
    if (!tr){
      tr = document.createElement('tr');
      tr.dataset.key = id;
      tr.innerHTML = `
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
        <td style="padding:6px;border-bottom:1px solid #2a2f3a;"></td>
      `;
      tb.appendChild(tr);
    }
    const [c0, c1, c2] = tr.children;
    c0.textContent = key;
    c1.textContent = ok ? '✔' : '✖';
    c1.style.color = ok ? '#44d07b' : '#ff5a63';
    c2.textContent = (details == null ? '' : String(details));
  }

  // ---------- CSRF helper ----------
  async function getCSRF(){
    // Try the explicit CSRF endpoint first; fall back to health header/cookie
    try {
      const r = await fetch('/api/v1/csrf', { credentials: 'include' });
      const tok = r.headers.get('X-CSRF-Token') || r.headers.get('X-CSRFToken');
      if (tok) return tok;
    } catch(_){}
    try {
      const r = await fetch('/api/v1/health', { credentials: 'include' });
      const tok = r.headers.get('X-CSRF-Token') || r.headers.get('X-CSRFToken');
      if (tok) return tok;
    } catch(_){}
    // Cookie fallback (if server sets XSRF-TOKEN)
    const m = document.cookie.match(/(?:^|;\s*)XSRF-TOKEN=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  // ---------- WS helper for a single session ----------
  function openDiagWS(sessionId, onFrame){
    return new Promise((resolve, reject) => {
      try {
        const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
        const url = new URL(proto + location.host + '/ws/v1/chat');
        url.searchParams.set('session_id', sessionId);
        const ws = new WebSocket(url.toString());
        ws.onopen = () => resolve(ws);
        ws.onmessage = (ev) => {
          try {
            const fr = JSON.parse(ev.data);
            onFrame && onFrame(fr);
          } catch(_){}
        };
        ws.onerror = (e) => reject(e);
        // ws.onclose handled by caller if needed
      } catch (e){
        reject(e);
      }
    });
  }

  // ---------- Diagnostic chunk (small, neutral payload) ----------
  function diagChunkB64(){
    // 256 bytes of zero — not valid audio, but sufficient to exercise the path.
    // Deepgram won't emit partial/final for this; that's expected.
    const bytes = new Uint8Array(256);
    let bin = '';
    for (let i=0;i<bytes.length;i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  // ---------- Full System Test ----------
  async function runFullSystemTest(){
    const btn = $any(BUTTON_SELECTORS);
    const table = ensureResultsTable();
    function set(key, ok, details){ setRow(table, key, ok, details); }

    if (btn) {
      btn.disabled = true;
      const original = btn.textContent;
      btn.textContent = 'Running…';
      setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 4000);
    }

    const sid = `diag-${Math.random().toString(36).slice(2,10)}`;
    const userMsgId = 'diag-1';
    const chunkSeq = 1;

    // 1) WS subscribe
    let ws;
    let partials = 0;
    let finals = 0;
    let wsErr = null;
    try {
      ws = await openDiagWS(sid, (fr) => {
        if (!fr || typeof fr !== 'object') return;
        if (fr.type === 'user_partial'){
          // When server has user_msg_id on ASR frames, prefer to count our diag id only
          if (!fr.user_msg_id || fr.user_msg_id === userMsgId) partials++;
        } else if (fr.type === 'user_final'){
          if (!fr.user_msg_id || fr.user_msg_id === userMsgId) finals++;
        }
      });
      set('bus_subscribe', true, `session=${sid}`);
    } catch(e){
      wsErr = e;
      set('bus_subscribe', false, 'failed to subscribe ws');
    }

    // 2) POST a chunk to /api/v1/voice/chunk (replaces legacy stt/stream)
    let postOk = false;
    try {
      const csrf = await getCSRF();
      const body = {
        sid,
        user_msg_id: userMsgId,
        chunk_seq: chunkSeq,
        audio_b64: diagChunkB64()
      };
      const r = await fetch('/api/v1/voice/chunk', {
        method: 'POST',
        headers: { 'Content-Type':'application/json', 'X-CSRF-Token': csrf },
        credentials: 'include',
        body: JSON.stringify(body)
      });
      postOk = r.ok;
      set('chunk_post', r.ok, r.ok ? 'ok' : `HTTP ${r.status}`);
      if (!r.ok) {
        // If server enforces 413 for large chunks (or CSRF error), show details
        try { set('enqueue_ok', false, (await r.text()).slice(0,120)); } catch(_){}
      }
    } catch(e){
      set('chunk_post', false, 'exception');
    }

    // 3) Wait briefly for ASR frames (optional) — 2.5s
    const waitMs = 2500;
    const start = performance.now();
    while (performance.now() - start < waitMs) {
      await new Promise(r => setTimeout(r, 150));
    }

    // 4) Summarize
    set('enqueue_ok', !!postOk, postOk ? 'ok' : 'failed');
    set('partials_seen', partials > 0, String(partials));
    set('final_seen', finals > 0, finals > 0 ? 'ok' : 'no user_final within window');

    // Close WS
    try { ws && ws.close(); } catch(_){}
  }

  // ---------- Wire up ----------
  function attachButton(){
    let btn = $any(BUTTON_SELECTORS);
    if (!btn){
      // Create a simple button if one doesn't exist
      btn = document.createElement('button');
      btn.id = 'btn-full-system-test';
      btn.textContent = 'Run full system test';
      btn.style.marginTop = '12px';
      const host = document.querySelector('#admin-diagnostics') || document.body;
      host.appendChild(btn);
    }
    btn.addEventListener('click', runFullSystemTest);
  }

  // Initialize on DOM ready
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', attachButton);
  } else {
    attachButton();
  }

  // Expose for manual triggering from console
  window.AdminDiagnostics = { runFullSystemTest };
})();
