// static/js/api.js — resilient fetch + JSON parsing + orchestrator fallback (2025‑08‑24b)
const API = (() => {
  const VERSION = "2025-08-24b";
  const DEFAULT_TIMEOUT = 20000; // 20s

  function withTimeout(promise, ms = DEFAULT_TIMEOUT) {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("timeout")), ms);
      promise.then(
        (v) => { clearTimeout(t); resolve(v); },
        (e) => { clearTimeout(t); reject(e); }
      );
    });
  }

  async function parseMaybeJson(res) {
    const ct = res.headers.get("content-type") || "";
    const text = await res.text();
    if (ct.includes("application/json")) {
      try { return JSON.parse(text); } catch { /* fallthrough */ }
    }
    // salvage if server mislabels JSON
    if (/^\s*[{[]/.test(text)) {
      try { return JSON.parse(text); } catch { /* fallthrough */ }
    }
    return { ok: false, error: "non_json_response", status: res.status, body: text.slice(0, 400) };
  }

  async function request(url, init) {
    try {
      const res = await withTimeout(fetch(url, { credentials: "include", ...init }), DEFAULT_TIMEOUT);
      const payload = await parseMaybeJson(res);
      // Normalize common auth failure
      if (res.status === 401 && (!payload || payload.ok === false)) {
        return { ok: false, error: "unauthorized", status: 401, body: (payload && payload.body) || "" };
      }
      return payload;
    } catch (err) {
      return { ok: false, error: "network_error", detail: String(err && err.message || err) };
    }
  }

  function post(url, body) {
    return request(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function get(url) {
    return request(url, { method: "GET" });
  }

  const endpoints = {
    chat: "/api/chat",
    greet: "/api/greet",
    orchestrator: "/api/orchestrator",
    conversation: "/api/conversation",
    health: "/api/health",
    login: "/api/login",
    logout: "/api/logout",
    me: "/api/me",
    profile: "/api/profile",
    emailSend: "/api/email/send",
    accountsSearch: "/api/accounts/search",
    phrase: "/api/phrase",
    followup: "/api/followup",
    nudge: "/api/nudge"
  };

  async function orchestrate(payload) {
    // Keep this only for legacy callers; chat path should use /api/chat directly
    let r = await post(endpoints.orchestrator, payload);
    if ((r && r.error === "non_json_response") || (r && r.status === 404)) {
      r = await post(endpoints.conversation, payload);
    }
    return r;
  }

  return {
    // expose util for quick diagnostics if needed
    VERSION,
    get: (url) => get(url),
    post: (url, body) => post(url, body),

    login: (email) => post(endpoints.login, { email }),
    logout: () => post(endpoints.logout),
    me: () => get(endpoints.me),
    getProfile: () => get(endpoints.profile),
    saveProfile: (data) => post(endpoints.profile, data),

    health: () => get(endpoints.health),
    greet: () => get(endpoints.greet),

    // Main path (guardrails live on the server route backing /api/chat)
    chat: (text) => post(endpoints.chat, { text }),

    // Legacy orchestrator
    orchestrate: (payload) => orchestrate(payload),

    // Tools
    emailSend: (payload) => post(endpoints.emailSend, payload),
    accountsSearch: (q) => get(`${endpoints.accountsSearch}?q=${encodeURIComponent(q || "")}`),
    phrase: (role, data) => post(endpoints.phrase, { role, data }),
    followup: (user_text, assistant_text) => post(endpoints.followup, { user_text, assistant_text }),
    nudge: (state) => post(endpoints.nudge, { state })
  };
})();
