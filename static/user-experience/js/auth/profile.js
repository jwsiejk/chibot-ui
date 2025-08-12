// auth/profile.js — profile gating, layout, handlers
import { $, show, hide, setToolbarHeightVar, _getQueryParam } from "../core/dom.js";
import { _chipSetAdmin, _chipGuide, _chipSetState, _chipStep } from "../core/state.js";
import { j } from "../core/api.js";

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

export async function loadProfileIntoForm() {
  const profileForm = $("profileForm"); if (!profileForm) return;
  const getI = (n) => profileForm.querySelector(`input[name="${n}"]`);
  const nameI = getI("name"), titleI = getI("title"), emailI = getI("email");

  // Try server first
  try {
    const r = await fetch("/api/profile", { credentials: "include" });
    if (r.status === 401) {
      // Not logged in; let gate() handle showing login
      return;
    }
    if (r.ok) {
      const js = await r.json();
      // Support both shapes: {profile:{...}} or flat { name, title, email }
      const p = (js && (js.profile || js)) || {};
      if (nameI)  nameI.value  = p.name  || "";
      if (titleI) titleI.value = p.title || "";
      if (emailI) emailI.value = p.email || "";
      return;
    }
  } catch { /* fall back to localStorage */ }

  // Fallback to localStorage (best‑effort)
  try {
    if (nameI)  nameI.value  = localStorage.getItem("profileName")  || "";
    if (titleI) titleI.value = localStorage.getItem("profileTitle") || "";
    if (emailI) emailI.value = localStorage.getItem("profileEmail") || "";
  } catch {}
}

export function applyAuthedLayout() {
  const appEl   = $("app");
  const chipBox = $("chipBox");
  const toolbar = $("askChipToolbar");

  show(appEl, "block");
  show(chipBox, "grid");
  show(toolbar, "flex");
  setToolbarHeightVar();

  // Resize/anchor the avatar
  if (window.ChipViseme && typeof window.ChipViseme.layout === "function") {
    window.ChipViseme.setAnchor(0.49, 0.46);
    window.ChipViseme.setSize(0.095, 0.075);
    window.ChipViseme.layout();
  }
}

export async function enforceProfileCompleteness({ applyLayout = true } = {}) {
  const appEl = $("app");
  const loginModal = $("loginModal");

  try {
    const { ok, status, data } = await j("/api/me");
    if (data) {
      _chipSetAdmin(!!data.isAdmin, _getQueryParam("debug"));
      _chipStep("me", data);
    }

    if (!ok) {
      // Treat any failure as unauth; show login
      hide(appEl);
      show(loginModal, "flex");
      return { ok: false, reason: (status === 401 ? "unauthenticated" : "server") };
    }

    // Prefer explicit profileComplete; fall back to inverse of first_time if needed
    const profileComplete = (data?.profileComplete !== undefined)
      ? !!data.profileComplete
      : (data?.first_time === false);

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
  } catch {
    hide(appEl);
    show(loginModal, "flex");
    return { ok: false, reason: "error" };
  }
}

// gate() defaults to NO layout changes during active session
export async function gate(opts = { applyLayout: false }) {
  hide($("profileModal"));
  return await enforceProfileCompleteness(opts);
}

export function wireLoginAndProfileHandlers() {
  const loginForm   = $("loginForm");
  const profileForm = $("profileForm");
  const saveBtn     = $("saveProfileBtn");
  const loginModal  = $("loginModal");

  // --- Login form ---
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

      try { localStorage.setItem("profileEmail", email); } catch {}
      const emailInput = profileForm?.querySelector('input[name="email"]');
      if (emailInput) emailInput.value = email;

      hide(loginModal);
      await gate({ applyLayout: true });
      _chipGuide("Press Start or Chat to speak with Chip.");
    });
  }

  // --- Save profile ---
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
        body: JSON.stringify({ name, title, email })
      });

      const saved = !!(r.data?.ok || r.data?.success);
      if (!r.ok || !saved) {
        alert(r.data?.error || "Could not save profile. Please try again.");
        return;
      }

      hide($("profileModal"));
      if ((($("profileModal")?.dataset.mode) || "edit") === "gate") {
        applyAuthedLayout();
      }
      _chipGuide("Press Start or Chat to speak with Chip.");
      _chipSetState("idle");
      alert("Profile saved.");
    });
  }
}
