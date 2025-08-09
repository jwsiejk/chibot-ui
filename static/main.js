// main.js — consolidated auth/profile gating (guided, JSON login) — 2025-08-09e
document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ main.js loaded (consolidated)");

  // Elements (wider selectors + guards)
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

  console.log("[main] elements:", {
    startButton: !!startButton,
    loginModal: !!loginModal,
    profileModal: !!profileModal,
    loginForm: !!loginForm,
    profileForm: !!profileForm,
    saveProfileBtn: !!saveProfileBtn
  });

  const MESSAGES = {
    login:   "Please sign in with your Trace3 or Pure Storage email address.",
    profile: "Please fill out your profile to continue.",
    saved:   "Profile saved. Ready."
  };

  if (loginHint)   loginHint.textContent   = MESSAGES.login;
  if (profileHint) profileHint.textContent = MESSAGES.profile;

  if (startButton && "disabled" in startButton) startButton.disabled = true;

  // Helpers
  const show    = (el) => el && (el.style.display = "block");
  const hide    = (el) => el && (el.style.display = "none");
  const enable  = (el) => el && (el.disabled = false);
  const disable = (el) => el && (el.disabled = true);

  // Generic fetch that always carries cookies (same-site or cross-subdomain)
  async function j(path, opts = {}) {
    const r = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts
    });
    const contentType = r.headers.get("content-type") || "";
    let data = null;
    try { data = contentType.includes("application/json") ? await r.json() : null; } catch {}
    return { ok: r.ok, status: r.status, data };
  }

  async function getStatus() {
    // Preferred: session check via /api/me
    try {
      const { ok, status, data } = await j("/api/me");
      if (ok && data && data.email) {
        // If profileComplete present, infer first_time
        const first_time = data.profileComplete === false;
        return { authenticated: true, first_time };
      }
      if (status === 401) return { authenticated: false };
    } catch {}
    // Fallback to legacy status endpoint
    try {
      const { ok, data } = await j("/auth/status");
      if (ok && data) return data;
    } catch {}
    return { authenticated: false };
  }

  async function saveProfileJSON(payload) {
    const body = JSON.stringify(payload);
    // Try new path first
    let r = await j("/profile/save", { method: "POST", body });
    if (r.ok) return true;
    // Fallbacks
    r = await j("/profile", { method: "POST", body });
    if (r.ok) return true;
    r = await j("/api/profile", { method: "POST", body });
    return r.ok;
  }

  async function gate() {
    disable(startButton);
    const st = await getStatus();

    if (!st.authenticated) {
      show(loginModal);
      hide(profileModal);
      return;
    }
    hide(loginModal);

    if (st.first_time) {
      show(profileModal);
      disable(startButton);
    } else {
      hide(profileModal);
      enable(startButton);
    }
  }

  // Login (JSON → /api/login). Password field looks present but is ignored.
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

      // Re-check session state to sync with any other code that calls /api/me
      gate();
    });
  }

  // Profile Save (JSON → /profile/save fallback /profile and /api/profile)
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
        if (statusBar) statusBar.textContent = MESSAGES.saved;
      } else {
        alert("Could not save profile. Please try again.");
      }
    });
  }

  // Initial state
  gate();
});
