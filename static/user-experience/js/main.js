// main.js — toolbar Chat, working top-right "Ask Chip ▾" (orange) menu, and proper profile gating
document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  // Core elements
  const loginModal   = $("loginModal");
  const profileModal = $("profileModal");
  const loginForm    = $("loginForm");
  const profileForm  = $("profileForm");
  const saveBtn      = $("saveProfileBtn");
  const profileHint  = $("profileHint");
  const appEl        = $("app");
  const chipBox      = $("chipBox");

  // Bottom toolbar
  const toolbar      = $("askChipToolbar");
  const btnStatic    = $("btnModeStatic");
  const btnDynamic   = $("btnModeDynamic");
  const btnMic       = $("btnMic");
  const btnChat      = $("btnAsk");
  const btnHistory   = $("btnHistory");
  const btnLogout    = $("btnLogout");

  // Top-right nav (orange) — Ask Chip ▾
  const navMenuBtn   = $("navMenuBtn");
  const navMenu      = $("navMenu");
  const navProfile   = $("navProfile");

  // Helpers
  const show = (el, d) => { if (!el) return; d ? el.style.display = d : el.style.removeProperty("display"); };
  const hide = (el) => { if (el) el.style.display = "none"; };

  async function j(path, opts = {}) {
    const r = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(opts.headers||{}) },
      ...opts
    });
    const ct = r.headers.get("content-type") || "";
    let data = null; try { data = ct.includes("application/json") ? await r.json() : null; } catch {}
    return { ok: r.ok, status: r.status, data, raw: r };
  }

  // ---------- Profile modal modes ----------
  function setProfileModalMode(mode) {
    if (!profileModal) return;
    profileModal.dataset.mode = mode; // 'gate' | 'edit'
    const titleEl = profileModal.querySelector("h2");
    if (titleEl) titleEl.textContent = (mode === "gate") ? "Complete Your Profile" : "Your Profile";
    if (profileHint) {
      if (mode === "gate") { profileHint.textContent = "Please fill out your profile to continue."; profileHint.style.display = "block"; }
      else { profileHint.textContent = ""; profileHint.style.display = "none"; }
    }
    if (saveBtn) saveBtn.textContent = (mode === "gate") ? "Save & Continue" : "Save changes";
  }

  async function loadProfileIntoForm() {
    if (!profileForm) return;
    const getI = (n) => profileForm.querySelector(`input[name="${n}"]`);
    const nameI = getI("name"), titleI = getI("title"), emailI = getI("email");
    try {
      const r = await fetch("/api/profile", { credentials: "include" });
      if (r.ok) {
        const js = await r.json();
        const p = (js && js.profile) || {};
        if (nameI)  nameI.value  = p.name  || "";
        if (titleI) titleI.value = p.title || "";
        if (emailI) emailI.value = p.email || "";
        return;
      }
    } catch {}
    // local fallback
    try {
      if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
      if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
      if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
    } catch {}
  }

  function applyAuthedLayout() {
    show(appEl, "block");
    show(chipBox, "grid");
    show(toolbar, "flex");
  }

  async function enforceProfileCompleteness({ applyLayout = true } = {}) {
    try {
      const { ok, status, data } = await j("/api/me");
      if (!ok) {
        if (status === 401) { hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"unauthenticated" }; }
        hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"server" };
      }
      if (!data?.profileComplete) {
        setProfileModalMode("gate");
        await loadProfileIntoForm();
        show(profileModal, "flex");
        hide(toolbar);
        return { ok:false, reason:"incomplete" };
      }
      if (applyLayout) applyAuthedLayout();
      return { ok:true };
    } catch {
      hide(appEl); show(loginModal, "flex");
      return { ok:false, reason:"error" };
    }
  }

  async function gate() {
    hide(profileModal);
    return await enforceProfileCompleteness({ applyLayout: true });
  }

  // ---------- Login ----------
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;

      const { ok, data, status } = await j("/api/login", { method: "POST", body: JSON.stringify({ email }) });
      if (!ok) { alert((data && data.error) || `Login failed (${status})`); return; }

      try { localStorage.setItem("profileEmail", email); } catch {}
      const emailInput = profileForm?.querySelector('input[name="email"]');
      if (emailInput) emailInput.value = email;

      hide(loginModal);
      await gate();
    });
  }

  // ---------- Save Profile ----------
  if (saveBtn && !saveBtn.dataset.wired) {
    saveBtn.dataset.wired = "1";
    saveBtn.addEventListener("click", async () => {
      if (!profileForm) return;
      const fd = new FormData(profileForm);
      const name  = (fd.get("name")  || "").toString().trim();
      const title = (fd.get("title") || "").toString().trim();
      const email = (fd.get("email") || "").toString().trim();
      if (!name || !title || !email) { alert("Please complete all fields."); return; }

      try { localStorage.setItem("profileName", name); localStorage.setItem("profileTitle", title); localStorage.setItem("profileEmail", email); } catch {}
      const r = await j("/api/profile", { method:"POST", body: JSON.stringify({ name, title, email }) });
      if (!r.ok || !r.data?.ok) { alert(r.data?.error || "Could not save profile. Please try again."); return; }

      hide(profileModal);
      if ((profileModal?.dataset.mode || "edit") === "gate") applyAuthedLayout();
      alert("Profile saved.");
    });
  }

  // ---------- Bottom toolbar ----------
  // Static/Dynamic: not selected by default; clicking toggles mode-active class
  function reflectMode(mode) {
    btnStatic?.classList.toggle("mode-active", mode === "static");
    btnDynamic?.classList.toggle("mode-active", mode === "dynamic");
    document.documentElement.setAttribute("data-chip-mode", mode);
  }
  btnStatic?.addEventListener("click", () => reflectMode("static"));
  btnDynamic?.addEventListener("click", () => reflectMode("dynamic"));

  // Mic (placeholder)
  btnMic?.addEventListener("click", () => alert("Voice input coming soon. Use Chat for now."));

  // Chat → /ask
  btnChat?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    const question = prompt("Chat with Chip:");
    if (!question) return;
    try {
      const { ok, data, status } = await j("/ask", { method:"POST", body: JSON.stringify({ question }) });
      if (!ok) { alert((data && data.error) || `Chat failed (${status})`); return; }
      const reply = data?.response || "(no reply)";
      // Play audio if present; also show quick alert with text
      if (data?.audio) {
        try { await new Audio(data.audio).play(); } catch {}
      }
      alert(reply);
    } catch { alert("Something went wrong. Try again."); }
  });

  // More menu
  btnHistory?.addEventListener("click", async () => {
    const { ok, data } = await j("/history", { method:"POST", body: JSON.stringify({ query: "What did we talk about last time?" }) });
    alert(ok ? (data?.response || "No history yet.") : "No history yet.");
  });
  btnLogout?.addEventListener("click", async () => {
    await j("/api/logout", { method:"POST" });
    location.reload();
  });

  // ---------- Top-right nav (Ask Chip ▾) ----------
  function toggleNavMenu(forceOpen) {
    if (!navMenu) return;
    if (typeof forceOpen === "boolean") { navMenu.hidden = !forceOpen; return; }
    navMenu.hidden = !navMenu.hidden; // <-- FIXED: actually toggles
  }
  navMenuBtn?.addEventListener("click", (e) => { e.stopPropagation(); toggleNavMenu(); });
  document.addEventListener("click", (e) => {
    if (!navMenu?.hidden && !navMenu.contains(e.target) && e.target !== navMenuBtn) toggleNavMenu(false);
  });

  // Profile (EDIT MODE) from dropdown
  navProfile?.addEventListener("click", async () => {
    toggleNavMenu(false);
    setProfileModalMode("edit");
    await loadProfileIntoForm();
    show(profileModal, "flex");
  });

  // ---------- Initial gate ----------
  (async () => { await gate(); })();
});
