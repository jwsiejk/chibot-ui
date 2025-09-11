/* static/js/app.js — unified, deterministic auth/profile gating + your existing UI glue
   - Single CSRF getter (GET /api/v1/csrf)
   - Exactly one modal at a time (login XOR profile)
   - Login modal hides immediately on submit; re-opens only on real failure
   - Start is disabled until authenticated && profile_complete
   - Does NOT autoconnect WS — that happens from ws.js or Start button by design
*/

import { API } from "./config.js";
import { initAuthGate, refreshGating, showLoginModal, showProfileModal, prefillProfile } from './auth_gate.js';

import { STATES, setState, getState } from "./state.js";
import { openWS, waitWSOpen, closeWS, bindControls, isOpen, cancelNudge } from "./ws.js";
import { showError, hideError } from "./errors.js";
import { renderSuggestions } from "./suggestions.js";
import { ensureCSRF, installFetchInterceptor } from "./csrf.js";
import { playStream, stopPlayback, setVisemeCallback, isPlaying } from "./audio.js";
import { armVAD, disarmVAD, initMic } from "./voice.js";
import { getSID } from "./util/sid.js";

const $ = (s) => document.querySelector(s);
// Track per-session greet to avoid double-greet
const __greetedSIDs = new Set();

/* -------------------------------------------------------
   CSRF (canonical, idempotent)
------------------------------------------------------- */
window.__csrfToken = null;

/* -------------------------------------------------------
   UI refs and basic state
------------------------------------------------------- */
let startBtn, endBtn, sendBtn, composer, stateLabelEl, stateDotsWrap;

function onViseme(v){ /* mouth anim hook (optional) */ }

document.addEventListener("DOMContentLoaded", async () => {
  startBtn = $("#startButton");
  endBtn   = $("#endButton");
  sendBtn  = $("#composerSend");
  composer = $("#composerInput");
  stateLabelEl = $("#stateLabel");
  stateDotsWrap = $("#stateDots");

  bindControls(startBtn, endBtn);
  setVisemeCallback(onViseme);

  wireUI();
  renderSuggestions(["Show roadmap", "Explain Portworx", "Demo FlashArray", "Open Admin"], onSuggestion);

  // prefetch CSRF so first POSTs don't 403
  await ensureCSRF();

  // initial state in the stage dots
  setState(STATES.READY);
  updateStateIndicators(STATES.READY);

  // deterministic auth/profile evaluation
  await evaluateAuth();
});

/* -------------------------------------------------------
   Deterministic auth/profile controller
------------------------------------------------------- */
function el(id){ return document.getElementById(id); }
// showLoginModal handled by auth_gate.js
// showProfileModal handled by auth_gate.js
// prefillProfile handled by auth_gate.js
// refreshGating handled by auth_gate.js
const STATE = { status:'init', evalNonce:0, authInFlight:false };

async function evaluateAuth(){
  const myNonce = ++STATE.evalNonce;
  try{
    const me = await fetch('/api/v1/auth/me', {credentials:'include'}).then(r=>r.json());
    if (myNonce !== STATE.evalNonce) return; // superseded
    const authed = !!(me && me.authenticated);

    if (!authed){
      STATE.status = 'unauth';
      showLoginModal(true);  showProfileModal(false);
    } else if (me.profile_complete === false){
      STATE.status = 'needs_profile';
      await prefillProfile();
      showProfileModal(true); showLoginModal(false);
    } else {
      STATE.status = 'ready';
      showLoginModal(false); showProfileModal(false);
    }
    await refreshGating();
    wireAuthFormsOnce(); // idempotent
  }catch(_){
    if (myNonce !== STATE.evalNonce) return;
    STATE.status = 'unauth';
    showLoginModal(true); showProfileModal(false);
    wireAuthFormsOnce(); // still wire handlers so login works
  }
}

