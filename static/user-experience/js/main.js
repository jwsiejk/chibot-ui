// main.js — consolidated auth/profile gating + toolbar wiring — 2025-08-09g
document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ main.js loaded (consolidated)");

  // ---------- Elements (wider selectors + guards) ----------
  const loginModal     = document.getElementById("loginModal");
  const profileModal   = document.getElementById("profileModal");
  const startButton    = document.getElementById("startButton")
                        || document.getElementById("startBtn")
                        || document.querySelector('[data-role="start"]');
  const loginForm      = document.getElementById("loginForm");
  const profileForm    = document.getElementById("profileForm");
  const saveProfileBtn = document.getElementById("saveProfileBtn");
  const loginHint      = document.getElementById("loginHint");
  const profileHint    = document.getElementById("profileHint");
  const statusBar      = document.getElementById("status");
  // Toolbar bits (may or may not exist yet)
  const toolbar        = document.getElementById("askChipToolbar");
  const btnMic         = document.getElementById("btnMic");
  const btnAsk         = document.getElementById("btnAsk");
  const btnHistory     = document.getElementById("btnHistory");
  const btnProfile     = document.getElementById("btnProfile");
  const btnLogout      = document.getElementById("btnLogout");
  // Optional floating controls
  const recordBtn      = document.getElementById("recordBtn");
  const recordPrompt   = document.getElementById("recordPrompt");
  const waveform       = document.getElementById("waveform");

  console.log("[main] elements:", {
    startButton: !!startButton,
    loginModal: !!loginModal,
    profileModal: !!profileModal,
    loginForm: !!loginForm,
    profileForm: !!profileForm,
    saveProfileBtn: !!saveProfileBtn,
    toolbar: !!toolbar
  });

  const MESSAGES = {
    login:   "Please sign in with your Trace3 or Pure Storage email address.",
    profile: "Please fill out your profile to continue.",
    saved:   "Profile saved. Ready."
  };

  if (loginHint)   loginHint.textContent   = MESSAGES.login;
  if (profileHint) profileHint.textContent = MESSAGES.profile;

  if (startButton && "disabled" in startButton) startButton.disabled = true;

  // ---------- Helpers ----------
  const show    = (el) => el && (el.style.display = "block");
  const hide    = (el) => el && (el.style.display = "none");
  const enable  = (el) => el && (el.disabled = false);
  const disable = (el) => el && (el.disabled = true);
  const setStatus = (t) => { if (statusBar) statusBar.textContent = t || ""; };
  const playAudio = (src) => { try { if (src) new Audio(src).play(); } catch {} };

  // Generic fetch that always carries cookies
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
        const first_time = data.profileComplete === false;
        return { authenticated: true, first_time, me: data };
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
    if (r.ok) re
