import { ensureCSRF, installFetchInterceptor } from '/static/js/csrf.js';
// static/js/auth_gate.js
// Auth/profile gate for AskChip UI: enables/disables Start based on /auth/me,
// wires Login + Profile Save, CSRF-aware, de-duplicated wiring, fail-open on errors.


const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

// ---------- UI helpers ----------
function setStartEnabled(enabled) {
  const btn = $('#startButton') || $('#start');
  if (btn) {
    btn.disabled = !enabled;
    btn.title = enabled ? '' : 'Please complete your profile to continue';
  }
}
function show(el, yes) {
  if (!el) return;
  if (el.classList) el.classList.toggle('hidden', !yes);
  else el.style.display = yes ? '' : 'none';
}
function showBanner(msg) {
  const b = $('#inlineLoginMsg');
  if (!b) { console.warn('[auth_gate]', msg); return; }
  b.textContent = msg;
  b.classList.add('warn');
}

// ---------- HTTP helpers ----------
async function getJSON(url, init = {}) {
  init.credentials = init.credentials || 'include';
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${url}`);
  return res.json();
}
async function postJSON(url, body) {
  await ensureCSRF();
  return getJSON(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// ---------- Field accessors (match your DOM) ----------
function valueById(id) {
  const el = document.getElementById(id);
  return el && 'value' in el ? (el.value || '').trim() : '';
}
function valueFromLabel(labelsLower) {
  const lbl = $$('label').find(L => {
    const t = (L.textContent || '').trim().toLowerCase();
    return labelsLower.some(w => t === w || t.startsWith(`${w} `));
  });
  if (!lbl) return '';
  const forId = lbl.getAttribute('for');
  if (forId) {
    const el = document.getElementById(forId);
    return el && 'value' in el ? (el.value || '').trim() : '';
  }
  const sib = lbl.nextElementSibling;
  return (sib && sib.tagName === 'INPUT') ? (sib.value || '').trim() : '';
}
function readProfileFields() {
  // Supported ids: #prof_name, #prof_role (or #profileTitle), #prof_region
  const name   = valueById('prof_name')   || valueFromLabel(['name']);
  const title  = valueById('prof_role')   || valueById('profileTitle') || valueFromLabel(['role','title']);
  const region = valueById('prof_region') || valueFromLabel(['region']);
  return { name, title, region };
}

// ---------- Gate evaluation ----------
let evalLock = false;

export async function evaluateAuth() {
  if (evalLock) return;
  evalLock = true;
  try {
    // ✅ Fail-open immediately so first paint has Start enabled.
    // We'll only disable if we KNOW the user must be gated.
    setStartEnabled(true);

    const me = await getJSON('/api/v1/auth/me');
    // Fire a state event for observability/tools
    window.dispatchEvent(new CustomEvent('askchip-auth-state', { detail: me }));

    const loginModal   = $('#loginModal');
    const profileModal = $('#profileModal');

    // Always mirror email into the profile form so it never appears blank
    const emailField = document.getElementById('prof_email');
    const emailVal = me?.profile?.email || me?.email || '';
    if (emailField && emailVal) { try { emailField.value = emailVal; } catch(e){} }

    if (!me.authenticated) {
      show(loginModal, true);
      show(profileModal, false);
      setStartEnabled(false);
      showBanner('Please log in to continue.');
      return;
    }

    // Prefer top-level profile_complete, fall back to nested
    const complete = !!(me.profile_complete || me.profile?.profile_complete);

    if (!complete) {
      show(loginModal, false);
      show(profileModal, true);
      setStartEnabled(false);
      showBanner('Please fill out your profile to continue.');
      return;
    }

    // Ready
    show(loginModal, false);
    show(profileModal, false);
    setStartEnabled(true);
    // Back-compat + explicit event
    document.dispatchEvent(new CustomEvent('auth_ready', { detail: me }));
    window.dispatchEvent(new CustomEvent('askchip-auth-ready', { detail: me }));
  } catch (e) {
    // Fail-open so the user isn't stranded if /auth/me is temporarily failing
    console.warn('[auth_gate] evaluateAuth failed (fail-open):', e);
    setStartEnabled(true);
  } finally {
    evalLock = false;
  }
}

// ---------- Wire once ----------
let wired = false;

export function wireAuthGate() {
  // Safety guard: prevent duplicate profile-gate wiring
  if (window.__authgateWired) {
    console.warn('[auth_gate] duplicate wiring prevented');
    return;
  }
  window.__authgateWired = true;

  if (wired) return;
  wired = true;

  try { installFetchInterceptor(); } catch {}

  // LOGIN
  const loginBtn = $('#inlineLoginSubmit') || $('#loginButton') || $('[data-action="auth-login"]');
  if (loginBtn && !loginBtn.__wired) {
    loginBtn.__wired = true;
    on(loginBtn, 'click', async (e) => {
      e.preventDefault();
      try {
        const emailEl = $('#inlineLoginEmail');
        const email = (emailEl && emailEl.value || '').trim();
        if (!email) { showBanner('Enter your email to log in.'); return; }
        await postJSON('/api/v1/auth/login', { email });
        await evaluateAuth();
      } catch (err) {
        console.warn('[auth_gate] login failed:', err);
        showBanner('Login failed. Try again.');
      }
    });
  }

  // PROFILE SAVE (your button id is #profileSave)
  const saveBtn = $('#profileSave') || $('#saveProfileButton') || $('[data-action="profile-save"]');
  if (saveBtn && !saveBtn.__wired) {
    saveBtn.__wired = true;
    on(saveBtn, 'click', async (e) => {
      e.preventDefault();
      try {
        const { name, title, region } = readProfileFields();
        await postJSON('/api/v1/profile', { name, title, region });
        // Let the rest of the app know we’re good now
        window.dispatchEvent(new CustomEvent('askchip-profile-saved', { detail: { complete: !!(name && title) } }));
        await evaluateAuth();
      } catch (err) {
        console.warn('[auth_gate] profile save failed:', err);
        showBanner('Profile save failed. Check fields and try again.');
      }
    });
  }

  // Re-enable Start when a session ends (quality-of-life, harmless if unused)
  window.addEventListener('askchip-session-ended', () => setStartEnabled(true));

  // Kick once on DOM ready (guarded)
  if (document.readyState !== 'loading') evaluateAuth();
  else document.addEventListener('DOMContentLoaded', evaluateAuth);
}

// Auto-boot
try { wireAuthGate(); } catch (e) { console.warn('[auth_gate] wire failed:', e); }