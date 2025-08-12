// auth/profile.js — robust gating + handlers + layout (with exported loadProfileIntoForm)
import { $, show, hide, setToolbarHeightVar, _getQueryParam } from "../core/dom.js";
import { _chipSetAdmin, _chipGuide, _chipSetState, _chipStep } from "../core/state.js";
import { j } from "../core/api.js";

/* ---------------- UI helpers ---------------- */

export function setProfileModalMode(mode) {
  const profileModal = $("profileModal");
  const profileHint  = $("profileHint");
  const saveBtn      = $("saveProfileBtn");
  if (!profileModal) return;

  profileModal.dataset.mode = mode; // 'gate' | 'edit'

  const titleEl = profileModal.querySelector("h2");
  if (titleEl) titleEl.textContent = (mode === "gate") ? "Complete Your Profile" : "Your Profile";

  if (profileHint) {
    if (mode === "gate") {
      profileHint.textContent = "Please fill out your profile to continue.";
      profileHint.style.display = "block";
    } else {
      profileHint.textContent = "";
      profileHint.style.display = "none";
    }
  }

  if (saveBtn) saveBtn.textContent = (mode === "gate") ? "Save & Continue" : "Save changes";
}

export function applyAuthedLayout() {
  const appEl   = $("app");
  const chipBox = $("chipBox");
  const toolbar = $("askChipToolbar");

  show(appEl, "block");
  show(chipBox, "grid");
  show(toolbar, "flex");
  setToolbarHeightVar();

  if (window.ChipViseme && typeof window.ChipViseme.layout === "function") {
    window.ChipViseme.setAnchor(0.49, 0.46);
    window.ChipViseme.setSize(0.095, 0.075);
    window.ChipViseme.layout();
  }
}

/* ---------------- Data helpers ---------------- */

async function fetchMe() {
  try {
    const r = await fetch("/api/me", { credentials: "include" });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

async function fetchProfilePrefill() {
  // Only call after we know we're authenticated.
  try {
    const r = await fetch("/api/profile", { credentials: "include" });
    if (!r.ok) return null; // includes 401
    return await r.json();
  } catch {
    return null;
  }
}

/** Exported: safe to call anytime; no-ops if unauthenticated. */
export async function loadProfileIntoForm() {
  const profileForm = $("profileForm"); if (!profileForm) return;

  const getI = (n) => profileForm.querySelector(`input[name="${n}"]`);
  const nameI = getI("name"), titleI = getI("title"), emailI = getI("email");

  const me = await fetchMe();
  if (!me || !me.authenticated) {
    // Not logged in yet — avoid calling /api/profile (prevents 401s).
    try {
      if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
      if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
      if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
    } catch {}
    return;
  }

  const js = await fetchProfilePrefill();
  if (js) {
    const p = js.profile || js;
    if (nameI)  nameI.value  = p.name  || "";
    if (titleI) titleI.value = p.title || "";
    if (emailI) emailI.value = p.email || "";
    return;
  }

  // Last-ditch localStorage
  try {
    if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
    if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
    if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
  } catch {}
}

/* ---------------- Gating ---------------- */

export async function enforceProfileCompleteness({ applyLayout = true } = {}) {
  const appEl = $("app");
  const loginModal = $("loginModal");

  const me = await fetchMe();

  if (!me || !me.authenticated) {
    hide(appEl);
    show(loginModal, "flex");
    return { ok: false, reason: "unauthenticated" };
  }

  // Prefer explicit profileComplete; fall back to inverse of first_time.
  const profileComplete = (me.profileComplete !== undefined)
    ? !!me.profileComplete
    : (me.first_time === false);

  if (!profileComplete) {
    setProfileModalMode("gate");
    await loadProfileIntoForm();
    show($("profileModal"), "flex");
    hide($("askChipToolbar"));
    return { ok: false, reason: "incomplete" };
  }

  if (applyLayout) {
    applyAuthedLayout();
    _chipSetState("idle");
    _chipGuide("Press Start or Chat to speak with Chip.");
  }
  return { ok: true };
}

// Convenience wrapper the rest of the app can call
export async function gate(opts = { applyLayout: false }) {
  hide($("profileModal"));
  return await enforceProfileCompleteness(opts);
}

/* ---------------- Wire handlers ---------------- */

export function wireLoginAndProfileHandlers() {
  const loginForm   = $("loginForm");
  const profileForm = $("profileForm");
  const saveBtn     = $("saveProfileBtn");
  const loginModal  = $("loginModal");

  // Login
  if (loginForm && !loginForm.dataset.wired) {
    loginForm.dataset.wired = "1";
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;

      const { ok, data, status } = await j("/api/login", {
        method: "POST",
        body: JSON.stringify({ email }),
      });

      if (!ok) {
        alert((data && data.error) || `Login failed (${status})`);
        return;
      }

      try { localStorage.setItem("profileEmail", email); } catch {}
      hide(loginModal);
      const r = await gate({ applyLayout: true });
      if (!r.ok && r.reason === "incomplete") {
        // profile modal is already shown by gate()
      }
    });
  }

  // Save profile
  if (saveBtn && !saveBtn.dataset.wired) {
    saveBtn.dataset.wired = "1";
    saveBtn.addEventListener("click", async () => {
      if (!profileForm) return;

      const fd = new FormData(profileForm);
      const name  = (fd.get("name")  || "").toString().trim();
      const title = (fd.get("title") || "").toString().trim();
      const email = (fd.get("email") || "").toString().trim();
      if (!name || !title || !email) { alert("Please complete all fields."); return; }

      try {
        localStorage.setItem("profileName", name);
        localStorage.setItem("profileTitle", title);
        localStorage.setItem("profileEmail", email);
      } catch {}

      const r = await j("/api/profile", {
        method: "POST",
        body: JSON.stringify({ name, title, email, region: "NA" })  // ensure region is set
      });

      const saved = !!(r.data?.ok || r.data?.success);
      if (!r.ok || !saved) {
        alert(r.data?.error || "Could not save profile. Please try again.");
        return;
      }

      hide($("profileModal"));
      applyAuthedLayout();
      _chipSetState("idle");
      _chipGuide("Press Start or Chat to speak with Chip.");
      alert("Profile saved.");
    });
  }
}

/* ---------------- Bootstrap on load ---------------- */

if (!window.__chipProfileBoot) {
  window.__chipProfileBoot = true;
  document.addEventListener("DOMContentLoaded", async () => {
    try { wireLoginAndProfileHandlers(); } catch {}
    await gate({ applyLayout: true });
  });
}
