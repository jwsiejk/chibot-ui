const API = (() => {
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body || {}) });
    return res.json();
  }
  async function get(url) {
    const res = await fetch(url);
    return res.json();
  }
  return {
    login: (email) => post("/api/login", { email }),
    logout: () => post("/api/logout"),
    me: () => get("/api/me"),
    getProfile: () => get("/api/profile"),
    saveProfile: (data) => post("/api/profile", data),
    greet: () => get("/api/greet"),
    chat: (prompt) => post("/api/chat", { prompt }),
    ttsWithVisemes: (text) => post("/api/tts_with_visemes", { text }),
  };
})();
