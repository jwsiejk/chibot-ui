// static/js/chat/send.js — helper for typed chat
import { ensureCSRF } from '../csrf.js';
import { getSID } from '../util/sid.js';

export async function sendText(text, extra={}){
  const headers = new Headers({ 'Content-Type':'application/json' });
  const csrf = await ensureCSRF().catch(()=> '');
  if (csrf) headers.set('X-CSRF-Token', csrf);
  headers.set('Idempotency-Key', String(crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random())));
  const body = JSON.stringify({ text, session_id: getSID(), ...extra });
  return fetch('/api/v1/chat', { method:'POST', headers, body, credentials:'include' });
}