/* One-time wiring for login/profile forms */
let _formsWired = false;
async function wireAuthFormsOnce(){
  if (_formsWired) return;
  _formsWired = true;

  // LOGIN
  const f  = el('inlineLoginForm');
  const msg= el('inlineLoginMsg');
  const btn= el('inlineLoginSubmit');
  const can= el('inlineLoginCancel');
  const email = el('inlineLoginEmail');
  if (can) can.addEventListener('click', ()=> showLoginModal(false));
  if (f){
    f.addEventListener('submit', async (e)=>{
      e.preventDefault();
      const v = (email?.value || '').trim();
      if (!v){ if(msg) msg.textContent='Please enter a valid email.'; return; }
      if (btn) btn.disabled = true;
      if (msg) msg.textContent = 'Signing in…';
      STATE.authInFlight = true;
      showLoginModal(false); // hide immediately

      try{
        const tok = await ensureCSRF();
        const r = await fetch('/api/v1/auth/login', {
          method:'POST', credentials:'include',
          headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
          body: JSON.stringify({ email: v })
        });
        const me = await fetch('/api/v1/auth/me', {credentials:'include'}).then(x=>x.json()).catch(()=>null);
        if (r.ok && me && me.authenticated){
          if (me.profile_complete === false){
            STATE.status='needs_profile';
            await prefillProfile();
            showProfileModal(true);
          } else {
            STATE.status='ready';
            showProfileModal(false);
          }
          await refreshGating();
        } else {
          STATE.status='unauth';
          if (msg) msg.textContent='Login failed. Please try again.';
          showLoginModal(true);
          if (btn) btn.disabled=false;
        }
      }catch(_){
        STATE.status='unauth';
        if (msg) msg.textContent='Network error. Please try again.';
        showLoginModal(true);
        if (btn) btn.disabled=false;
      }finally{
        STATE.authInFlight = false;
      }
    });
  }

  // PROFILE
  const pf   = el('inlineProfileForm');
  const pmsg = el('inlineProfileMsg');
  const pbtn = el('inlineProfileSubmit');
  const pcan = el('inlineProfileCancel');
  if (pcan) pcan.addEventListener('click', ()=> showProfileModal(false));
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
        if (pmsg) pmsg.textContent='Please complete name, title, and region.'; return;
      }
      if (pbtn) pbtn.disabled = true;
      if (pmsg) pmsg.textContent='Saving…';
      try{
        const tok = await ensureCSRF();
        const r = await fetch('/api/v1/auth/profile/save', {
          method:'POST', credentials:'include',
          headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
          body: JSON.stringify(data)
        });
        if (r.ok){
          STATE.status='ready';
          showProfileModal(false);
          await refreshGating();
        } else {
          if (pmsg) pmsg.textContent='Save failed.';
        }
      }catch(_){
        if (pmsg) pmsg.textContent='Network error.';
      }finally{
        if (pbtn) pbtn.disabled=false;
      }
    });
  }
}

/* -------------------------------------------------------
   Stage/toolbar/chat glue (your existing behaviors)
------------------------------------------------------- */
function wireUI(){
  startBtn?.addEventListener("click", onStart);
  endBtn?.addEventListener("click", onEnd);
  sendBtn?.addEventListener("click", onSend);
  composer?.addEventListener("keydown", (e)=>{ if (e.key === "Enter") onSend(); });

  // Soft barge-in: pause on first VAD hit; confirm ~420ms; commit interrupt
  document.addEventListener("vad-hit", () => {
    if (getState() === STATES.RESPONDING && isPlaying()){
      stopPlayback();
      sendInterrupt();
      setTimeout(() => setState(STATES.LISTENING), 420);
    }
  });
}

function updateStateIndicators(s){
  const label = ({
    [STATES.READY]: "Ready",
    [STATES.LISTENING]: "Listening",
    [STATES.RESPONDING]: "Responding"
  })[s] || "Ready";
  if (stateLabelEl) stateLabelEl.textContent = label;
}
function updateStateIndicatorsOnce(s){ updateStateIndicators(s); }

function onSuggestion(text){
  if (composer) composer.value = text;
  onSend();
}

