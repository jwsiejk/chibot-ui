
// Minimal, production-safe retry helper for AskChip WS
export async function openChatSocketWithRetry({ sid, fetchToken, onmessage, onopen, onclose, onerror, maxRetries = 1 }) {
  const url = location.origin.replace(/^http/,'ws') + `/ws/v1/chat?session_id=${encodeURIComponent(sid)}`;
  let attempts = 0;
  async function connect() {
    const tok = await fetchToken(); // must return { token }
    const ws  = new WebSocket(url, ['bearer', `bearer.${tok.token}`]);
    let gotReady = false;
    ws.onmessage = (e) => {
      try { const d = JSON.parse(e.data); if (d && d.type === 'ready') gotReady = true; } catch {}
      onmessage && onmessage(e);
    };
    ws.onopen = (e) => onopen && onopen(e);
    ws.onerror = (e) => onerror && onerror(e);
    ws.onclose = async (e) => {
      // If we closed before seeing "ready", attempt a single retry with jitter
      if (!gotReady && attempts < (maxRetries||0)) {
        attempts++;
        await new Promise(r => setTimeout(r, 200 + Math.random()*300));
        return connect();
      }
      onclose && onclose(e);
    };
    return ws;
  }
  return connect();
}
