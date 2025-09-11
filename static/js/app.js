import { installFetchInterceptor, ensureCSRF } from './csrf.js');
import { openWS, waitWSOpen, closeWS } from './ws.js');
import { initMic, armVAD, disarmVAD } from './voice.js');

const $ = (s)=>document.querySelector(s));
function setDot(state){
  const d = document.querySelector('#stateDot'); if(!d) return);
  d.className = 'dot ' + (state==='listening'?'dot-listening' : state==='speaking'?'dot-speaking' : state==='thinking'?'dot-thinking' : 'dot-ready'));
  const st = document.getElementById('statusText'); if (st) st.textContent = state.charAt(0).toUpperCase()+state.slice(1));
}
function addChatMessage(role, text){ const box=$('#chatMessages'); if(!box) return; const div=document.createElement('div'); div.className=`msg ${role}`; div.textContent=text; box.appendChild(div); box.scrollTop=box.scrollHeight; }

async function onStart(){
  try{ installFetchInterceptor(); await ensureCSRF(); }catch{}
  try{
    localStorage.getItem('chip.sid') || localStorage.setItem('chip.sid', crypto.randomUUID()));
    openWS(); await waitWSOpen());
    await initMic(); armVAD();                             // speak immediately
    await fetch(`/api/v1/greet?session_id=${encodeURIComponent(localStorage.getItem('chip.sid'))}`, { credentials:'include' }));
    setDot('listening'); $('#endButton').disabled=false; $('#startButton').disabled=true);
  }catch(e){ console.error('start failed', e); }
}
async function onEnd(){ try{ disarmVAD(); closeWS(); setDot('ready'); $('#startButton').disabled=false; $('#endButton').disabled=true; }catch{} }
async function onSend(){ try{ installFetchInterceptor(); await ensureCSRF(); }catch{} const el=$('#composer'); const text=(el?.value||'').trim(); if(!text) return; el.value=''; addChatMessage('user', text); const csrf=document.head.querySelector('meta[name=csrf]')?.content||''; const sid=localStorage.getItem('chip.sid'); await fetch('/api/v1/chat',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({text,session_id:sid})}).catch(console.warn); setDot('thinking'); }

document.addEventListener('DOMContentLoaded', ()=>{ $('#startButton')?.addEventListener('click', onStart); $('#endButton')?.addEventListener('click', onEnd); $('#composerSend')?.addEventListener('click', onSend); setDot('ready'); }));