async function greet(){
  const sid = getSID();
  if (__greetedSIDs.has(sid)) return;
  const r = await fetch(`${API.GREET}?session_id=${encodeURIComponent(sid)}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  __greetedSIDs.add(sid);
}

/* -------------------------------------------------------
   Start/End logic — guarded by AC_AUTH_READY
------------------------------------------------------- */
async function onStart(){
  hideError();
  if (!window.AC_AUTH_READY){
    showError("auth", "blocked", "Please complete login/profile first");
    return;
  }
  try{
    openWS();                 // opens /ws/v1/chat (no autoconnect on load)
    await waitWSOpen();       // ensure server subscription is ready
    await initMic().catch(()=>{ showError("mic","blocked","Microphone permission denied"); });
    await greet();            // GET /api/v1/greet?session_id=SID
    setState(STATES.LISTENING);
    document.body.classList.add("chat-open");
  }catch(e){
    showError(API.GREET, e.status || "ERR", e.message || "start failed");
  }
}
async function onEnd(){
  try{
    closeWS();
    setState(STATES.READY);
    document.body.classList.remove("chat-open");
  }catch(e){ /* noop */ }
}

/* -------------------------------------------------------
   Text send
------------------------------------------------------- */
async function onSend(){
  try{ if (!isOpen()) { openWS(); await waitWSOpen(); } }catch{}
  try { const ghost = (composer?.value || '').trim(); if(ghost) { try{ addChatMessage('user', ghost); }catch(e){} } } catch(e) {}
  cancelNudge();
  const text = (composer?.value || "").trim();
  if (!text) return;
  if (composer) composer.value = "";
  try{
    const tok = await ensureCSRF();
    const r = await fetch(API.CHAT, {
      method: "POST",
      headers: { "Content-Type":"application/json", ...(tok ? {"X-CSRF-Token":tok} : {}) },
      credentials: "include",
      body: JSON.stringify({ text, session_id: getSID() })
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  __greetedSIDs.add(sid);
  }catch(e){
    showError(API.CHAT, e.status || "ERR", e.message || "send failed");
  }
}

/* -------------------------------------------------------
   Minimal chat UI helper
------------------------------------------------------- */
function addChatMessage(role, text){
  try{
    const box = document.getElementById('chatMessages');
    if(!box || !text) return;
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }catch(e){}
}


/* === APPEND-ONLY: Auth/Profile Gate (deterministic, non-destructive) ========
   - Ensures only one modal is visible at a time.
   - Login hides immediately; re-opens only on real failure.
   - Profile save cannot hang (CSRF refresh + timeout + explicit errors).
   - Sets window.AC_AUTH_READY when authenticated && profile_complete.
   - Does NOT autoconnect WebSocket.
=============================================================================*/
(() => {
  try{ installFetchInterceptor(); }catch{}
  try{ ensureCSRF(); }catch{}
  const $  = (sel)=> document.querySelector(sel);
  const el = (id)=> document.getElementById(id);


  function showLoginModal(on){
    const m = el('loginModal'), p = el('profileModal');
    if (m) m.hidden = !on;
    if (on && p) p.hidden = true;
    if (on){ const e = el('inlineLoginEmail'); if (e) setTimeout(()=>e.focus(), 0); }
  }
  function showProfileModal(on){
    const p = el('profileModal'), m = el('loginModal');
    if (p) p.hidden = !on;
    if (on && m) m.hidden = true;
    if (on){ const e = el('prof_name'); if (e) setTimeout(()=>e.focus(), 0); }
  }

  async function prefillProfile(){
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      const prof = (me && me.profile) || {};
      if (me && me.email) prof.email = me.email;
      const map = { email:'prof_email', name:'prof_name', role:'prof_role', region:'prof_region', company:'prof_company' };
      for (const k in map){ const x = el(map[k]); if (x && prof[k] != null) x.value = prof[k]; }
    }catch(_){}
  }

  async function refreshGating(){
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      const authed = !!(me && me.authenticated);
      const needs  = authed && me.profile_complete === false;
      const startBtn = el('startButton');
      const banner   = el('profileGateBanner');
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

  const STATE = { status:'init', evalNonce:0, authInFlight:false };

  async function evaluateAuth(){
    const myNonce = ++STATE.evalNonce;
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      if (myNonce !== STATE.evalNonce) return;
      const authed = !!(me && me.authenticated);
      if (!authed){
        STATE.status='unauth'; showLoginModal(true);  showProfileModal(false);
      } else if (me.profile_complete === false){
        STATE.status='needs_profile'; await prefillProfile(); showProfileModal(true); showLoginModal(false);
      } else {
        STATE.status='ready'; showLoginModal(false); showProfileModal(false);
      }
      await refreshGating();
      wireFormsOnce();
    }catch(_){
      if (myNonce !== STATE.evalNonce) return;
      STATE.status='unauth'; showLoginModal(true); showProfileModal(false); wireFormsOnce();
    }
  }

  function replaceNodeKeepValue(node){
    if (!node) return null;
    const clone = node.cloneNode(true);
    if (node.value != null) clone.value = node.value;
    node.parentNode.replaceChild(clone, node);
    return clone;
  }

  let _wired = false;
  async function wireFormsOnce(){
    if (_wired) return; _wired = true;

    // LOGIN
    let f  = el('inlineLoginForm');
    let msg= el('inlineLoginMsg');
    let btn= el('inlineLoginSubmit');
    let can= el('inlineLoginCancel');
    let email = el('inlineLoginEmail');
    if (f)   f = replaceNodeKeepValue(f);
    if (msg) msg = el('inlineLoginMsg');
    if (btn) btn = el('inlineLoginSubmit');
    if (can) can = el('inlineLoginCancel');
    if (email) email = el('inlineLoginEmail');

    if (can) can.addEventListener('click', ()=> showLoginModal(false));
    if (f){
      f.addEventListener('submit', async (e)=>{
        e.preventDefault();
        const v = (email?.value || '').trim();
        if (!v){ if (msg) msg.textContent='Please enter a valid email.'; return; }
        if (btn) btn.disabled = true;
        if (msg) msg.textContent='Signing in…';
        STATE.authInFlight = true;
        showLoginModal(false);
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
              STATE.status='needs_profile'; await prefillProfile(); showProfileModal(true);
            } else {
              STATE.status='ready'; showProfileModal(false);
            }
            await refreshGating();
          } else {
            STATE.status='unauth'; if (msg) msg.textContent='Login failed. Please try again.'; showLoginModal(true); if (btn) btn.disabled=false;
          }
        }catch(_){
          STATE.status='unauth'; if (msg) msg.textContent='Network error. Please try again.'; showLoginModal(true); if (btn) btn.disabled=false;
        }finally{
          STATE.authInFlight = false;
        }
      });
    }

    // PROFILE
    let pf   = el('inlineProfileForm');
    let pmsg = el('inlineProfileMsg');
    let pbtn = el('inlineProfileSubmit');
    let pcan = el('inlineProfileCancel');
    if (pf)   pf = replaceNodeKeepValue(pf);
    if (pmsg) pmsg = el('inlineProfileMsg');
    if (pbtn) pbtn = el('inlineProfileSubmit');
    if (pcan) pcan = el('inlineProfileCancel');

    if (pcan) pcan.addEventListener('click', ()=> showProfileModal(false));
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
          if (pmsg) pmsg.textContent='Please complete name, title, and region.'; return;
        }

        async function postJSON(url, body, timeoutMs=12000){
          const ac = new AbortController();
          const to = setTimeout(()=>ac.abort(), timeoutMs);
          try{
            const tok = await ensureCSRF(true);
            const r = await fetch(url, {
              method:'POST', credentials:'include',
              headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
              body: JSON.stringify(body),
              signal: ac.signal
            });
            return r;
          } finally { clearTimeout(to); }
        }

        if (pbtn) pbtn.disabled=true;
        if (pmsg) pmsg.textContent='Saving…';
        try{
          const r = await postJSON('/api/v1/auth/profile/save', data);
          if (!r.ok){
            let msg = `Save failed (HTTP ${r.status})`;
            if (r.status === 403) msg = 'Save blocked by CSRF — refresh and try again.';
            if (r.status === 405) msg = 'Save not allowed — check route method.';
            if (pmsg) pmsg.textContent = msg;
            return;
          }
          if (pmsg) pmsg.textContent='Saved.';
          showProfileModal(false);
          await refreshGating();
          await evaluateAuth();
        }catch(err){
          if (pmsg) pmsg.textContent = (err?.name === 'AbortError')
            ? 'Save timed out. Please try again.'
            : 'Network error. Please try again.';
        }finally{
          if (pbtn) pbtn.disabled=false;
        }
      });
    }
  }

  document.addEventListener('DOMContentLoaded', async ()=>{
    await ensureCSRF();
    await evaluateAuth();
  });
})();


/* === APPEND-ONLY: Auth/Profile Gate (deterministic, non-destructive, v2) =====
   - Exactly one modal visible at a time (login XOR profile)
   - Login hides immediately; re-opens only on real failure
   - Profile save cannot hang (CSRF refresh + timeout + explicit errors)
   - Sets window.AC_AUTH_READY when authenticated && profile_complete
   - Does NOT autoconnect WebSocket
=============================================================================*/
(() => {
  try{ installFetchInterceptor(); }catch{}
  try{ ensureCSRF(); }catch{}
  const $  = (sel)=> document.querySelector(sel);
  const el = (id)=> document.getElementById(id);

    }

  function showLoginModal(on){
    const m = el('loginModal'), p = el('profileModal');
    if (m) m.hidden = !on;
    if (on && p) p.hidden = true;
    if (on){ const e = el('inlineLoginEmail'); if (e) setTimeout(()=>e.focus(), 0); }
  }
  function showProfileModal(on){
    const p = el('profileModal'), m = el('loginModal');
    if (p) p.hidden = !on;
    if (on && m) m.hidden = true;
    if (on){ const e = el('prof_name'); if (e) setTimeout(()=>e.focus(), 0); }
  }

  // Force-hide any stale modals when auth is ready
  function settleUIForReady(){
    showLoginModal(false);
    showProfileModal(false);
  }

  async function prefillProfile(){
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      const prof = (me && me.profile) || {};
      // Always set email from login and make it read-only
      const emailEl = el('prof_email');
      if (emailEl){
        emailEl.value = me?.email || prof.email || '';
        emailEl.readOnly = true;
        emailEl.setAttribute('readonly','');
      }
      const map = { name:'prof_name', role:'prof_role', region:'prof_region', company:'prof_company' };
      for (const k in map){
        const x = el(map[k]);
        if (x && (prof[k] != null)) x.value = prof[k];
      }
    }catch(_){}
  }

  async function refreshGating(){
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      const authed = !!(me && me.authenticated);
      const needs  = authed && me.profile_complete === false;
      const startBtn = el('startButton');
      const banner   = el('profileGateBanner');
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

  const STATE = { status:'init', evalNonce:0, authInFlight:false };

  async function evaluateAuth(){
    const myNonce = ++STATE.evalNonce;
    try{
      const me = await fetch('/api/v1/auth/me', { credentials:'include' }).then(r=>r.json());
      if (myNonce !== STATE.evalNonce) return;
      const authed = !!(me && me.authenticated);
      if (!authed){
        STATE.status='unauth'; showLoginModal(true);  showProfileModal(false);
      } else if (me.profile_complete === false){
        STATE.status='needs_profile'; await prefillProfile(); showProfileModal(true); showLoginModal(false);
      } else {
        STATE.status='ready'; settleUIForReady();
      }
      await refreshGating();
      wireFormsOnce();
    }catch(_){
      if (myNonce !== STATE.evalNonce) return;
      STATE.status='unauth'; showLoginModal(true); showProfileModal(false); wireFormsOnce();
    }
  }

  function replaceNodeKeepValue(node){
    if (!node) return null;
    const clone = node.cloneNode(true);
    if (node.value != null) clone.value = node.value;
    node.parentNode.replaceChild(clone, node);
    return clone;
  }

  let _wired = false;
  async function wireFormsOnce(){
    if (_wired) return; _wired = true;

    // LOGIN
    let f  = el('inlineLoginForm');
    let msg= el('inlineLoginMsg');
    let btn= el('inlineLoginSubmit');
    let can= el('inlineLoginCancel');
    let email = el('inlineLoginEmail');
    if (f)   f = replaceNodeKeepValue(f);
    if (msg) msg = el('inlineLoginMsg');
    if (btn) btn = el('inlineLoginSubmit');
    if (can) can = el('inlineLoginCancel');
    if (email) email = el('inlineLoginEmail');

    if (can) can.addEventListener('click', ()=> showLoginModal(false));
    if (f){
      f.addEventListener('submit', async (e)=>{
        e.preventDefault();
        const v = (email?.value || '').trim();
        if (!v){ if (msg) msg.textContent='Please enter a valid email.'; return; }
        if (btn) btn.disabled = true;
        if (msg) msg.textContent='Signing in…';
        STATE.authInFlight = true;
        showLoginModal(false);
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
              STATE.status='needs_profile'; await prefillProfile(); showProfileModal(true);
            } else {
              STATE.status='ready'; showProfileModal(false);
            }
            await refreshGating();
          } else {
            STATE.status='unauth'; if (msg) msg.textContent='Login failed. Please try again.'; showLoginModal(true); if (btn) btn.disabled=false;
          }
        }catch(_){
          STATE.status='unauth'; if (msg) msg.textContent='Network error. Please try again.'; showLoginModal(true); if (btn) btn.disabled=false;
        }finally{
          STATE.authInFlight = false;
        }
      });
    }

    // PROFILE
    let pf   = el('inlineProfileForm');
    let pmsg = el('inlineProfileMsg');
    let pbtn = el('inlineProfileSubmit');
    let pcan = el('inlineProfileCancel');
    if (pf)   pf = replaceNodeKeepValue(pf);
    if (pmsg) pmsg = el('inlineProfileMsg');
    if (pbtn) pbtn = el('inlineProfileSubmit');
    if (pcan) pcan = el('inlineProfileCancel');

    if (pcan) pcan.addEventListener('click', ()=> showProfileModal(false));
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
          if (pmsg) pmsg.textContent='Please complete name, title, and region.'; return;
        }

        async function postJSON(url, body, timeoutMs=12000){
          const ac = new AbortController();
          const to = setTimeout(()=>ac.abort(), timeoutMs);
          try{
            const tok = await ensureCSRF(true);
            const r = await fetch(url, {
              method:'POST', credentials:'include',
              headers:{ 'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{}) },
              body: JSON.stringify(body),
              signal: ac.signal
            });
            return r;
          } finally { clearTimeout(to); }
        }

        if (pbtn) pbtn.disabled=true;
        if (pmsg) pmsg.textContent='Saving…';
        try{
          const r = await postJSON('/api/v1/auth/profile/save', data);
          if (!r.ok){
            let msg = `Save failed (HTTP ${r.status})`;
            if (r.status === 403) msg = 'Save blocked by CSRF — refresh and try again.';
            if (r.status === 405) msg = 'Save not allowed — check route method.';
            if (pmsg) pmsg.textContent = msg;
            return;
          }
          if (pmsg) pmsg.textContent='Saved.';
          showProfileModal(false);
          await refreshGating();
          await evaluateAuth();
        }catch(err){
          if (pmsg) pmsg.textContent = (err?.name === 'AbortError')
            ? 'Save timed out. Please try again.'
            : 'Network error. Please try again.';
        }finally{
          if (pbtn) pbtn.disabled=false;
        }
      });
    }
  }

  document.addEventListener('DOMContentLoaded', async ()=>{
    await ensureCSRF();
    await evaluateAuth();
  });

  // Belt-and-suspenders: when the app broadcasts auth-ready, hide any stale modals.
  window.addEventListener('ac:auth-ready', settleUIForReady);

  // Safety: greet is invoked by Start flow; no auto-greet on ws-ready.

})();