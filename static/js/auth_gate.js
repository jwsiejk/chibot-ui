// static/js/auth_gate.js
// Auth/profile gate for chibot-uiv150: matches #prof_* fields and #profileSave,
// CSRF-aware via csrf.js, de-dup wired handlers, and re-entrancy guard on evaluate.

import { ensureCSRF, installFetchInterceptor } from './csrf.js';

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

// ---------- UI helpers ----------
function prefillFromMe(me){
  try{
    const prof = (me && me.profile) || {};
    const em = document.getElementById('prof_email');
    if (em) { em.value = (me && me.email) || (prof.email || ''); em.disabled = true; }
    const nm = document.getElementById('prof_name');     if (nm && prof.name   != null) nm.value   = prof.name   || '';
    const rl = document.getElementById('prof_role');     if (rl){ const t = (prof.title!=null?prof.title:prof.role); if (t!=null) rl.value = t || ''; }
    const rg = document.getElementById('prof_region');   if (rg && prof.region != null) rg.value   = prof.region || '';
    const co = document.getElementById('prof_company');  if (co && prof.company!= null) co.value   = prof.company|| '';
  }catch(_){}
}

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

// ---------- HTTP helpers ----------
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

// ---------- Field accessors (match your DOM) ----------
function valueById(id) {
  const el = document.getElementById(id);
  return el && 'value' in el ? (el.value || '').trim() : '';
}
function readProfileFields() {
  // Your current template uses #prof_name, #prof_role, #prof_region
  const name   = valueById('prof_name')   || valueFromLabel(['name']);
  const title  = valueById('prof_role')   || valueById('profileTitle') || valueFromLabel(['role','title']);
  const region = valueById('prof_region') || valueFromLabel(['region']);
  return { name, title, region };
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

// ---------- Gate evaluation ----------
let evalLock = false;

export async function evaluateAuth() {
  if (evalLock) return;
  evalLock = true;
  try {
    await ensureCSRF();

    const me = await getJSON('/api/v1/auth/me');
    const loginModal   = $('#loginModal');
    const profileModal = $('#profileModal');

    if (!me.authenticated) {
      show(loginModal, true);
      show(profileModal, false);
      setStartEnabled(false);
      return;
    }

    // Prefill visible fields with server profile
    prefillFromMe(me);

    // If profile is not complete, open the profile modal and keep Start disabled
    if (!me.profile_complete) {
      show(loginModal, false);
      show(profileModal, true);
      setStartEnabled(false);
      return;
    }

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
  } catch (e) {
    console.warn('[auth_gate] evaluateAuth failed:', e);
    setStartEnabled(false);
  } finally {
    evalLock = false;
  }
}

// ---------- Wire once ----------
let wired = false;

export function wireAuthGate() {
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
        const email = ($('#inlineLoginEmail') && $('#inlineLoginEmail').value || '').trim();
        if (!email) return;
        await postJSON('/api/v1/auth/login', { email });
        await evaluateAuth();
      } catch (err) {
        console.warn('[auth_gate] login failed:', err);
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
        window.dispatchEvent(new CustomEvent('profile_saved', { detail: { complete: !!(name && title) } }));
        await evaluateAuth();
      } catch (err) {
        console.warn('[auth_gate] profile save failed:', err);
      }
    });
  }

  // Kick once on DOM ready (guarded)
  if (document.readyState !== 'loading') evaluateAuth();
  else document.addEventListener('DOMContentLoaded', evaluateAuth);
}

// Auto-boot
try { wireAuthGate(); } catch (e) { console.warn('[auth_gate] wire failed:', e); }
