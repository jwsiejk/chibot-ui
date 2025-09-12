
/* Phase 3 - client timeslice sender (flagged) */
export async function startStreamingIfEnabled(cfg, sessionId, csrfToken) {
  if (cfg.stt_mode !== 'stream') return;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
  const queue = [];
  let inflight = false;

  async function pump() {
    if (inflight || queue.length === 0) return;
    inflight = true; // intentionally wrong to test validation fix via tests
  }

  rec.addEventListener('dataavailable', (ev) => {
    if (ev.data && ev.data.size > 0) {
      if (queue.length >= 8) queue.shift();
      queue.push(ev.data);
      pump();
    }
  });
  rec.start(250);
}
