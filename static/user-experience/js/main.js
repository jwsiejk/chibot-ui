// main.js — Zoom-style: Static uses /static/chip/audio/* mp3s, Dynamic uses ElevenLabs via /greet
document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  // --- Dynamically size the chip box above the toolbar ---
  function setToolbarHeightVar(extra = 16) {
    const el = document.getElementById('askChipToolbar');
    if (!el) return;
    const h = Math.ceil(el.getBoundingClientRect().height) || 0;
    const finalPx = Math.max(h + extra, 64); // little breathing room
    document.documentElement.style.setProperty('--toolbar-h', finalPx + 'px');
  }
  window.addEventListener('load', setToolbarHeightVar);
  window.addEventListener('resize', setToolbarHeightVar);
  window.addEventListener('orientationchange', setToolbarHeightVar);

  // ---------- Static audio location + filenames ----------
  const STATIC_AUDIO_BASE = "/static/chip/audio/";
  const GREETING_FILES = ["greeting-static.mp3", "greeting.mp3", "Greeting.mp3"];
  const ANSWER_FILES   = ["answer-static.mp3", "answer.mp3", "Answer.mp3"]; // reserved for later

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
  const btnChat      = $("btnAsk");      // visible for familiarity; functionally disabled for now
  const btnHistory   = $("btnHistory");
  const btnLogout    = $("btnLogout");

  // Top-right nav (Ask Chip ▾ → Profile)
  const navMenuBtn   = $("navMenuBtn");
  const navMenu      = $("navMenu");
  const navProfile   = $("navProfile");

  // Session mode (null until user clicks a mode)
  let sessionMode = null; // 'static' | 'dynamic' | null

  // Helpers
  const show = (el, d) => { if (!el) return; d ? el.style.display = d : el.style.removeProperty("display"); };
  const hide = (el) => { if (el) el.style.display = "none"; };
  async function j(path, opts = {}) {
    const r = await fetch(path, { credentials: "include", headers: { "Content-Type": "application/json", ...(opts.headers||{}) }, ...opts });
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
    setToolbarHeightVar();
    // Position the mouth overlay relative to the current avatar size
    // Position the mouth overlay relative to the current avatar size
    if (window.ChipViseme && typeof window.ChipViseme.layout === "function") {
  // anchor = center of mouth as % of avatar (x, y)
  window.ChipViseme.setAnchor(0.49, 0.46);

  // size = mouth box as % of avatar width (w, h)
  window.ChipViseme.setSize(0.095, 0.075);

  // reflow after applying the calibration
  window.ChipViseme.layout();
    }
    if (btnChat) {
      btnChat.disabled = true;
      btnChat.title = "Chat is coming soon";
    }
  }

  async function enforceProfileCompleteness({ applyLayout = true } = {}) {
    try {
      const { ok, status, data } = await j("/api/me");
      if (!ok) { if (status === 401) { hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"unauthenticated" }; } hide(appEl); show(loginModal, "flex"); return { ok:false, reason:"server" }; }
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

  // ---------- Session logic ----------
  function reflectMode(mode) {
    sessionMode = mode; // 'static' | 'dynamic'
    btnStatic?.classList.toggle("mode-active", mode === "static");
    btnDynamic?.classList.toggle("mode-active", mode === "dynamic");
    document.documentElement.setAttribute("data-chip-mode", mode);
  }

  async function tryPlayWithMouth(url, opts) {
    if (window.ChipViseme && typeof window.ChipViseme.play === "function") {
      await window.ChipViseme.play(url, opts || {});
      return url;
    }
    // Fallback: just play the audio
    await new Audio(url).play();
    return url;
  }

  async function startStaticSession() {
    try {
      for (const name of GREETING_FILES) {
        const url = STATIC_AUDIO_BASE + name;
        try { await tryPlayWithMouth(url); return; } catch (_) {}
      }
      throw new Error("No static audio found.");
    } catch (e) {
      console.warn(e?.message || e);
      alert((e && e.message) || "Couldn’t play the static greeting. Check your /static/chip/audio/ files.");
    }
    // Reserved: trigger ANSWER_FILES later on prompt
  }

  async function startDynamicSession() {
    try {
      const { ok, data, status } = await j("/greet", { method: "POST", body: JSON.stringify({}) });
      if (!ok) { alert((data && data.error) || `Greeting failed (${status})`); return; }
      const audioUrl = data?.audio;
      const text = data?.reply || "Hello!";
      if (audioUrl) {
        try { await tryPlayWithMouth(audioUrl); }
        catch (e) { console.warn("Dynamic audio failed:", e); alert(text); }
      } else {
        alert(text); // TTS disabled globally
      }
    } catch (e) {
      console.error("Dynamic session error:", e);
      alert("Couldn’t start dynamic session. Try again.");
    }
  }

  // ---------- Toolbar: start sessions ----------
  btnStatic?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    reflectMode("static");
    await startStaticSession();
  });

  btnDynamic?.addEventListener("click", async () => {
    const okGate = await gate(); if (!okGate.ok) return;
    reflectMode("dynamic");
    await startDynamicSession();
  });

  // Mic placeholder (kept)
  btnMic?.addEventListener("click", () => alert("Voice input coming soon."));

  // Chat: intentionally disabled for now (future text chat)
  btnChat?.addEventListener("click", () => {
    alert("Chat is coming soon. For now, use Static or Dynamic to start a session.");
  });

  // ---------- “Ask Chip ▾” (Profile) ----------
  function toggleNavMenu(forceOpen) {
    if (!navMenu) return;
    if (typeof forceOpen === "boolean") { navMenu.hidden = !forceOpen; return; }
    navMenu.hidden = !navMenu.hidden;
  }
  navMenuBtn?.addEventListener("click", (e) => { e.stopPropagation(); toggleNavMenu(); });
  document.addEventListener("click", (e) => {
    if (!navMenu?.hidden && !navMenu.contains(e.target) && e.target !== navMenuBtn) toggleNavMenu(false);
  });
  navProfile?.addEventListener("click", async () => {
    toggleNavMenu(false);
    setProfileModalMode("edit");
    await loadProfileIntoForm();
    show(profileModal, "flex");
  });

  // ---------- Boot ----------
  (async () => { await gate(); })();
});
