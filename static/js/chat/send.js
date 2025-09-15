import { ensureCSRF } from '../csrf.js';

export async function sendText(text, extra={} ){
  const headers = new Headers({ 'Content-Type':'application/json' });
  const csrf = await ensureCSRF().catch(()=>'');
  if (csrf) headers.set('X-CSRF-Token', csrf);
  const idem = (crypto.randomUUID?.() ?? (Date.now()+'-'+Math.random()));
  headers.set('Idempotency-Key', String(idem));
  const sid = localStorage.getItem('chip.sid') || '';
  const body = JSON.stringify({ text, session_id: sid, ...extra });
  return fetch('/api/v1/chat', { method:'POST', headers, body, credentials:'include' });
}
