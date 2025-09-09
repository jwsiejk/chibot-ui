// static/js/auth_gate.js
// Auth + Profile gating extracted from app.js to keep app.js lean.
// Responsibilities:
//  - Fetch /api/v1/auth/me
//  - Control login & profile modals
//  - Prefill profile form (email readonly + populated from login)
//  - Enable Start button when authenticated & profile_complete
//  - Emit "ac:auth-ready" when ready

export async function getMe(){
  try{
    const r = await fetch('/api/v1/auth/me', { credentials: 'include' });
    return await r.json();
  }catch(_){ return { authenticated:false }; }
}

function el(id){ return document.getElementById(id); }

export function showLoginModal(on){
  const m = el('loginModal'), p = el('profileModal');
  if (m) m.hidden = !on;
  if (on && p) p.hidden = true;
  if (on){ const e = el('inlineLoginEmail'); if (e) setTimeout(()=>e.focus(), 0); }
}

export function showProfileModal(on){
  const p = el('profileModal'), m = el('loginModal');
  if (p) p.hidden = !on;
  if (on && m) m.hidden = true;
  if (on){ const e = el('prof_name'); if (e) setTimeout(()=>e.focus(), 0); }
}

export async function prefillProfile(){
  try{
    const me = await getMe();
    const prof = (me && me.profile) || {};
    if (me && me.email) prof.email = me.email;
    const map = {email:'prof_email', name:'prof_name', title:'prof_title', region:'prof_region', company:'prof_company'};
    for (const k in map){
      const x = el(map[k]); if (!x) continue;
      if (k === 'email'){
        x.readOnly = true;
        if (prof[k]) x.value = prof[k];
      } else {
        if (prof[k] != null) x.value = prof[k];
      }
    }
  }catch(_){}
}

export async function refreshGating(){
  try{
    const me = await getMe();
    const authed = !!(me && me.authenticated);
    const incomplete = authed && me.profile_complete === false;
    const banner = el('profileGateBanner');
    const startBtn = document.querySelector('[data-role="start-btn"], #startButton, #start');

    if (!authed){
      if (startBtn){ startBtn.disabled = true; startBtn.title = 'Please sign in to continue'; }
      if (banner){ banner.hidden = true; }
      showLoginModal(true); showProfileModal(false);
      window.AC_AUTH_READY = false;
    } else if (incomplete){
      if (startBtn){ startBtn.disabled = true; startBtn.title = 'Please fill out your profile to continue'; }
      if (banner){ banner.hidden = false; }
      await prefillProfile();
      showProfileModal(true); showLoginModal(false);
      window.AC_AUTH_READY = false;
    } else {
      if (startBtn){ startBtn.disabled = false; startBtn.title = ''; }
      if (banner){ banner.hidden = true; }
      showLoginModal(false); showProfileModal(false);
      window.AC_AUTH_READY = true;
      window.dispatchEvent(new CustomEvent('ac:auth-ready'));
    }
  }catch(_){}
}

// Bind modal forms (login + profile).
// These use existing backend routes /api/v1/auth/login and /api/v1/profile/save (if present).
// If routes differ, only the gating still works.
export function initAuthGate(){
  // Login form (non-blocking UI)
  const loginForm = el('inlineLoginForm');
  if (loginForm){
    loginForm.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      const email = (el('inlineLoginEmail')||{}).value;
      const msg = el('inlineLoginMsg');
      try{
        const r = await fetch('/api/v1/auth/login', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          credentials:'include',
          body: JSON.stringify({email})
        });
        if (r.ok){
          if (msg) msg.textContent = 'Logged in. Checking profile…';
          await refreshGating();
        } else {
          if (msg) msg.textContent = 'Login failed';
        }
      }catch(_){
        if (msg) msg.textContent = 'Login failed';
      }
    });
    const cancel = el('inlineLoginCancel');
    if (cancel) cancel.addEventListener('click', ()=> showLoginModal(false));
  }

  // Profile form
  const profForm = el('inlineProfileForm');
  if (profForm){
    profForm.addEventListener('submit', async (ev)=>{
      ev.preventDefault();
      const data = {
        email: (el('prof_email')||{}).value,
        name: (el('prof_name')||{}).value,
        title: (el('prof_title')||{}).value,
        region:(el('prof_region')||{}).value,
        company:(el('prof_company')||{}).value
      };
      const msg = el('inlineProfileMsg');
      try{
        const r = await fetch('/api/v1/profile/save', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          credentials:'include',
          body: JSON.stringify(data)
        });
        if (r.ok){
          if (msg) msg.textContent = 'Saved. You can start now.';
          showProfileModal(false);
          await refreshGating();
        } else {
          if (msg) msg.textContent = 'Save failed.';
        }
      }catch(_){
        if (msg) msg.textContent = 'Save failed.';
      }
    });
  }

  // First check on load
  refreshGating();
}
