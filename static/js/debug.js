
(() => {
  try { if (localStorage.AskChipDebug !== '1') return; } catch (e) { return; }
  const tag = (t, o) => console.info('[DBG] ' + t, o ?? '');
  const _send = WebSocket.prototype.send;
  WebSocket.prototype.send = function(d){ try { tag('UI→WS', typeof d==='string'?JSON.parse(d):d);}catch{tag('UI→WS',d)}; return _send.call(this,d); };
  const _add = WebSocket.prototype.addEventListener;
  WebSocket.prototype.addEventListener = function(ev,fn){
    if(ev==='message'){ return _add.call(this,ev,e=>{ try{ const j=JSON.parse(e.data); if(j?.type==='assistant_audio'){ tag('WS→UI audio',{turn_id:j.turn_id,chunks:j.audio_chunks?.length,last:j.is_last}); } else { tag('WS→UI',j);} }catch{ tag('WS→UI',e.data);} try{fn(e);}catch{} }); }
    return _add.call(this,ev,fn);
  };
  window.Trace = { mark(n){ performance.mark(n); tag('mark',n); }, measure(a,b){ try{ performance.measure(a+'→'+b,a,b); const m=performance.getEntriesByName(a+'→'+b).pop(); tag('measure',{name:a+'→'+b,dur_ms:Math.round(m.duration)});}catch(e){} } };
  tag('debug:on', { ua: navigator.userAgent });
})();
