
import { ensureCSRF } from './csrf.js';

const $ = (s)=>document.querySelector(s);
const el = (id)=>document.getElementById(id);

function setStartEnabled(on){
  const btn = el('startButton');
  if (!btn) return;
  btn.disabled = !on;
}

function showLogin(on){
  const m = el('loginModal');
  if (!m) return;
  m.classList.toggle('hidden', !on);
  if (on) setTimeout(()=>el('inlineLoginEmail')?.focus(), 0);
}

function showProfile(on){
  const m = el('profileModal');
  if (!m) return;
  m.classList.toggle('hidden', !on);
}

export async function prefillProfile(){
  try{
    const r = await fetch('/api/v1/profile', {credentials:'include'});
    if(!r.ok) return;
    const j = await r.json();
    const prof = j && j.profile || {};
    // Map UI fields
    const map = { name:'prof_name', email:'prof_email', title:'prof_role', region:'prof_region' };
    for(const k of Object.keys(map)){
      const x = el(map[k]);
      if(x && prof[k] != null){
        x.value = prof[k];
      }
    }
  }catch(_){ /* ignore */ }
}

export async function saveProfile(){
  try{
    await ensureCSRF();
    const payload = {
      name: el('prof_name')?.value?.trim() || '',
      email: el('prof_email')?.value?.trim() || '',
      title: el('prof_role')?.value?.trim() || '',
      region: el('prof_region')?.value?.trim() || ''
    };
    const r = await fetch('/api/v1/profile', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      credentials:'include',
      body: JSON.stringify(payload)
    });
    if(!r.ok){
      el('profileMsg').textContent = 'Save failed.';
      return false;
    }
    const j = await r.json();
    const complete = !!(j && j.profile && j.profile.profile_complete);
    if(complete){
      el('profileMsg').textContent = 'Saved.';
      setStartEnabled(true);
      showProfile(false);
    }else{
      el('profileMsg').textContent = 'Saved — please complete required fields.';
      setStartEnabled(false);
    }
    return complete;
  }catch(e){
    el('profileMsg').textContent = 'Save failed.';
    return false;
  }
}

async function login(email){
  await ensureCSRF();
  const r = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    credentials:'include',
    body: JSON.stringify({ email })
  }).catch(()=>null);
  return r && r.ok;
}

async function evaluate(){
  try{
    const r = await fetch('/api/v1/auth/me', {credentials:'include'});
    const j = await r.json();
    const authed = !!(j && j.authenticated);
    const complete = !!(j && j.profile_complete);
    if(!authed){
      setStartEnabled(false);
      showProfile(false);
      showLogin(true);
      return;
    }
    // Authed:
    if(!complete){
      setStartEnabled(false);
      await prefillProfile();
      showLogin(false);
      showProfile(true);
    }else{
      setStartEnabled(true);
      showLogin(false);
      showProfile(false);
    }
  }catch(_){
    setStartEnabled(false);
    showLogin(true);
  }
}

function wire(){
  const form = el('inlineLoginForm');
  const emailInput = el('inlineLoginEmail');
  const cancel = el('inlineLoginCancel');
  const msg = el('inlineLoginMsg');
  if(cancel) cancel.onclick = ()=>showLogin(false);
  if(form && !form.__wired){
    form.__wired = true;
    form.onsubmit = async (e)=>{
      e.preventDefault();
      msg.textContent = '';
      const email = emailInput?.value?.trim().toLowerCase();
      if(!email){ msg.textContent = 'Email required.'; return; }
      const ok = await login(email);
      if(!ok){ msg.textContent = 'Login failed.'; return; }
      showLogin(false);
      await evaluate();
    };
  }
  const save = el('profileSave');
  if(save && !save.__wired){
    save.__wired = true;
    save.onclick = async ()=>{
      await saveProfile();
      await evaluate();
    };
  }
}

document.addEventListener('DOMContentLoaded', ()=>{
  wire();
  setTimeout(evaluate,0);
});

export { evaluate as evaluateAuth };
