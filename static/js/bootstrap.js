
// bootstrap.js — production bootstrap (single owner of Start/End/Send)
import { openWS, waitWSOpen, ensureCSRF, getSID, initMic, onEnd, onSend } from '/static/js/app.js?v=v20250911b';

function wire(){
  const startBtn = document.getElementById('startButton');
  const endBtn   = document.getElementById('endButton');
  const sendBtn  = document.getElementById('composerSend');
  if (startBtn){
    startBtn.addEventListener('click', async () => {
      startBtn.disabled = true;
      try{
        await ensureCSRF();
        openWS();
        await waitWSOpen();
        try { await initMic(); } catch {}
        await fetch(`/api/v1/greet?reset=1&session_id=${encodeURIComponent(getSID())}`, { credentials:'include' });
      } finally {
        startBtn.disabled = false;
      }
    });
  }
  if (endBtn) endBtn.addEventListener('click', onEnd);
  if (sendBtn) sendBtn.addEventListener('click', onSend);
  const form = document.getElementById('composerForm');
  if (form) form.addEventListener('submit', (e)=>{ e.preventDefault(); onSend(); });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
else wire();
