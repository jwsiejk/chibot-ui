// core/api.js — fetch + websocket helpers (patched for streaming)

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
