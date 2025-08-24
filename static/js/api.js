const API = (() => {
  async function parseMaybeJson(res) {
    const ct = res.headers.get("content-type") || "";
    const text = await res.text();
    if (ct.includes("application/json")) {
      try { return JSON.parse(text); } catch { /* fallthrough */ }
    }
    return { ok: false, error: "non_json_response", status: res.status, body: text.slice(0, 400) };
  }

  async function post(url, body) {
    const res = await fetch(url, {
      credentials: 'include',
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(body || {})
    });
    return parseMaybeJson(res);
  }

  async function get(url) {
    const res = await fetch(url, { credentials: 'include' });
    return parseMaybeJson(res);
  }

  const endpoints = {
    chat: "/api/chat",
    orchestrator: "/api/orchestrator",
    conversation: "/api/conversation",
    greet: "/api/greet"
  };

  async function orchestrate(payload) {
    let r = await post(endpoints.orchestrator, payload);
    if ((r && r.error === "non_json_response") || (r && r.status === 404)) {
      r = await post(endpoints.conversation, payload);
    }
    return r;
  }

  return {
    login: (email) => post("/api/login", { email }),
    logout: () => post("/api/logout"),
    me: () => get("/api/me"),
    getProfile: () => get("/api/profile"),
    saveProfile: (data) => post("/api/profile", data),
    greet: () => get(endpoints.greet),
    chat: (prompt) => post(endpoints.chat, { text: prompt }),
    orchestrate: (payload) => orchestrate(payload),
    emailSend: (payload) => post("/api/email/send", payload),
    accountsSearch: (q) => get(`/api/accounts/search?q=${encodeURIComponent(q||"")}`),
    phrase: (role, data) => post("/api/phrase", { role, data }),
    followup: (user_text, assistant_text) => post("/api/followup", { user_text, assistant_text }),
    nudge: (state) => post("/api/nudge", { state })
  };
})(); 
