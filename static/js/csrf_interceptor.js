// AskChip CSRF fetch interceptor (recursion-safe, minimal GETs)
(function(){
  // keep only one wrapper
  if (window.__askchip && window.__askchip.fetchWrapped) return;
  if (!window.__askchip) window.__askchip = {};

  const _origFetch = window.fetch.bind(window);
  let __csrfToken = null;
  let __csrfAt = 0;
  let __csrfPromise = null;
  const TTL_MS = 60_000; // 60s cache

  async function _getCSRF(force){
    const now = Date.now();
    if (!force && __csrfToken && (now - __csrfAt) < TTL_MS) return __csrfToken;
    if (__csrfPromise && !force) return __csrfPromise;
    __csrfPromise = (async () => {
      try{
        // IMPORTANT: use original fetch to avoid recursion and skip wrapper behavior
        const r = await _origFetch('/api/v1/auth/csrf', { credentials:'include', headers: { 'X-AskChip-CSRF':'1' } });
        const j = await r.json().catch(()=>({}));
        __csrfToken = j && j.csrf || null;
        __csrfAt = Date.now();
        return __csrfToken;
      } finally {
        __csrfPromise = null;
      }
    })();
    return __csrfPromise;
  }
  window.__askchip.ensureCSRF = async function(force=false){ return _getCSRF(force); };

  function _isApi(url){
    try {
      const u = new URL(url, location.origin);
      if (u.origin !== location.origin) return false;
      return u.pathname.startsWith('/api/v1/');
    } catch(_) { return false; }
  }
  function _isMutating(method){ return /^(POST|PUT|PATCH|DELETE)$/i.test(method||''); }
  function _isCsrfEndpoint(url){
    try{
      const u = new URL(url, location.origin);
      return u.pathname === '/api/v1/auth/csrf';
    }catch(_){ return false; }
  }

  window.fetch = async function(resource, init){
    const url = (typeof resource === 'string') ? resource : resource && resource.url;
    init = init || {};
    const method = (init.method || 'GET').toUpperCase();

    // Always use credentials for API calls
    if (_isApi(url)) init.credentials = init.credentials || 'include';

    // Do not interfere with the CSRF token endpoint itself
    if (_isApi(url) && !_isCsrfEndpoint(url) && _isMutating(method)){
      // Ensure token (recursion-safe)
      const csrf = await _getCSRF(false);
      if (csrf){
        const headers = new Headers(init.headers || {});
        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', csrf);
        init.headers = headers;
      }
    }
    return _origFetch(resource, init);
  };
  window.__askchip.fetchWrapped = true;

  // Optional: quick debug helper
  window.__askchip.debugCsrf = async function(){
    const t = await _getCSRF(false);
    return { token: t, at: __csrfAt };
  };
})();
