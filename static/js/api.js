const API = (() => {
  async function post(url, body) {
    const res = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body || {})});
    return res.json();
  }
  async function get(url) { const res = await fetch(url); return res.json(); }
  return {
    login: (email) => post("/api/login", { email }),
    logout: () => post("/api/logout"),
    me: () => get("/api/me"),
    getProfile: () => get("/api/profile"),
    saveProfile: (data) => post("/api/profile", data),
    greet: () => get("/api/greet"),
    chat: (prompt) => post("/api/chat", { prompt }),
    emailSend: (payload) => post("/api/email/send", payload),
    accountsSearch: (q) => fetch(`/api/accounts/search?q=${encodeURIComponent(q||"")}`).then(r=>r.json())
  };
})(); 
