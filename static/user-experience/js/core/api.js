// ---- AskChip host shims (patched for /api/v1 + /ws/v1) ----
const API = window.ASKCHIP_API_BASE || "";
const WS  = window.ASKCHIP_WS_BASE  || "";

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

  // Normalize to pathname for checks
  let base = p;
  try {
    const u = new URL(p, location.origin);
    base = u.pathname;
  } catch {}

  const lower = base.toLowerCase();

  // TTS & alias rewrites first (normalize to /api/v1/voice/*)
  const ttsAliases = [
    "/api/voice/tts-with-visemes",
    "/api/voice/tts_with_visemes",
    "/api/voice/tts",
    "/api/voice/speak",
    "/api/voice/eleven/tts",
    "/api/voice/eleven/speak"
  ];
  if (ttsAliases.some(a => lower === a)) return "/api/v1/voice/tts-with-visemes";

  // Avoid double-prefixing: if already versioned, leave it as-is
  if (lower.startsWith("/api/v1/")) return p;
  if (lower.startsWith("/ws/v1/"))  return p;

  // General API & WS versioning
  if (lower.startsWith("/api/")) return "/api/v1" + p.slice(4);
  if (lower.startsWith("/ws/"))  return "/ws/v1"  + p.slice(3);
  return p;
}

  if (p.startsWith("/api/v1/") || p.startsWith("/ws/v1/")) return p;

  // Split query/hash so we preserve them across rewrites
  const hashIdx = p.indexOf("#");
  const qIdx    = p.indexOf("?");
  const cutAt   = Math.min(qIdx === -1 ? p.length : qIdx, hashIdx === -1 ? p.length : hashIdx);
  const base    = p.slice(0, cutAt);
  const suffix  = p.slice(cutAt); // includes ?query/#hash if present

  // ---- Legacy TTS aliases → v1 TTS ----
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

  // Optional: normalize STT if ever called unversioned
  if (base === "/api/voice/stt") return "/api/v1/voice/stt" + suffix;

  // General API & WS versioning
  if (base.startsWith("/api/")) return "/api/v1" + p.slice(4);
  if (base.startsWith("/ws/"))  return "/ws/v1"  + p.slice(3);
  return p;
}

function absolutize(url) {
  const path = _rewriteV1(url);
  const lower = typeof path === "string" ? path.toLowerCase() : "";
  if (lower.startsWith("http://") || lower.startsWith("https://") ||
      lower.startsWith("ws://")   || lower.startsWith("wss://")) {
    return path;
  }
  if (path.startsWith("/api/")) return `${API}${path}`;
  if (path.startsWith("/ws/"))  return `${WS}${path}`;
  return path;
}

// Patch fetch so any relative "/api/..." gets versioned & absolutized
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
