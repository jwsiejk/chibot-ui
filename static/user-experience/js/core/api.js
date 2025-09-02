// ---- AskChip host shims (patched for /api/v1 + /ws/v1) ----
const API = window.ASKCHIP_API_BASE;
const WS  = window.ASKCHIP_WS_BASE;

function _rewriteV1(path) {
  // Map legacy paths to /api/v1 and /ws/v1
  if (typeof path !== "string") return path;
  if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("ws")) return path;
  if (path.startsWith("/api/v1/") || path.startsWith("/ws/v1/")) return path;
  if (path.startsWith("/api/voice/tts_with_visemes")) return "/api/v1/voice/tts-with-visemes";
  if (path.startsWith("/api/")) return "/api/v1" + path.slice(4);
  if (path.startsWith("/ws/"))  return "/ws/v1" + path.slice(3);
  return path;
}

function absolutize(url) {
  const path = _rewriteV1(url);
  if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("ws")) return path;
  if (path.startsWith("/api/")) return `${API}${path}`;
  if (path.startsWith("/ws/"))  return `${WS}${path}`;
  return path;
}

// Patch fetch so any relative "/api/..." goes to the API host
const _fetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  let url = typeof input === "string" ? input : input.url;
  const abs = absolutize(url);
  if (typeof input === "string") return _fetch(abs, init);
  const req = new Request(abs, input);
  return _fetch(req, init);
};

export function j(url, opts={}) {
  const u = absolutize(url);
  const o = Object.assign({ credentials: "include" }, opts);
  return fetch(u, o).then(r => r.ok ? r.json() : Promise.reject(r));
}

export function wsConnect(url, { onOpen, onClose, onError, onMessage } = {}) {
  const target = absolutize(url);
  const ws = new WebSocket(target);
  let open = false;
  ws.onopen = () => { open = true; onOpen && onOpen(); };
  ws.onclose = () => { open = false; onClose && onClose(); };
  ws.onerror = (e) => { onError && onError(e); };
  ws.onmessage = (e) => {
    let msg = null;
    try { msg = JSON.parse(e.data); } catch { msg = e.data; }
    onMessage && onMessage(msg);
  };
  return {
    socket: ws,
    isOpen: () => open && ws.readyState === WebSocket.OPEN,
    send: (msg) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      ws.send(typeof msg === "string" ? msg : JSON.stringify(msg));
      return true;
    },
    close: () => { try { ws.close(); } catch {} }
  };
}
