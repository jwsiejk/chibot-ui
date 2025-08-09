// main.js — consolidated auth/profile gating + toolbar wiring — 2025-08-09g
// main.js — auth/profile gating + Chip + toolbar — 2025-08-09h
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

// Optional floating controls
  const recordBtn      = document.getElementById("recordBtn");
  const recordPrompt   = document.getElementById("recordPrompt");
  const waveform       = document.getElementById("waveform");
  const recordBtn      = $("recordBtn");
  const recordPrompt   = $("recordPrompt");
  const waveform       = $("waveform");

console.log("[main] elements:", {
    startButton: !!startButton,
    loginModal: !!loginModal,
    profileModal: !!profileModal,
    loginForm: !!loginForm,
    profileForm: !!profileForm,
    saveProfileBtn: !!saveProfileBtn,
    toolbar: !!toolbar
    startButton: !!startButton, loginModal: !!loginModal, profileModal: !!profileModal,
    loginForm: !!loginForm, profileForm: !!profileForm, saveProfileBtn: !!saveProfileBtn,
    toolbar: !!toolbar, chipBox: !!chipBox, chipImage: !!chipImage
});

const MESSAGES = {
@@ -44,7 +45,6 @@ document.addEventListener("DOMContentLoaded", () => {

if (loginHint)   loginHint.textContent   = MESSAGES.login;
if (profileHint) profileHint.textContent = MESSAGES.profile;

if (startButton && "disabled" in startButton) startButton.disabled = true;

// ---------- Helpers ----------
@@ -55,7 +55,6 @@ document.addEventListener("DOMContentLoaded", () => {
const setStatus = (t) => { if (statusBar) statusBar.textContent = t || ""; };
const playAudio = (src) => { try { if (src) new Audio(src).play(); } catch {} };

  // Generic fetch that always carries cookies
async function j(path, opts = {}) {
const r = await fetch(path, {
credentials: "include",
@@ -72,8 +71,7 @@ document.addEventListener("DOMContentLoaded", () => {
try {
const { ok, status, data } = await j("/api/me");
if (ok && data && data.email) {
        const first_time = data.profileComplete === false;
        return { authenticated: true, first_time, me: data };
        return { authenticated: true, first_time: data.profileComplete === false, me: data };
}
if (status === 401) return { authenticated: false };
} catch {}
@@ -87,4 +85,178 @@ document.addEventListener("DOMContentLoaded", () => {
async function saveProfileJSON(payload) {
const body = JSON.stringify(payload);
let r = await j("/profile/save", { method: "POST", body });
    if (r.ok) re
    if (r.ok) return true;
    r = await j("/profile", { method: "POST", body });
    if (r.ok) return true;
    r = await j("/api/profile", { method: "POST", body });
    return r.ok;
  }

  // ---------- Gate UI based on auth/profile ----------
  async function gate() {
    disable(startButton);
    const st = await getStatus();

    if (!st.authenticated) {
      show(loginModal); hide(profileModal);
      hide(toolbar); hide(chipBox);
      setStatus("Please sign in.");
      return;
    }
    hide(loginModal);

    if (st.first_time) {
      show(profileModal);
      hide(toolbar); hide(chipBox);
      setStatus("Profile needed to continue.");
      disable(startButton);
    } else {
      hide(profileModal);
      enable(startButton);
      if (toolbar) show(toolbar);
      if (chipBox) show(chipBox);
      setStatus("Ready.");
    }
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

      hide(loginModal);
      if (data && data.first_time === true) {
        show(profileModal);
        disable(startButton);
      } else {
        enable(startButton);
      }
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
      const role   = (fd.get("role")   || title).toString().trim();
      const region = (fd.get("region") || "NA").toString().trim();
      if (!name || !title) { alert("Please complete all fields."); return; }

      try {
        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);
        localStorage.setItem("chip_name", name);
        localStorage.setItem("chip_title", title);
      } catch {}

      const ok = await saveProfileJSON({ name, title, role, region });
      if (ok) {
        hide(profileModal);
        enable(startButton);
        setStatus(MESSAGES.saved);
        gate();
      } else {
        alert("Could not save profile. Please try again.");
      }
    });
  }

  // ---------- Start → greet ----------
  if (startButton && !startButton.dataset.greetWired) {
    startButton.dataset.greetWired = "1";
    startButton.addEventListener("click", async () => {
      try {
        setStatus("Warming up Chip…");
        disable(startButton);
        const { ok, data } = await j("/greet", { method: "POST", body: JSON.stringify({}) });
        if (!ok) { setStatus("Greet failed. Try again."); return; }
        if (data?.reply) setStatus(data.reply);
        playAudio(data?.audio);
      } finally {
        enable(startButton);
      }
    });
  }

  // ---------- Toolbar wiring ----------
  // Ask (prompt → /ask)
  if (btnAsk && !btnAsk.dataset.wired) {
    btnAsk.dataset.wired = "1";
    btnAsk.addEventListener("click", async () => {
      const q = prompt("Ask Chip:");
      if (!q) return;
      setStatus("Thinking…");
      const { ok, data } = await j("/ask", {
        method: "POST",
        body: JSON.stringify({ question: q })
      });
      if (!ok) { setStatus("Request failed. Try again."); return; }
      if (data?.response) setStatus(data.response);
      playAudio(data?.audio);
    });
  }

  // Mic placeholder (hook streaming later)
  if (btnMic && !btnMic.dataset.wired) {
    btnMic.dataset.wired = "1";
    btnMic.addEventListener("click", () => {
      alert("Voice input coming soon. Use Ask for now.");
    });
  }

  // History
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

  // Profile (open modal)
  if (btnProfile && !btnProfile.dataset.wired) {
    btnProfile.dataset.wired = "1";
    btnProfile.addEventListener("click", () => {
      if (profileModal) { show(profileModal); profileModal.removeAttribute("hidden"); }
    });
  }

  // Logout
  if (btnLogout && !btnLogout.dataset.wired) {
    btnLogout.dataset.wired = "1";
    btnLogout.addEventListener("click", async () => {
      await j("/api/logout", { method: "POST" });
      location.reload();
    });
  }

  // ---------- Initial run ----------
  gate();
});
