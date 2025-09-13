// static/js/auth_gate.js
// Production-safe auth/profile gate controller (no re-entrancy, CSRF-aware)

import { ensureCSRF, installFetchInterceptor } from './csrf.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

function qsAny(selectors) {
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) return el;
  }
  return null;
}

function setStartEnabled(enabled) {
  const btn = document.getElementById('startButton') || document.getElementById('start');
  if (btn) {
    btn.disabled = !enabled;
    btn.title = enabled ? '' : 'Please complete your profile to continue';
  }
}

function showEl(el, yes) {
  if (!el) return;
  // Prefer 'hidden' class if present, otherwise use style
  if (el.classList) el.classList.toggle('hidden', !yes);
  else el.style.display = yes ? '' : 'none';
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

// -------- Field/ID resolution that tolerates markup differences --------
function readValue(labelFallbacks, idFallbacks, nameFallbacks, dataFallbacks) {
  let el = qsAny(idFallbacks.map((id) => `#${id}`))
        || qsAny(nameFallbacks.map((nm) => `input[name="${nm}"]`))
        || qsAny(dataFallbacks.map((d) => `[data-field="${d}"]`));

  if (!el) {
    // very defensive: try to find by label text
    const labels = $$('label');
    const match = labels.find((l) => {
      const txt = (l.textContent || '').trim().toLowerCase();
      return labelFallbacks.some((want) => txt === want || txt.startsWith(want + ' '));
    });
    if (match) {
      const forId = match.getAttribute('for');
      if (forId) el = document.getElementById(forId);
      if (!el) el = match.nextElementSibling && match.nextElementSibling.tagName === 'INPUT'
        ? match.nextElementSibling : null;
    }
  }
  return (el && 'value' in el) ? (el.value || '') : '';
}

function findSaveButton() {
  return qsAny([
    '#saveProfileButton',
    '[data-action="profile-save"]',
    '#profileModal button.save',
    '.profile button.save',
    'button#save',         // last resort patterns
    'button.save'
  ]);
}

function findLoginButton() {
  return qsAny([
    '#loginButton',
    '[data-action="auth-login"]',
    'button#login',
    'button.login'
  ]);
}

// -------- Gate evaluation --------
let evalLock = false;

export async function evaluateAuth() {
  if (evalLock) return;
  evalLock = true;
  try {
    await ensureCSRF();

    const me = await getJSON('/api/v1/auth/me');

    const loginModal   = document.getElementById('loginModal');
    const profileModal = document.getElementById('profileModal');

    if (!me.authenticated) {
      showEl(loginModal, true);
      showEl(profileModal, false);
      setStartEnabled(false);
      return;
    }

    if (!me.profile_complete) {
      showEl(loginModal, false);
      showEl(profileModal, true);
      setStartEnabled(false);
      return;
    }

    showEl(loginModal, false);
    showEl(profileModal, false);
    setStartEnabled(true);
    document.dispatchEvent(new CustomEvent('auth_ready', { detail: me }));
  } catch (err) {
    console.warn('[auth_gate] evaluateAuth failed:', err);
    setStartEnabled(false);
  } finally {
    evalLock = false;
  }
}

// -------- Wire once --------
let wired = false;

export function wireAuthGate() {
  if (wired) return;
  wired = true;

  try { installFetchInterceptor(); } catch {}

  // LOGIN
  const loginBtn = findLoginButton();
  if (loginBtn && !loginBtn.__wired) {
    loginBtn.__wired = true;
    on(loginBtn, 'click', async () => {
      try {
        const email = readValue(
          ['email'],
          ['loginEmail'],
          ['email'],
          ['email']
        );
        if (!email) return;
        await postJSON('/api/v1/auth/login', { email });
        await evaluateAuth();
      } catch (e) {
        console.warn('[auth_gate] login failed:', e);
      }
    });
  }

  // PROFILE SAVE
  const saveBtn = findSaveButton();
  if (saveBtn && !saveBtn.__wired) {
    saveBtn.__wired = true;
    on(saveBtn, 'click', async () => {
      try {
        const name   = readValue(['name'],  ['profileName'],  ['name'],  ['name']);
        const title  = readValue(['role','title'], ['profileTitle'], ['title','role'], ['title','role']);
        const region = readValue(['region'], ['profileRegion'], ['region'], ['region']);
        await postJSON('/api/v1/profile', { name, title, region });
        window.dispatchEvent(new CustomEvent('profile_saved', { detail: { complete: !!(name && title) } }));
        await evaluateAuth();
      } catch (e) {
        console.warn('[auth_gate] profile save failed:', e);
      }
    });
  }

  // Kick once on DOM ready
  if (document.readyState !== 'loading') evaluateAuth();
  else document.addEventListener('DOMContentLoaded', evaluateAuth);
}

// Auto-boot when imported
try { wireAuthGate(); } catch (e) { console.warn('[auth_gate] wire failed:', e); }
``
