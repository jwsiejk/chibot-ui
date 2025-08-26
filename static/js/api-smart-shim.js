// static/js/api_smart-shim.js
(function(){
  const VER = '2025-08-26b';
  try {
    const prev = window.__AskChipShimVersion;
    window.__AskChipShimVersion = VER;
    const base = window.location.origin;
    console.log('[AskChip Smart Shim] active v%s. API_ORIGIN=%s', VER, base);

    const origFetch = window.fetch;
    window.fetch = async function(input, init){
      const url = (typeof input === 'string') ? input : input.url;
      const method = (init && init.method) || 'GET';
      const isVoice = /\/api\/voice\//.test(url);
      let resp = await origFetch(input, init);
      if(!isVoice) return resp;

      // Clone + normalize JSON payload for voice endpoints
      try {
        const copy = resp.clone();
        const data = await copy.json();
        const normalized = normalizeTTS(data);
        return new Response(JSON.stringify(normalized), {
          status: resp.status,
          headers: {'Content-Type': 'application/json'}
        });
      } catch (e) {
        console.warn('[AskChip Smart Shim] could not normalize voice response:', e);
        return resp;
      }
    };

    function normalizeTTS(d){
      if (d && d.ok){
        if (!d.audio && d.audio_base64){ d.audio = d.audio_base64; }
        if (!d.visemes && d.marks){ d.visemes = d.marks; }
      }
      return d;
    }

    // Quick manual test: paste __askchip_shim_test() into the console
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
