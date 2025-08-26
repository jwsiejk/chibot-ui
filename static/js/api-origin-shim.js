/*
 * Ask Chip - API Origin Shim (for Render Web Service or any server)
 * Forces all relative /api/* calls (fetch/XHR/WebSocket) to hit a configured API origin.
 * Include in <head> with:
 *   <script>window.__ASKCHIP_API_ORIGIN='https://<YOUR-API-SERVICE>.onrender.com';</script>
 *   <script src="/static/js/api-origin-shim.js"></script>
 */
(function () {
  var ORIGIN = String(window.__ASKCHIP_API_ORIGIN || '').replace(/\/$/, '');
  if (!ORIGIN) { console.warn('[AskChip Shim] __ASKCHIP_API_ORIGIN not set; shim inactive.'); return; }
  function rewriteUrl(input) {
    try {
      if (typeof input === 'string') {
        if (input === '/api' || input.startsWith('/api/')) return ORIGIN + input;
        return input;
      }
      if (typeof Request !== 'undefined' && input instanceof Request) {
        var u = input.url || '';
        if (u.startsWith('/api/')) return new Request(ORIGIN + u, input);
      }
    } catch (e) {}
    return input;
  }
  // fetch
  if (typeof window.fetch === 'function') {
    var _fetch = window.fetch;
    window.fetch = function (input, init) { return _fetch.call(this, rewriteUrl(input), init); };
  }
  // XHR
  if (typeof XMLHttpRequest === 'function') {
    var _open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      try { if (typeof url === 'string' && (url === '/api' || url.startsWith('/api/'))) arguments[1] = ORIGIN + url; } catch(e) {}
      return _open.apply(this, arguments);
    };
  }
  // WebSocket
  if (typeof window.WebSocket === 'function') {
    var _WS = window.WebSocket;
    window.WebSocket = function (url, protocols) {
      try {
        if (typeof url === 'string' && url.startsWith('/api/')) {
          var http = new URL(ORIGIN);
          var scheme = (http.protocol === 'https:') ? 'wss://' : 'ws://';
          var wsUrl = scheme + http.host + url;
          return new _WS(wsUrl, protocols);
        }
      } catch(e) {}
      return new _WS(url, protocols);
    };
    for (var k in _WS) try { window.WebSocket[k] = _WS[k]; } catch {}
    window.WebSocket.prototype = _WS.prototype;
  }
  console.log('[AskChip Shim] Active. Rewriting /api/* to: ' + ORIGIN);
})();