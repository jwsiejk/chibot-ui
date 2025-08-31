// auth/profile.js — gating, login/profile handlers (r5 hotfix)
import { $, show, hide } from "../core/dom.js";
import { j } from "../core/api.js";

/* ----------------------------- Utilities ----------------------------- */
const byId = (id) => document.getElementById(id);
const appEl = () => byId("app") || byId("layout");
const loginModal = () => byId("loginModal");
const profileModal = () => byId("profileModal");

function applyAuthedLayout(){
  const app = appEl(); if (app) { try { app.removeAttribute("inert"); } catch {} show(app); }
  hide(loginModal()); hide(profileModal());
}

async function fetchMe(){
  try {
    const r = await j("/api/me");
    if (!r || !r.ok) return null;
    const d = r.data || null; if (!d) return null;
    if (typeof d.authenticated === "undefined") d.authenticated = !!d.logged_in;
    if (typeof d.profileComplete === "undefined") d.profileComplete = !!(d.profile_complete ?? d.profileComplete);
    if (typeof d.first_time === "undefined" && typeof d.profileComplete === "boolean") d.first_time = !d.profileComplete;
    return d;
  } catch { return null; }
}

async function fetchProfilePrefill(){
  try {
    const r = await j("/api/profile");
    if (!r || !r.ok) return null;
    return r.data || null;
  } catch { return null; }
}

/* --------------------------- Exported helpers ------------------------- */
export function setProfileModalMode(mode = "gate"){
  const hint = byId("profileHint");
  if (hint) {
    hint.textContent = (mode === "gate")
      ? "Please fill out your profile to continue"
      : "Edit your profile";
  }
}

export async function loadProfileIntoForm(){
  const form = byId("profileForm"); if (!form) return;
  const name  = form.querySelector('input[name="name"]');
  const title = form.querySelector('input[name="title"]');
  const email = form.querySelector('input[name="email"]');
  const data = await fetchProfilePrefill();
  if (data){
    if (name && data.name) name.value = data.name;
    if (title && data.title) title.value = data.title;
    if (email && data.email) email.value = data.email;
  }
}

/**
 * Primary gate used by Start/Send:
 * - If /api/me is unavailable or errors → FAIL OPEN (returns {ok:true})
 * - If explicitly unauthenticated and login modal exists → show it and block ({ok:false})
 * - If authenticated but profile is incomplete and profile modal exists → show it and block
 * - Otherwise → allow
 */
export async function gate(opts = { applyLayout: false }){
  const applyLayout = !!(opts && opts.applyLayout);
  const me = await fetchMe(); // may be null

  // If we can't reach the endpoint, fail open so buttons still work
  if (!me) {
    if (applyLayout) try { applyAuthedLayout(); } catch {}
    return { ok: true, reason: "me-unavailable-fail-open" };
  }

  if (!me.authenticated) {
    const lm = loginModal();
    if (lm) {
      if (applyLayout) { hide(appEl()); }
      show(lm, "flex");
      return { ok: false, reason: "unauthenticated" };
    }
    // No login modal wired → proceed
    if (applyLayout) try { applyAuthedLayout(); } catch {}
    return { ok: true, reason: "unauthenticated-no-modal" };
  }

  // Authenticated
  const profileComplete = (me.profileComplete !== undefined)
    ? !!me.profileComplete
    : (me.first_time === false);

  if (!profileComplete) {
    const pm = profileModal();
    if (pm) {
      setProfileModalMode("gate");
      await loadProfileIntoForm();
      show(pm, "flex");
      hide(byId("askChipToolbar"));
      return { ok: false, reason: "incomplete-profile" };
    }
  }

  if (applyLayout) try { applyAuthedLayout(); } catch {}
  return { ok: true };
}

export function wireLoginAndProfileHandlers(){
  // Login form
  const loginForm = byId("loginForm");
  if (loginForm) {
    loginForm.addEventListener("submit", async (e)=>{
      e.preventDefault();
      const fd = new FormData(loginForm);
      const email = (fd.get("email") || "").toString().trim().toLowerCase();
      if (!email) return;
      try { await fetch("/api/login", { method:"POST", credentials:"include", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ email }) }); }
      catch {}
      applyAuthedLayout();
    });
  }

  // Profile save
  const saveBtn = byId("saveProfileBtn");
  const form    = byId("profileForm");
  if (saveBtn && form) {
    saveBtn.addEventListener("click", async ()=>{
      const fd = new FormData(form);
      const data = {
        name : (fd.get("name")  || "").toString().trim(),
        title: (fd.get("title") || "").toString().trim(),
        email: (fd.get("email") || "").toString().trim().toLowerCase(),
      };
      if (!data.name || !data.title || !data.email) return;
      try {
        const r = await fetch("/api/profile", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data)
        });
        if (r.ok) {
          hide(profileModal());
          show(byId("askChipToolbar"));
        }
      } catch {}
    });
  }
}
