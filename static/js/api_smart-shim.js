// static/js/api_smart-shim.js
(function(){
  const VER = '2025-08-26c';
  try {
    const base = window.location.origin;
    console.log('[AskChip Smart Shim] active v%s. API_ORIGIN=%s', VER, base);

    // ---------- fetch() voice normalizer ----------
    const origFetch = window.fetch;
    window.fetch = async function(input, init){
      const url = (typeof input === 'string') ? input : input.url;
      let resp = await origFetch(input, init);
      if(!/\/api\/voice\//.test(url)) return resp;
      try {
        const copy = resp.clone();
        const data = await copy.json();
        const normalized = normalizeTTS(data);
        return new Response(JSON.stringify(normalized), {
          status: resp.status,
          headers: {'Content-Type': 'application/json'}
        });
      } catch (e) {
        console.warn('[AskChip Smart Shim] voice normalize failed:', e);
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

    // ---------- EventSource (SSE) normalizer ----------
    const RealES = window.EventSource;
    function toJSONData(text){
      // If already JSON, pass through
      try { JSON.parse(text); return text; } catch(e){}
      // OpenAI-style [DONE]
      if (text === '[DONE]' || text === '[done]'){
        return JSON.stringify({ type: 'done', done: true });
      }
      // Plain text chunk -> wrap so main.js can parse as JSON
      return JSON.stringify({ type: 'chunk', delta: text, content: text });
    }
    function cloneEvent(e, newData){
      try {
        return new MessageEvent('message', {
          data: newData,
          lastEventId: e.lastEventId,
          origin: e.origin
        });
      } catch (err){
        // Older browsers
        const ev = document.createEvent('MessageEvent');
        ev.initMessageEvent('message', true, true, newData, e.origin, '', window, null);
        return ev;
      }
    }
    class ShimEventSource {
      constructor(url, conf){
        this.__es = new RealES(url, conf);
        this.url = url;
        this.withCredentials = this.__es.withCredentials;
        this.readyState = this.__es.readyState;
        this.__listeners = { open: new Set(), error: new Set(), message: new Set() };

        this.__es.addEventListener('open', (e)=>{
          this.readyState = this.__es.readyState;
          if (typeof this.onopen === 'function') this.onopen(e);
          this.__listeners.open.forEach(fn=>{ try{ fn(e); }catch(_){} });
        });
        this.__es.addEventListener('error', (e)=>{
          if (typeof this.onerror === 'function') this.onerror(e);
          this.__listeners.error.forEach(fn=>{ try{ fn(e); }catch(_){} });
        });
        this.__es.addEventListener('message', (e)=>{
          const data = toJSONData(e.data);
          const wrapped = cloneEvent(e, data);
          if (typeof this.onmessage === 'function') this.onmessage(wrapped);
          this.__listeners.message.forEach(fn=>{ try{ fn(wrapped); }catch(_){} });
        });
      }
      close(){ this.__es.close(); this.readyState = this.__es.readyState; }
      addEventListener(type, cb){ (this.__listeners[type] || (this.__listeners[type]=new Set())).add(cb); }
      removeEventListener(type, cb){ const s = this.__listeners[type]; if (s) s.delete(cb); }
      dispatchEvent(ev){ return true; }
    }
    // Only patch if not already patched
    if (!window.__AskChipSSEShimApplied){
      window.EventSource = ShimEventSource;
      window.__AskChipSSEShimApplied = true;
      console.log('[AskChip Smart Shim] SSE normalizer installed');
    }

    // Quick manual test for TTS
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
