// AskChip fetch interceptor for CSRF and credentials (production-safe)
(function(){
  if (!window.__askchip) window.__askchip = {};
  if (window.__askchip.fetchWrapped) return;
  const origFetch = window.fetch.bind(window);

  async function _ensureCSRF(force){
    if (window.__askchip.ensureCSRF) return window.__askchip.ensureCSRF(force);
    // lightweight fallback
    try{
      const r = await fetch('/api/v1/auth/csrf', { credentials:'include' });
      const j = await r.json().catch(()=>({}));
      window.__askchip.__csrfToken = j.csrf || null;
      return window.__askchip.__csrfToken;
    }catch(_){ return null; }
  }

  function _isApi(url){
    try{
      // support absolute and relative URLs
      const u = new URL(url, location.origin);
      return u.origin === location.origin && u.pathname.startsWith('/api/v1/');
    }catch(_){ return false; }
  }

  window.fetch = async function(resource, init){
    try{
      const url = (typeof resource === 'string') ? resource : resource.url;
      if (_isApi(url)){
        init = init || {};
        // Ensure credentials so CSRF cookie is sent
        if (!init.credentials) init.credentials = 'include';
        // Normalize headers
        let headers = new Headers(init.headers || {});
        if (!headers.has('X-CSRF-Token')){
          const csrf = await _ensureCSRF(false);
          if (csrf) headers.set('X-CSRF-Token', csrf);
        }
        init.headers = headers;
      }
    }catch(_){} // never break fetch
    return origFetch(resource, init);
  };
  window.__askchip.fetchWrapped = true;
})();
