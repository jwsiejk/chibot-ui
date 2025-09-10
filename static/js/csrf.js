// static/js/csrf.js — single source of truth
let _token = null;
let _inflight = null;

export async function ensureCSRF(force=false){
  if (!force && _token) return _token;
  if (_inflight && !force) return _inflight;
  _inflight = (async () => {
    const r = await fetch('/api/v1/csrf', { credentials:'include' });
    const t = r.headers.get('X-CSRF-Token') || '';
    if (t) _token = t;
    // Reflect into <meta> for any consumers
    let meta = document.head.querySelector('meta[name=csrf]');
    if (!meta){ meta = document.createElement('meta'); meta.name='csrf'; document.head.appendChild(meta); }
    meta.content = _token || '';
    _inflight = null;
    return _token;
  })();
  return _inflight;
}

export async function csrfHeader(){
  const t = _token || (await ensureCSRF().catch(()=>''));
  return t ? {'X-CSRF-Token': t} : {};
}

export function installFetchInterceptor(){
  const orig = window.fetch;
  window.fetch = async (input, init={}) => {
    const url = (typeof input === 'string') ? input : (input?.url || '');
    const sameOrigin = !/^https?:\/\//i.test(url) || url.startsWith(location.origin);
    const method = (init.method || 'GET').toUpperCase();
    if (sameOrigin && ['POST','PUT','PATCH','DELETE'].includes(method)){
      const hdrs = new Headers(init.headers || {});
      if (!hdrs.get('X-CSRF-Token')){
        const h = await csrfHeader();
        if (h['X-CSRF-Token']) hdrs.set('X-CSRF-Token', h['X-CSRF-Token']);
      }
      init = { ...init, headers: hdrs, credentials: init.credentials || 'include' };
    }
    let res = await orig(input, init);
    if (res.status === 403){
      await ensureCSRF(true).catch(()=>{});
      const hdrs = new Headers(init.headers || {});
      const h = await csrfHeader();
      if (h['X-CSRF-Token']) hdrs.set('X-CSRF-Token', h['X-CSRF-Token']);
      const retryInit = { ...init, headers: hdrs, credentials: init.credentials || 'include' };
      res = await orig(input, retryInit);
    }
    return res;
  };
}
