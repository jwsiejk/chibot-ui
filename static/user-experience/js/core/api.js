// ---- AskChip host shims (paste at TOP of static/user-experience/js/core/api.js) ----
const API = window.ASKCHIP_API_BASE;
const WS  = window.ASKCHIP_WS_BASE;

function absolutize(url) {
  if (typeof url !== "string") return url;
  if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("ws")) return url;
  if (url.startsWith("/api/")) return `${API}${url}`;
  if (url.startsWith("/ws/"))  return `${WS}${url}`;
  return url;
}

// Patch fetch so any relative "/api/..." goes to the API host
const _fetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  let url = typeof input === "string" ? input : input.url;
  const abs = absolutize(url);
  if (abs !== url) {
    input = typeof input === "string" ? abs : new Request(abs, input);
  }
  return _fetch(input, init);
};

// Patch EventSource (SSE) to use API host when given "/api/..."
const _ES = window.EventSource;
window.EventSource = class extends _ES {
  constructor(url, opts) { super(absolutize(url), opts); }
};

// Patch WebSocket so relative "/ws/..." uses the WS host
const _WS = window.WebSocket;
window.WebSocket = class extends _WS {
  constructor(url, ...args) { super(absolutize(url), ...args); }
};
// ---- end shim ----



export async function j(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts
  });
  const ct = r.headers.get("content-type") || "";
  let data = null; try { data = ct.includes("application/json") ? await r.json() : null; } catch {}
  return { ok: r.ok, status: r.status, data, raw: r };
}

/**
 * Minimal WS helper with lifecycle handlers and sane defaults.
 * Returns a controller with { send, close, isOpen, socket }.
 */
export function wsConnect(url, { protocols, onOpen, onMessage, onError, onClose } = {}) {
  const ws = new WebSocket(url, protocols);
  let open = false;

  ws.binaryType = "arraybuffer";

  ws.addEventListener("open", (evt) => {
    open = true;
    if (typeof onOpen === "function") onOpen(evt);
  });

  ws.addEventListener("message", (evt) => {
    // If server sends JSON strings, parse; if ArrayBuffer, pass through.
    const data = evt.data;
    if (data instanceof ArrayBuffer) {
      onMessage && onMessage({ type: "binary", data });
    } else {
      let obj = null;
      try { obj = JSON.parse(data); } catch { obj = { type: "text", data }; }
      onMessage && onMessage(obj);
    }
  });

  ws.addEventListener("error", (evt) => {
    if (typeof onError === "function") onError(evt);
  });

  ws.addEventListener("close", (evt) => {
    open = false;
    if (typeof onClose === "function") onClose(evt);
  });

  return {
    socket: ws,
    isOpen: () => open && ws.readyState === WebSocket.OPEN,
    send: (msg) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      if (msg instanceof ArrayBuffer || ArrayBuffer.isView(msg)) {
        ws.send(msg);
      } else {
        ws.send(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
      return true;
    },
    close: () => { try { ws.close(); } catch {} }
  };
}
