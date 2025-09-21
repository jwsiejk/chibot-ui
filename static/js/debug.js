
// static/js/debug.js — opt-in deep console tracing (set localStorage.AskChipDebug='1')
(() => {
  try {
    if (localStorage.AskChipDebug !== '1') return;
  } catch (e) { return; }

  const tag = (t, o) => console.info(`[DBG] ${t}`, o ?? '');

  // WS spy (send + receive)
  const _send = WebSocket.prototype.send;
  WebSocket.prototype.send = function(data){
    try { tag('UI→WS', typeof data === 'string' ? JSON.parse(data) : data); } catch { tag('UI→WS', data); }
    return _send.call(this, data);
  };
  const _add = WebSocket.prototype.addEventListener;
  WebSocket.prototype.addEventListener = function(ev, fn){
    if (ev === 'message') {
      return _add.call(this, ev, e => {
        try {
          const j = JSON.parse(e.data);
          if (j?.type === 'assistant_audio') {
            tag('WS→UI audio', { turn_id: j.turn_id, chunks: j.audio_chunks?.length, last: j.is_last });
          } else {
            tag('WS→UI', j);
          }
        } catch { tag('WS→UI', e.data); }
        try { fn(e); } catch {}
      });
    }
    return _add.call(this, ev, fn);
  };

  // Performance markers
  window.Trace = {
    mark(name){ performance.mark(name); tag('mark', name); },
    measure(a,b){ try {
      performance.measure(`${a}→${b}`, a, b);
      const m = performance.getEntriesByName(`${a}→${b}`).pop();
      tag('measure', { name:`${a}→${b}`, dur_ms: Math.round(m.duration) });
    } catch(e) {}
    }
  };

  tag('debug:on', { userAgent: navigator.userAgent });
})();
