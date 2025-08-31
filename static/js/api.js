// static/js/api.js — canonical API only (no fallbacks)
const API = (() => {
  const VERSION = "2025-08-31-clean";
  const DEFAULT_TIMEOUT = 20000; // 20s

  function withTimeout(promise, ms = DEFAULT_TIMEOUT) {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("timeout")), ms);
      promise.then(v => { clearTimeout(t); resolve(v); }, e => { clearTimeout(t); reject(e); });
    });
  }

  async function parseMaybeJson(res) {
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("application/json")) return res.json();
    const text = await res.text();
    return { ok: false, error: "non_json_response", status: res.status, body: text.slice(0, 400) };
  }

  async function request(url, opts) {
    const res = await withTimeout(fetch(url, { credentials: "include", ...opts }));
    const data = await parseMaybeJson(res);
    if (!res.ok) throw Object.assign(new Error("api_error"), { status: res.status, data });
    return data;
  }

  function post(url, body) {
    return request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function get(url) { return request(url, { method: "GET" }); }

  const endpoints = {
    chat: "/api/chat",
    greet: "/api/greet",
    health: "/api/health",
    login: "/api/login",
    logout: "/api/logout",
    me: "/api/me",
    profile: "/api/profile",
    emailSend: "/api/email/send",
    accountsSearch: "/api/accounts/search",
    features: "/api/features"
  };

  return {
    endpoints,
    version: () => VERSION,
    // Canonical chat path
    chat: (text) => post(endpoints.chat, { text }),
    greet: () => post(endpoints.greet, {}),
    // Tools
    emailSend: (payload) => post(endpoints.emailSend, payload),
    accountsSearch: (q) => get(`${endpoints.accountsSearch}?q=${encodeURIComponent(q || "")}`),
    features: () => get(endpoints.features)
  };
})();
