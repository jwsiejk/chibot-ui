// main.js — auth/profile gating + Chip + toolbar — 2025-08-09k → patched to use chip.js + Static/Dynamic buttons
document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ main.js loaded (consolidated)");

  // --- Global error logger (helps catch silent JS errors) ---
  window.addEventListener("error", (e) => {
    console.error("[GlobalError]", e.message, e.filename, e.lineno, e.colno, e.error);
  });

  // ---------- Element lookups (guarded) ----------
  const $ = (id) => document.getElementById(id);

  const loginModal     = $("loginModal");
  const profileModal   = $("profileModal");
  const startButton    = $("startButton") || $("startBtn") || document.querySelector('[data-role="start"]');
  const loginForm      = $("loginForm");
  const profileForm    = $("profileForm");
  const saveProfileBtn = $("saveProfileBtn");
  const loginHint      = $("loginHint");
  const profileHint    = $("profileHint");
  const statusBar      = $("status");

  const chipBox        = $("chipBox");
  const chipImage      = $("chipImage");

  // Toolbar
  const toolbar        = $("askChipToolbar");
  const btnMic         = $("btnMic");
  const btnAsk         = $("btnAsk");
  const btnHistory     = $("btnHistory");
  const btnProfile     = $("btnProfile");
  const btnLogout      = $("btnLogout");

  // NEW: Static/Dynamic mode buttons (safe if missing)
  const btnModeStatic  = $("btnModeStatic");
  const btnModeDynamic = $("btnModeDynamic");

  // Optional floating controls
  const recordBtn      = $("recordBtn");
  const recordPrompt   = $("recordPrompt");
  const waveform       = $("waveform");

  console.log("[main] elements:", {
    startButton: !!startButton, loginModal: !!loginModal, profileModal: !!profileModal,
    loginForm: !!loginForm, profileForm: !!profileForm, saveProfileBtn: !!saveProfileBtn,
    toolbar: !!toolbar, chipBox: !!chipBox, chipImage: !!chipImage,
    btnModeStatic: !!btnModeStatic, btnModeDynamic: !!btnModeDynamic
  });

  const MESSAGES = {
    login:   "Please sign in with your Trace3 or Pure Storage email address.",
    profile: "Please fill out your profile to continue.",
    saved:   "Profile saved. Ready."
  };

  if (loginHint)   loginHint.textContent   = MESSAGES.login;
  if (profileHint) profileHint.textContent = MESSAGES.profile;
  if (startButton && "disabled" in startButton) startButton.disabled = true;
  if (btnModeStatic)  btnModeStatic.disabled  = true;   // disabled until authed+profile
  if (btnModeDynamic) btnModeDynamic.disabled = true;

  // ---------- Helpers (patched show/hide) ----------
  const show    = (el, asDisplay) => { if (!el) return; asDisplay ? (el.style.display = asDisplay) : el.style.removeProperty("display"); };
  const hide    = (el) => { if (el) el.style.display = "none"; };
  const enable  = (el) => { if (el) el.disabled = false; };
  const disable = (el) => { if (el) el.disabled = true; };
  const setStatus = (t) => { if (statusBar) statusBar.textContent = t || ""; };
  const playAudio = (src) => { try { if (src) new Audio(src).play(); } catch {} };

  function reflectMode(mode) {
    if (btnModeStatic)  btnModeStatic.classList.toggle("active", mode === "static");
    if (btnModeDynamic) btnModeDynamic.classList.toggle("active", mode === "dynamic");
    document.documentElement.setAttribute("data-chip-mode", mode);
  }

  function applyAuthedLayout() {
    const appEl = document.getElementById("app");
    if (appEl) show(appEl, "block");
    if (chipBox) show(chipBox, "grid");
    if (toolbar) show(toolbar, "flex");
  }

  async function j(path, opts = {}) {
    const r = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts
    });
    const ct = r.headers.get("content-type") || "";
    let data = null;
    try { data = ct.includes("application/json") ? await r.json() : null; } catch {}
    return { ok: r.ok, status: r.status, data, raw: r };
  }

  async function getStatus() {
    try {
      const { ok, status, data } = await j("/api/me");
      if (ok && data && data.email) {
        return { authenticated: true, first_time: data.profileComplete === false, me: data };
      }
      if (status === 401) return { authenticated: false };
    } catch {}
    try {
      const { ok, data } = await j("/auth/status");
      if (ok && data) return data;
    } catch {}
    return { authenticated: false };
  }

  async function saveProfileJSON(payload) {
    const body = JSON.stringify(payload);
    let r = await j("/profile/save", { method: "POST", body });
    if (r.ok) return true;
    r = await j("/profile", { method: "POST", body });
    if (r.ok) return true;
    r = await j("/api/profile", { method: "POST", body });
    return r.ok;
  }

  // ---------- Profile completeness helpers ----------
  // Require name, title, and email (email comes from auth / login)
  const REQUIRED_FIELDS = ["name", "title", "email"];
  function isProfileIncomplete(p) {
    if (!p || typeof p !== "object") return true;
    for (let i = 0; i < REQUIRED_FIELDS.length; i++) {
      const k = REQUIRED_FIELDS[i];
      const v = (p[k] || "").toString().trim();
      if (!v) return true;
    }
    return false;
  }

  async function fetchProfile() {
    const { ok, data } = await j("/api/me");
    if (ok && data) {
      const prof = {
        name:  (data.name || data.profile?.name  || "").toString(),
        title: (data.title || data.profile?.title || "").toString(),
        email: (data.email || data.profile?.email || "").toString()
      };
      return prof;
    }
    let name = "", title = "", email = "";
    try {
      name   = localStorage.getItem("profileName")  || localStorage.getItem("chip_name")  || "";
      title  = localStorage.getItem("profileTitle") || localStorage.getItem("chip_title") || "";
      email  = localStorage.getItem("profileEmail") || "";
    } catch {}
    return { name, title, email };
  }

  async function enforceProfileCompleteness(opts) {
    const o = opts || {};
    const prof = await fetchProfile();
    const incomplete = isProfileIncomplete(prof);
    if (incomplete) {
      if (profileHint) profileHint.textContent = MESSAGES.profile;
      // Pre-fill email on the profile form if we have it
      if (profileForm) {
        const emailInput = profileForm.querySelector('input[name="email"]');
        if (emailInput && prof.email) emailInput.value = prof.email;
      }
      show(profileModal, "flex");
      hide(chipBox);
      hide(toolbar);
      setStatus("Profile needed to continue.");
      disable(startButton);
      if (btnModeStatic)  disable(btnModeStatic);
      if (btnModeDynamic) disable(btnModeDynamic);
      console.log("[profile] incomplete -> prompting user");
      return { ok: false, profile: prof };
    }
    if (o.applyLayout !== false) {
      applyAuthedLayout();
      enable(startButton);
      if (btnModeStatic)  enable(btnModeStatic);
      if (btnModeDynamic) enable(btnModeDynamic);
      setStatus("Ready.");
    }
    return { ok: true, profile: prof };
  }

  // ---------- Gate UI based on auth/profile ----------
  async function gate() {
    disable(startButton);
    if (btnModeStatic)  disable(btnModeStatic);
    if (btnModeDynamic) disable(btnModeDynamic);

    const st = await getStatus();

    if (!st.authenticated) {
      show(loginModal, "flex");
      hide(profileModal);
      hide(toolbar);
      hide(chipBox);
      setStatus("Please sign in.");
      console.log("[gate] unauthenticated");
      return;
    }
    hide(loginModal);

    const check = await enforceProfileCompleteness({ applyLayout: false });
    if (!check.ok) return;

    hide(profileModal);
    enable(startButton);
    if (btnModeStatic)  enable(btnModeStatic);
    if (btnModeDynamic) enable(btnModeDynamic);
    applyAuthedLayout();
    setStatus("Ready.");
    console.log("[gate] authed + profile complete");
  }
  window.chipGate = gate; // handy manual re-run

  // ---------- Login ----------
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = loginForm.querySelector('input[name="password"]');
      if (pw) pw.disabled = true;

      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;

      const { ok, data, status } = await j("/api/login", {
        method: "POST",
        body: JSON.stringify({ email })
      });

      if (!ok) {
        alert((data && data.error) || `Login failed (${status}). Use your Trace3 or Pure Storage email.`);
        return;
      }

      // Auto-populate profile email field from login
      if (profileForm) {
        const emailInput = profileForm.querySelector('input[name="email"]');
        if (emailInput) emailInput.value = email;
      }
      try { localStorage.setItem("profileEmail", email); } catch {}

      hide(loginModal);

      const check = await enforceProfileCompleteness();
      if (!check.ok) return;

      enable(startButton);
      if (btnModeStatic)  enable(btnModeStatic);
      if (btnModeDynamic) enable(btnModeDynamic);
      applyAuthedLayout();
      gate();
    });
  }

  // ---------- Profile Save ----------
  if (saveProfileBtn && !saveProfileBtn.dataset.wired) {
    saveProfileBtn.dataset.wired = "1";
    saveProfileBtn.addEventListener("click", async () => {
      if (!profileForm) return;
      const fd = new FormData(profileForm);
      const name   = (fd.get("name")   || "").toString().trim();
      const title  = (fd.get("title")  || "").toString().trim();
      const email  = (fd.get("email")  || "").toString().trim();

      if (!name || !title || !email) { alert("Please complete all fields."); return; }

      try {
        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);
        localStorage.setItem("chip_name", name);
        localStorage.setItem("chip_title", title);
        localStorage.setItem("profileEmail", email);
      } catch {}

      const ok = await saveProfileJSON({ name, title, email });
      if (ok) {
        hide(profileModal);
        enable(startButton);
        if (btnModeStatic)  enable(btnModeStatic);
        if (btnModeDynamic) enable(btnModeDynamic);
        setStatus(MESSAGES.saved);
        applyAuthedLayout();
        gate();
      } else {
        alert("Could not save profile. Please try again.");
      }
    });
  }

  // ---------- Start → greet (kept for safety; you can remove later if desired) ----------
  if (startButton && !startButton.dataset.greetWired) {
    startButton.dataset.greetWired = "1";
    startButton.addEventListener("click", async () => {
      try {
        const check = await enforceProfileCompleteness({ applyLayout: true });
        if (!check.ok) return;

        console.log("[UI] Start clicked");
        const mode = (window.chip && typeof window.chip.getMode === "function") ? window.chip.getMode() : "static";
        setStatus(mode === "dynamic" ? "Warming up Chip…" : "Playing greeting…");
        disable(startButton);

        if (window.chip && typeof window.chip.playGreeting === "function") {
          await window.chip.playGreeting();
          setStatus(mode === "dynamic" ? "Greeting complete." : "Greeting finished.");
        } else {
          setStatus("Chip module not available.");
        }
      } finally {
        enable(startButton);
      }
    });
  }

  // ---------- NEW: Static/Dynamic mode buttons ----------
  if (btnModeStatic && !btnModeStatic.dataset.wired) {
    btnModeStatic.dataset.wired = "1";
    btnModeStatic.addEventListener("click", async () => {
      if (window.chip?.setMode) window.chip.setMode("static");
      reflectMode("static");
      const check = await enforceProfileCompleteness({ applyLayout: true });
      if (!check.ok) return;
      setStatus("Playing greeting (static)...");
      await window.chip?.playGreeting();
      setStatus("Greeting finished.");
    });
  }

  if (btnModeDynamic && !btnModeDynamic.dataset.wired) {
    btnModeDynamic.dataset.wired = "1";
    btnModeDynamic.addEventListener("click", async () => {
      if (window.chip?.setMode) window.chip.setMode("dynamic");
      reflectMode("dynamic");
      const check = await enforceProfileCompleteness({ applyLayout: true });
      if (!check.ok) return;
      setStatus("Warming up Chip…");
      await window.chip?.playGreeting();
      setStatus("Greeting complete.");
    });
  }

  // ---------- Toolbar wiring ----------
  if (btnAsk && !btnAsk.dataset.wired) {
    btnAsk.dataset.wired = "1";
    btnAsk.addEventListener("click", async () => {
      console.log("[UI] Ask clicked");
      const check = await enforceProfileCompleteness({ applyLayout: true });
      if (!check.ok) return;

      const mode = (window.chip && typeof window.chip.getMode === "function") ? window.chip.getMode() : "static";

      let q = "";
      if (mode === "dynamic") {
        q = prompt("Ask Chip:");
        if (!q) return;
        setStatus("Thinking…");
      } else {
        setStatus("Playing answer…");
      }

      if (window.chip && typeof window.chip.playAnswer === "function") {
        await window.chip.playAnswer(q);
        setStatus(mode === "dynamic" ? "Answer ready." : "Answer finished.");
      } else {
        setStatus("Chip module not available.");
      }
    });
  }

  // Mic placeholder (hook streaming later)
  if (btnMic && !btnMic.dataset.wired) {
    btnMic.dataset.wired = "1";
    btnMic.addEventListener("click", () => {
      console.log("[UI] Mic clicked");
      alert("Voice input coming soon. Use Ask for now.");
    });
  }

  // History
  if (btnHistory && !btnHistory.dataset.wired) {
    btnHistory.dataset.wired = "1";
    btnHistory.addEventListener("click", async () => {
      console.log("[UI] History clicked");
      setStatus("Looking up history…");
      const { ok, data } = await j("/history", {
        method: "POST",
        body: JSON.stringify({ query: "What did we talk about last time?" })
      });
      console.log("[UI] /history ->", ok, data);
      setStatus(ok ? (data?.response || "No history yet.") : "No history yet.");
    });
  }

  // Profile (open modal)
  if (btnProfile && !btnProfile.dataset.wired) {
    btnProfile.dataset.wired = "1";
    btnProfile.addEventListener("click", () => {
      console.log("[UI] Profile clicked");
      if (profileModal) {
        // Ensure email is prefilled from localStorage if available
        try {
          const email = localStorage.getItem("profileEmail") || "";
          const emailInput = profileForm?.querySelector('input[name="email"]');
          if (email && emailInput) emailInput.value = email;
        } catch {}
        show(profileModal, "flex");
      }
    });
  }

  // Logout
  if (btnLogout && !btnLogout.dataset.wired) {
    btnLogout.dataset.wired = "1";
    btnLogout.addEventListener("click", async () => {
      console.log("[UI] Logout clicked");
      await j("/api/logout", { method: "POST" });
      location.reload();
    });
  }

  // ---------- Initial run ----------
  reflectMode((window.chip && window.chip.getMode && window.chip.getMode()) || "static");
  gate();
});
