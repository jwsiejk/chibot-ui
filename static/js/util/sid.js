// static/js/util/sid.js — single source of truth for session id
export function getSID(){
  const k = 'chip.sid';
  let s = localStorage.getItem(k);
  if (!s) {
    s = (crypto.randomUUID?.() ?? (Date.now() + '-' + Math.random()));
    localStorage.setItem(k, s);
  }
  return s;
}
