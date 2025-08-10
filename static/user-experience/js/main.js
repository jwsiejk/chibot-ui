// main.js — Ask Chip UI, auth/profile gating, toolbar, and modal behavior
// Updated: profile modal now has two modes: 'gate' (first time) and 'edit' (from toolbar)

document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ main.js (profile gate/edit modes) loaded");

  const $  = (id) => document.getElementById(id);
  const $$ = (sel, root=document) => root.querySelector(sel);

  // Core elements
  const loginModal     = $("loginModal");
  const profileModal   = $("profileModal");
  const loginForm      = $("loginForm");
  const profileForm    = $("profileForm");
  const saveProfileBtn = $("saveProfileBtn");
  const profileHint    = $("profileHint");           // <p class="hint">...</p> inside the modal
  const statusBar      = $("status");

  // Modal title (we'll update text per mode)
  const profileTitleEl = profileModal ? profileModal.querySelector("h2") : null;

  // App areas
  const appEl   = $("app");
  const chipBox = $("chipBox");
  const toolbar = $("askChipToolbar");

  // Toolbar controls
  const btnMic     = $("btnMic");
  const btnAsk     = $("btnAsk");
  const btnHistory = $("btnHistory");
  const btnProfile = $("btnProfile");
  const btnLogout  = $("btnLogout");

  // Mode buttons (support both ID sets)
  const btnModeStatic  = $("btnModeStatic")  || $("staticBtn");
  const btnModeDynamic = $("btnModeDynamic") || $("dynamicBtn");

  // Optional legacy start button (kept for safety)
  const startButton = $("startButton") || $("startBtn") || document.querySelector('[data-role="start"]');

  // Helpers
  const show    = (el, asDisplay) => { if (!el) return; asDisplay ? (el.style.display = asDisplay) : el.style.removeProperty("display"); };
  const hide    = (el) => { if (!el) return; el.style.display = "none"; };
  const enable  = (el) => { if (el) el.disabled = false; };
  const disable = (el) => { if (el) el.disabled = true; };
  const setStatus = (t) => { if (statusBar) statusBar.textContent = t || ""; };

  // Fetch JSON helper
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

  // ---------- Profile modal modes ----------
  function setProfileModalMode(mode) {
    if (!profileModal) return;
    profileModal.dataset.mode = mode; // 'gate' | 'edit'

    // Title
    if (profileTitleEl) {
      profileTitleEl.textContent = (mode === "gate") ? "Complete Your Profile" : "Your Profile";
    }

    // Helper hint
    if (profileHint) {
      if (mode === "gate") {
        profileHint.textContent = "Please fill out your profile to continue.";
        profileHint.style.display = "block";
      } else {
        profileHint.textContent = "";
        profileHint.style.display = "none";
      }
    }

    // Save button label
    if (saveProfileBtn) {
      saveProfileBtn.textContent = (mode === "gate") ? "Save & Continue" : "Save changes";
    }
  }

  // Load current profile and prefill the form
  async function loadProfileIntoForm() {
    if (!profileForm) return;
    const qs = (name) => profileForm.querySelector(`input[name="${name}"]`);
    const nameI  = qs("name");
    const titleI = qs("title");
    const emailI = qs("email");

    try {
      const r = await fetch("/api/profile", { credentials: "include" });
      if (r.ok) {
        const j = await r.json();
        const p = (j && j.profile) || {};
        if (nameI)  nameI.value  = p.name  || "";
        if (titleI) titleI.value = p.title || "";
        if (emailI) emailI.value = p.email || "";
        return;
      }
    } catch (e) {
      console.warn("[profile] /api/profile fetch failed", e);
    }

    // Fallback from localStorage if API fails
    try {
      if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
      if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
      if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
    } catch {}
  }

  // App layout once authenticated & complete
  function applyAuthedLayout() {
    show(appEl, "block");
    show(chipBox, "grid");
    show(toolbar, "flex");
    enable(btnModeStatic);
    enable(btnModeDynamic);
    enable(startButton);
  }

  // Check server auth/profile status and gate if needed
  async function enforceProfileCompleteness({ applyLayout = true } = {}) {
    try {
      const { ok, status, data } = await j("/api/me");
      if (!ok) {
        if (status === 401) {
          hide(appEl); hide(profileModal); show(loginModal, "flex");
          return { ok: false, reason: "unauthenticated" };
        }
        // server hiccup—fail open to login
        hide(appEl); hide(profileModal); show(loginModal, "flex");
        return { ok: false, reason: "server" };
      }

      const complete = !!data?.profileComplete;
      if (!complete) {
        // Gate mode: block the app and ask to complete
        setProfileModalMode("gate");
        await loadProfileIntoForm();
        show(profileModal, "flex");
        hide(chipBox);
        hide(toolbar);
        disable(btnModeStatic);
        disable(btnModeDynamic);
        disable(startButton);
        setStatus("Profile needed to continue.");
        return { ok: false, reason: "incomplete" };
      }

      // All good
      if (applyLayout) applyAuthedLayout();
      return { ok: true };
    } catch (e) {
      console.error("[gate] error", e);
      hide(appEl); hide(profileModal); show(loginModal, "flex");
      return { ok: false, reason: "error" };
    }
  }

  // Master gate
  async function gate() {
    disable(btnModeStatic); disable(btnModeDynamic); disable(startButton);
    hide(profileModal);
    const res = await enforceProfileCompleteness({ applyLayout: true });
    if (!res.ok) return;
    setStatus("Ready.");
  }
  window.chipGate = gate;

  // ---------- Login ----------
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;

      const { ok, data, status } = await j("/api/login", {
        method: "POST",
        body: JSON.stringify({ email })
      });
      if (!ok) {
        alert((data && data.error) || `Login failed (${status})`);
        return;
      }

      // Prefill email in the profile form
      try { localStorage.setItem("profileEmail", email); } catch {}
      if (profileForm) {
        const emailInput = profileForm.querySelector('input[name="email"]');
        if (emailInput) emailInput.value = email;
      }

      hide(loginModal);
      await gate();
    });
  }

  // ---------- Save Profile ----------
  if (saveProfileBtn && !saveProfileBtn.dataset.wired) {
    saveProfileBtn.dataset.wired = "1";
    saveProfileBtn.addEventListener("click", async () => {
      if (!profileForm) return;
      const fd = new FormData(profileForm);
      const name  = (fd.get("name")  || "").toString().trim();
      const title = (fd.get("title") || "").toString().trim();
      const email = (fd.get("email") || "").toString().trim();

      if (!name || !title || !email) {
        alert("Please complete all fields.");
        return;
      }

      try {
        // persist locally as convenience
        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);
        localStorage.setItem("profileEmail", email);
      } catch {}

      const r = await j("/api/profile", {
        method: "POST",
        body: JSON.stringify({ name, title, email })
      });

      if (!r.ok || !r.data?.ok) {
        alert(r.data?.error || "Could not save profile. Please try again.");
        return;
      }

      // Close modal; behavior depends on mode
      const mode = profileModal?.dataset.mode || "edit";
      hide(profileModal);

      if (mode === "gate") {
        // Gate completed -> enable UI
        applyAuthedLayout();
        setStatus("Profile saved. Ready.");
      } else {
        // Edit mode -> keep the app as-is
        setStatus("Profile updated.");
      }
    });
  }

  // ---------- Toolbar: Profile opens in EDIT mode ----------
  if (btnProfile && !btnProfile.dataset.wired) {
    btnProfile.dataset.wired = "1";
    btnProfile.addEventListener("click", async () => {
      setProfileModalMode("edit");        // <- important: no nag copy
      await loadProfileIntoForm();        // prefill from server
      show(profileModal, "flex");         // open without gating the app
    });
  }

  // ---------- Toolbar: Ask / Mic / History (unchanged minimal wiring) ----------
  if (btnAsk && !btnAsk.dataset.wired) {
    btnAsk.dataset.wired = "1";
    btnAsk.addEventListener("click", async () => {
      const res = await enforceProfileCompleteness({ applyLayout: true });
      if (!res.ok) return;

      const mode = document.documentElement.getAttribute("data-chip-mode") || "static";
      let q = "";
      if (mode === "dynamic") {
        q = prompt("Ask Chip:");
        if (!q) return;
        setStatus("Thinking…");
      } else {
        setStatus("Playing answer…");
      }
      if (window.chip?.playAnswer) {
        await window.chip.playAnswer(q);
      }
      setStatus(mode === "dynamic" ? "Answer ready." : "Answer finished.");
    });
  }

  if (btnMic && !btnMic.dataset.wired) {
    btnMic.dataset.wired = "1";
    btnMic.addEventListener("click", () => {
      alert("Voice input coming soon. Use Ask for now.");
    });
  }

  if (btnHistory && !btnHistory.dataset.wired) {
    btnHistory.dataset.wired = "1";
    btnHistory.addEventListener("click", async () => {
      setStatus("Looking up history…");
      const { ok, data } = await j("/history", {
        method: "POST",
        body: JSON.stringify({ query: "What did we talk about last time?" })
      });
      setStatus(ok ? (data?.response || "No history yet.") : "No history yet.");
    });
  }

  if (btnLogout && !btnLogout.dataset.wired) {
    btnLogout.dataset.wired = "1";
    btnLogout.addEventListener("click", async () => {
      await j("/api/logout", { method: "POST" });
      location.reload();
    });
  }

  // ---------- Mode buttons ----------
  function reflectMode(mode) {
    if (btnModeStatic)  btnModeStatic.classList.toggle("active", mode === "static");
    if (btnModeDynamic) btnModeDynamic.classList.toggle("active", mode === "dynamic");
    document.documentElement.setAttribute("data-chip-mode", mode);
  }
  reflectMode(document.documentElement.getAttribute("data-chip-mode") || "static");

  if (btnModeStatic && !btnModeStatic.dataset.wired) {
    btnModeStatic.dataset.wired = "1";
    btnModeStatic.addEventListener("click", async () => {
      reflectMode("static");
      const res = await enforceProfileCompleteness({ applyLayout: true });
      if (!res.ok) return;
      setStatus("Playing greeting (static)...");
      await window.chip?.playGreeting?.();
      setStatus("Greeting finished.");
    });
  }

  if (btnModeDynamic && !btnModeDynamic.dataset.wired) {
    btnModeDynamic.dataset.wired = "1";
    btnModeDynamic.addEventListener("click", async () => {
      reflectMode("dynamic");
      const res = await enforceProfileCompleteness({ applyLayout: true });
      if (!res.ok) return;
      setStatus("Warming up Chip…");
      await window.chip?.playGreeting?.();
      setStatus("Greeting complete.");
    });
  }

  // ---------- Initial gate ----------
  gate();
});
