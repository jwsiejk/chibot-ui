// static/js/auth_gate.js
// Production-safe auth/profile gate controller (no double calls, CSRF-aware)

import { ensureCSRF, installFetchInterceptor } from './csrf.js';

const $ = (s) => document.querySelector(s);
const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

function setStartEnabled(enabled) {
  const btn = document.getElementById('startButton') || document.getElementById('start');
  if (btn) {
    btn.disabled = !enabled;
    btn.title = enabled ? '' : 'Please complete your profile to continue';
  }
}

function show(el, yes) {
  if (!el) return;
  el.classList.toggle('hidden', !yes);
}

async function getJSON(url, init = {}) {
  init.credentials = init.credentials || 'include';
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${url}`);
  return res.json();
}

async function postJSON(url, body) {
  return getJSON(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// ---- Gate evaluation ----
let evalLock = false;

export async function evaluateAuth() {
  if (evalLock) return;
  evalLock = true;
  try {
    // Make sure CSRF is primed before first POST
    await ensureCSRF();

    const me = await getJSON('/api/v1/auth/me');

    const loginModal = document.getElementById('loginModal');
    const profileModal = document.getElementById('profileModal');

    if (!me.authenticated) {
      show(loginModal, true);
      show(profileModal, false);
      setStartEnabled(false);
      return;
    }

    // Authenticated
    if (!me.profile_complete) {
      show(loginModal, false);
      show(profileModal, true);
      setStartEnabled(false);
      return;
    }

    // Ready
    show(loginModal, false);
    show(profileModal, false);
    setStartEnabled(true);
    document.dispatchEvent(new CustomEvent('auth_ready', { detail: me }));
  } catch (err) {
    // Non-fatal: keep UI usable but show a hint
    console.warn('[auth_gate] evaluateAuth failed:', err);
    setStartEnabled(false);
  } finally {
    evalLock = false;
  }
}

// ---- Wiring ----
export function wireAuthGate() {
  // Make fetch CSRF-aware (idempotent in csrf.js)
  try { installFetchInterceptor(); } catch {}

  const loginBtn = document.getElementById('loginButton');
  const profileSaveBtn = document.getElementById('saveProfileButton');

  on(loginBtn, 'click', async () => {
    try {
      const email = (document.getElementById('loginEmail') || {}).value || '';
      if (!email) return;
      await postJSON('/api/v1/auth/login', { email });
      await evaluateAuth();
    } catch (e) {
      console.warn('[auth_gate] login failed:', e);
    }
  });

  on(profileSaveBtn, 'click', async () => {
    try {
      const name = (document.getElementById('profileName') || {}).value || '';
      const title = (document.getElementById('profileTitle') || {}).value || '';
      const region = (document.getElementById('profileRegion') || {}).value || '';
      await postJSON('/api/v1/profile', { name, title, region });
      window.dispatchEvent(new CustomEvent('profile_saved', { detail: { complete: !!(name && title) } }));
      await evaluateAuth();
    } catch (e) {
      console.warn('[auth_gate] profile save failed:', e);
    }
  });

  // Kick once on DOM ready
  if (document.readyState !== 'loading') evaluateAuth();
  else document.addEventListener('DOMContentLoaded', evaluateAuth);
}

// Auto-boot when imported as a module
try { wireAuthGate(); } catch (e) { console.warn('[auth_gate] wire failed:', e); }
