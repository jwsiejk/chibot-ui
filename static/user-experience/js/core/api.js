// ---- AskChip host shims (patched for /api/v1 + /ws/v1) ----
const API = window.ASKCHIP_API_BASE;
const WS  = window.ASKCHIP_WS_BASE;

/**
 * Normalize legacy frontend paths to the v1 surfaces.
 * - All legacy TTS aliases → /api/v1/voice/tts-with-visemes
 * - /api/* → /api/v1/*
 * - /ws/*  → /ws/v1/*
 * Leaves absolute http(s)/ws(s) URLs unchanged.
 */
function _rewriteV1(path) {
  if (typeof path !== "string") return path;
  let p = path.trim();
  if (!p) return p;

  // absolute URLs (http/https/ws/wss) are left as-is
  const lower = p.toLowerCase();
  if (lower.startsWith("http://") || lower.startsWith("https://") ||
      lower.startsWith("ws://")   || lower.startsWith("wss://")) {
    return p;
  }

  // Already versioned
  if (p.startsWith("/api/v1/") || p.startsWith("/ws/v1/")) return p;

  // Split query/hash so we preserve them across rewrites
  const hashIdx = p.indexOf("#");
  const qIdx    = p.indexOf("?");
  const cutAt   = Math.min(
    qIdx === -1 ? p.length : qIdx,
    hashIdx === -1 ? p.length : hashIdx
  );
  const base    = p.slice(0, cutAt);
  const suffix  = p.slice(cutAt); // includes ?query and/or #hash if present

  // ---- Legacy TTS aliases → v1 TTS ----
  // (covers historical front-end calls like /api/speak, /tts_with_visemes, etc.)
  const TTS_ALIASES = new Set([
    "/api/speak",
    "/api/tts_with_visemes",
    "/api/voice/tts_with_visemes",
    "/tts_with_visemes",
    "/tts",
    "/speak",
    "/eleven/tts",
    "/eleven/speak"
  ]);
  if (TTS_ALIASES.has(base)) {
    return "/api/v1/voice/tts-with-visemes" + suffix;
  }

  // Optional: normalize STT legacy path if it ever appears unversioned
  if (base === "/api/voice/stt") {
    return "/api/v1/voice/stt" + suffix;
  }

  // ---- General API & WS versioning ----
  if (base.startsWith("/api/")) return "/api/v1" + p.slice(4);  // keep suffix
  if (base.startsWith("/ws/"))  return "/ws/v1"  + p.slice(3);  // keep suffix

  // No change
  return p;
}

function absolutize(url) {
  const path = _rewriteV1(url);
  // absolute URLs (already have scheme) — pass through
  const lower = typeof path === "string" ? path.toLowerCase() : "";
  if (lower.startsWith("http://") || lower.startsWith("https://") ||
      lower.startsWith("ws://")   || lower.startsWith("wss://")) {
    return path;
  }
  // same-origin joining for REST/WS under Option A
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
