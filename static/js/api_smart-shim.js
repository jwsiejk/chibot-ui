// static/js/api_smart-shim.js
(function(){
  const VER = '2025-08-26c';
  try {
    window.__AskChipShimVersion = VER;
    const base = window.location.origin;
    console.log('[AskChip Smart Shim] active v%s. API_ORIGIN=%s', VER, base);

    const origFetch = window.fetch;
    window.fetch = async function(input, init){
      const url = (typeof input === 'string') ? input : input.url;
      const method = (init && init.method) || 'GET';
      let resp = await origFetch(input, init);

      // Normalize voice
      if (/\/api\/voice\//.test(url)){
        try {
          const copy = resp.clone();
          const data = await copy.json();
          const normalized = normalizeTTS(data);
          return jsonResponse(resp.status, normalized);
        } catch (e) {
          console.warn('[AskChip Shim] voice normalize failed', e);
          return resp;
        }
      }

      // Profile fallback (save locally if server 5xx or non-json)
      if (url.endsWith('/api/profile')){
        try {
          const copy = resp.clone();
          const data = await copy.json(); // ok json -> pass through
          return jsonResponse(resp.status, data);
        } catch (e) {
          // Non-JSON or failed: synthesize success using localStorage
          try{
            if (method === 'POST' && init && init.body){
              localStorage.setItem('askchip.profile', init.body);
              return jsonResponse(200, { ok:true, source:'shim-local' });
            } else {
              const raw = localStorage.getItem('askchip.profile');
              const profile = raw ? JSON.parse(raw) : {};
              return jsonResponse(200, { ok:true, profile, source:'shim-local' });
            }
          }catch(e2){
            return jsonResponse(200, { ok:true, source:'shim-empty' });
          }
        }
      }

      // Ask/chat normalization: ensure JSON, avoid HTML error loops
      if (/\/api\/(ask|chat|ask_chip)/.test(url)){
        try {
          const copy = resp.clone();
          const data = await copy.json();
          return jsonResponse(resp.status, data);
        } catch (e) {
          // non-json -> return normalized error object
          const onceKey = '__askchip_nonjson_once';
          if (!sessionStorage.getItem(onceKey)) {
            sessionStorage.setItem(onceKey, String(Date.now()));
            return jsonResponse(200, { ok:false, error:'non_json_response' });
          } else {
            // suppress repeats to prevent TTS spam loops
            return jsonResponse(200, { ok:false, error:'repeat_non_json_suppressed' });
          }
        }
      }

      return resp;
    };

    function normalizeTTS(d){
      if (d && d.ok){
        if (!d.audio && d.audio_base64){ d.audio = d.audio_base64; }
        if (!d.visemes && d.marks){ d.visemes = d.marks; }
      }
      return d;
    }
    function jsonResponse(status, obj){
      return new Response(JSON.stringify(obj), { status: status, headers: {'Content-Type':'application/json'} });
    }

    // Expose quick tests
    window.__askchip_shim_test = async function(){
      const r = await fetch('/api/voice/tts_with_visemes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: 'Shim test: you should hear this.' })
      }).then(r=>r.json());
      console.log('[shim test] response:', r);
      if (r && r.ok && r.audio){
        const a = new Audio('data:audio/mpeg;base64,' + r.audio);
        a.play();
      } else {
        console.warn('[shim test] no playable audio');
      }
    };
  } catch (e){
    console.error('[AskChip Smart Shim] failed to load:', e);
  }
})();
