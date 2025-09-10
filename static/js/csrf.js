// static/js/csrf.js — single source of truth for CSRF
let _token = null;
let _inflight = null;

export async function ensureCSRF(force=false){
  if (!force && _token) return _token;
  if (!force && _inflight) return _inflight;
  _inflight = (async () => {
    try{
      const r = await fetch('/api/v1/csrf', { credentials: 'include' });
      if (!r.ok) throw new Error('csrf_fetch_failed:' + r.status);
      const t = r.headers.get('X-CSRF-Token');
      if (!t) throw new Error('csrf_missing_header');
      _token = t;
      // publish to meta for non-module code that reads it
      let meta = document.head.querySelector('meta[name=csrf]');
      if (!meta){
        meta = document.createElement('meta');
        meta.name = 'csrf';
        document.head.appendChild(meta);
      }
      meta.content = _token;
      return _token;
    } finally {
      _inflight = null;
    }
  })();
  return _inflight;
}

export async function csrfHeader(){
  const t = await ensureCSRF().catch(()=>null);
  return t ? { 'X-CSRF-Token': t } : {};
}

// Wrap fetch to auto-inject CSRF for same-origin mutating requests, with one retry on 403
export function installFetchInterceptor(){
  const orig = window.fetch;
  window.fetch = async function(input, init = {}){
    try{
      const url = (typeof input === 'string') ? input : (input?.url || '');
      const sameOrigin = !/^https?:\/\//i.test(url) || url.startsWith(location.origin);
      const method = (init?.method || 'GET').toUpperCase();
      const mutating = sameOrigin && ['POST','PUT','PATCH','DELETE'].includes(method);
      if (mutating){
        const hdrs = new Headers(init.headers || {});
        if (!hdrs.get('X-CSRF-Token')){
          const h = await csrfHeader();
          if (h['X-CSRF-Token']) hdrs.set('X-CSRF-Token', h['X-CSRF-Token']);
        }
        init = { ...init, headers: hdrs, credentials: init.credentials || 'include' };
      }
      const res = await orig(input, init);
      // If CSRF invalid, refresh once and retry
      if (res.status === 403){
        const ct = res.headers.get('content-type') || '';
        const shouldRetry = ct.includes('application/json');
        if (shouldRetry){
          try{ await ensureCSRF(true); }catch{}
          const hdrs = new Headers(init.headers || {});
          const h = await csrfHeader();
          if (h['X-CSRF-Token']) hdrs.set('X-CSRF-Token', h['X-CSRF-Token']);
          const retryInit = { ...init, headers: hdrs, credentials: init.credentials || 'include' };
          return await orig(input, retryInit);
        }
      }
      return res;
    } catch (e){
      return Promise.reject(e);
    }
  };
}
