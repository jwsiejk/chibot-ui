/* static/js/app.js  — Auth/Profile state + gating (deterministic, race-proof) */

function $(sel){ return document.querySelector(sel); }
function el(id){ return document.getElementById(id); }

/* --- CSRF (idempotent) --- */
window.__csrfToken = null;
async function ensureCSRF(){
  if (window.__csrfToken) return window.__csrfToken;
  try{
    const r = await fetch('/api/v1/csrf', { credentials:'include' });
    const t = r.headers.get('X-CSRF-Token');
    if (t) window.__csrfToken = t;
    return window.__csrfToken;
  }catch(_){ return null; }
}

/* --- Modal toggles (mutually exclusive, always hide the other) --- */
function showLogin(on){
  const m = el('loginModal'), p = el('profileModal');
  if (m) m.hidden = !on;
  if (on && p) p.hidden = true;
  if (on){ const e = el('inlineLoginEmail'); if (e) setTimeout(()=>e.focus(), 0); }
}
function showProfile(on){
  const p = el('profileModal'), m = el('loginModal');
  if (p) p.hidden = !on;
  if (on && m) m.hidden = true;
  if (on){ const e = el('prof_name'); if (e) setTimeout(()=>e.focus(), 0); }
}

/* --- Profile prefill --- */
async function prefillProfile(){
  try{
    const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
    const prof = (me && me.profile) || {};
    if (me && me.email) prof.email = me.email;
    const map = { email:'prof_email', name:'prof_name', role:'prof_role', region:'prof_region', company:'prof_company' };
    for (const k in map){ const x = el(map[k]); if (x && prof[k] != null) x.value = prof[k]; }
  }catch(_){}
}

/* --- Gating banner + Start state --- */
async function refreshGating(){
  try{
    const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
    const startBtn = el('startButton');
    const banner   = el('profileGateBanner');
    const authed   = !!(me && me.authenticated);
    const needs    = authed && me.profile_complete === false;

    if (!authed){
      if (startBtn){ startBtn.disabled = true; startBtn.title = 'Please sign in to continue'; }
      if (banner){ banner.hidden = true; }
      window.AC_AUTH_READY = false;
    } else if (needs){
      if (startBtn){ startBtn.disabled = true; startBtn.title = 'Please fill out your profile to continue'; }
      if (banner){ banner.hidden = false; }
      window.AC_AUTH_READY = false;
    } else {
      if (startBtn){ startBtn.disabled = false; startBtn.title = ''; }
      if (banner){ banner.hidden = true; }
      window.AC_AUTH_READY = true;
      window.dispatchEvent(new CustomEvent('ac:auth-ready'));
    }
  }catch(_){}
}

/* --- Deterministic state machine --- */
const STATE = { status: 'init', evalNonce: 0, authInFlight: false };

async function evaluateAuth(){
  const myNonce = ++STATE.evalNonce;
  try{
    const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
    if (myNonce !== STATE.evalNonce) return; // a newer evaluation superseded this one

    const authed = !!(me && me.authenticated);
    if (!authed){
      STATE.status = 'unauth';
      showLogin(true);  showProfile(false);
    } else if (me.profile_complete === false){
      STATE.status = 'needs_profile';
      await prefillProfile();
      showProfile(true); showLogin(false);
    } else {
      STATE.status = 'ready';
      showLogin(false); showProfile(false);
    }
    await refreshGating();
  }catch(_){
    if (myNonce !== STATE.evalNonce) return;
    STATE.status = 'unauth';
    showLogin(true); showProfile(false);
  }
}

/* --- Wire UI once DOM is ready --- */
document.addEventListener('DOMContentLoaded', async ()=>{
  await ensureCSRF();
  await evaluateAuth();

  /* Login form */
  const f  = el('inlineLoginForm');
  const msg= el('inlineLoginMsg');
  const btn= el('inlineLoginSubmit');
  const can= el('inlineLoginCancel');
  const email = el('inlineLoginEmail');

  if (can) can.addEventListener('click', ()=> showLogin(false));
  if (f){
    f.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const v = (email?.value || '').trim();
      if (!v){ msg.textContent = 'Please enter a valid email.'; return; }
      btn.disabled = true; msg.textContent = 'Signing in…';
      STATE.authInFlight = true;
      showLogin(false); // hide immediately for UX

      try{
        const tok = await ensureCSRF();
        const r = await fetch('/api/v1/auth/login', {
          method:'POST', credentials:'include',
          headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
          body: JSON.stringify({ email: v })
        });
        const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(x=>x.json()).catch(()=>null);
        if (r.ok && me && me.authenticated){
          if (me.profile_complete === false){
            STATE.status = 'needs_profile';
            await prefillProfile();
            showProfile(true);
          } else {
            STATE.status = 'ready';
            showProfile(false);
          }
          await refreshGating();
        } else {
          STATE.status = 'unauth';
          msg.textContent = 'Login failed. Please try again.';
          showLogin(true); btn.disabled = false;
        }
      }catch(_){
        STATE.status = 'unauth';
        msg.textContent = 'Network error. Please try again.';
        showLogin(true); btn.disabled = false;
      }finally{
        STATE.authInFlight = false;
      }
    });
  }

  /* Profile form */
  const pf   = el('inlineProfileForm');
  const pmsg = el('inlineProfileMsg');
  const pbtn = el('inlineProfileSubmit');
  const pcan = el('inlineProfileCancel');
  if (pcan) pcan.addEventListener('click', ()=> showProfile(false));
  if (pf){
    pf.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const data = {
        email:  (el('prof_email')?.value||'').trim(),
        name:   (el('prof_name')?.value||'').trim(),
        role:   (el('prof_role')?.value||'').trim(),
        region: (el('prof_region')?.value||'').trim(),
        company:(el('prof_company')?.value||'').trim(),
        completed: true
      };
      if (!data.name || !data.role || !data.region){
        pmsg.textContent = 'Please complete name, title, and region.'; return;
      }
      pbtn.disabled = true; pmsg.textContent = 'Saving…';
      try{
        const tok = await ensureCSRF();
        const r = await fetch('/api/v1/auth/profile/save', {
          method:'POST', credentials:'include',
          headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
          body: JSON.stringify(data)
        });
        if (r.ok){
          STATE.status = 'ready';
          showProfile(false);
          await refreshGating();
        } else {
          pmsg.textContent = 'Save failed.';
        }
      }catch(_){
        pmsg.textContent = 'Network error.';
      }finally{
        pbtn.disabled = false;
      }
    });
  }
});
