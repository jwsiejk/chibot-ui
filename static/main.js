// main.js — consolidated auth/profile gating (guided, JSON login) — 2025-08-09
document.addEventListener("DOMContentLoaded", () => {
  console.log("✅ main.js loaded (consolidated)");

  // Elements
  const loginModal     = document.getElementById("loginModal");
  const profileModal   = document.getElementById("profileModal");
  const startButton    = document.getElementById("startButton");
  const loginForm      = document.getElementById("loginForm");
  const profileForm    = document.getElementById("profileForm");
  const saveProfileBtn = document.getElementById("saveProfileBtn");
  const loginHint      = document.getElementById("loginHint");
  const profileHint    = document.getElementById("profileHint");
  const statusBar      = document.getElementById("status");

  // Guided copy (centralized)
  const MESSAGES = {
    login:   "Please sign in with your Trace3 or Pure Storage email address.",
    profile: "Please fill out your profile to continue.",
    saved:   "Profile saved. Ready."
  };

  if (loginHint)   loginHint.textContent   = MESSAGES.login;
  if (profileHint) profileHint.textContent = MESSAGES.profile;
  if (startButton) startButton.disabled = true;

  // Helpers
  const show    = (el) => el && (el.style.display = "block");
  const hide    = (el) => el && (el.style.display = "none");
  const enable  = (el) => el && (el.disabled = false);
  const disable = (el) => el && (el.disabled = true);

  async function getStatus() {
    try {
      const r = await fetch("/auth/status", { credentials: "same-origin" });
      if (!r.ok) return { authenticated: false };
      return await r.json();
    } catch {
      return { authenticated: false };
    }
  }

  async function saveProfileJSON(payload) {
    const opts = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    };
    try {
      const r1 = await fetch("/profile/save", opts);
      if (r1.ok) return true;
    } catch {}
    try {
      const r2 = await fetch("/profile", opts);
      if (r2.ok) return true;
    } catch {}
    return false;
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

  // Login (JSON → /login), password field stays disabled (preview)
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();

      // Enforce disabled password for preview clarity
      const pw = document.querySelector('input[name="password"]');
      if (pw) pw.disabled = true;

      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim();
      if (!email) return;

      try {
        const r = await fetch("/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ email })
        });
        const data = await r.json().catch(() => ({}));

        if (!r.ok) {
          alert(data?.error || "Login failed. Use your Trace3 or Pure Storage email address.");
          return;
        }

        hide(loginModal);

        if (data.first_time === true) {
          show(profileModal);
          disable(startButton);
        } else {
          enable(startButton);
        }
      } catch (err) {
        console.error("Login error:", err);
        alert("Login failed. Please try again.");
      }
    });
  }

  // Profile Save (JSON → /profile/save fallback /profile)
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

      // Persist locally for continuity
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
