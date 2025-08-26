/* Smart API Shim - single Render service (Flask) */
(function () {
  var API_ORIGIN = (window.__ASKCHIP_API_ORIGIN || '').replace(/\/$/, '');
  var sameOrigin = !API_ORIGIN;
  function stripApi(path){ return path.replace(/^\/api(?=\/|$)/,''); }
  var _fetch = window.fetch && window.fetch.bind(window);
  if (_fetch) {
    window.fetch = function(input, init){
      var first = input;
      if (typeof input === 'string' && input.startsWith('/api/') && !API_ORIGIN) {
        // same origin; try as-is; on 404 we will retry stripped
      } else if (typeof input === 'string' && input.startsWith('/api/') && API_ORIGIN) {
        first = API_ORIGIN + input;
      }
      var p = _fetch(first, init);
      try {
        var urlStr = (typeof first==='string') ? first : (first && first.url) || '';
        var isSame = !API_ORIGIN && /^\/api\/.+/.test(urlStr);
        if (!isSame) return p;
      } catch(e){ return p; }
      return p.then(function(res){
        if (res && res.status===404) {
          var path = (typeof input==='string') ? input : (input && input.url) || '';
          var alt = stripApi(path);
          var req = (typeof input==='string') ? alt : new Request(alt, input);
          return _fetch(req, init);
        }
        return res;
      });
    };
  }
  if (typeof window.WebSocket==='function') {
    var _WS = window.WebSocket;
    window.WebSocket = function(url, protocols){
      try {
        if (typeof url==='string' && url.startsWith('/api/')) {
          if (API_ORIGIN) {
            var http = new URL(API_ORIGIN);
            var scheme = (http.protocol==='https:' ? 'wss://' : 'ws://');
            return new _WS(scheme + http.host + url, protocols);
          } else {
            try { return new _WS(url, protocols); } catch(e) { return new _WS(stripApi(url), protocols); }
          }
        }
      } catch(e){}
      return new _WS(url, protocols);
    };
    for (var k in _WS) try{ window.WebSocket[k]=_WS[k]; }catch{}
    window.WebSocket.prototype = _WS.prototype;
  }
  console.log('[AskChip Smart Shim] active. API_ORIGIN='+(API_ORIGIN||'(same-origin)'));
})();