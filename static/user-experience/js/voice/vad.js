
/**
 * vad.js — New API (event-driven, echo-aware) + last-segment metrics + inert legacy shims
 *
 * Public:
 *   arm(), disarm(), isArmed()
 *   on('speechstart'|'speechend', fn), off(...)
 *   setSpeakingMode(isSpeaking, boost)
 *   getStream(), getLastSegmentMetrics()
 */
const _listeners = { speechstart: new Set(), speechend: new Set() };
let _armed=false, _ctx, _media, _source, _proc,
    _threshold=0.015, _currentThreshold=_threshold,
    _minStartMs=120, _minEndMs=160,
    _aboveMs=0, _belowMs=0, _isSpeech=false,
    _segMs=0, _rmsSum=0, _rmsCount=0, _rmsMax=0, _lastSegmentMetrics=null;

export async function arm(){
  if (_armed) return;
  _ctx = new (window.AudioContext||window.webkitAudioContext)();
  _media = await navigator.mediaDevices.getUserMedia({
    audio:{ echoCancellation:true, noiseSuppression:true, autoGainControl:false, channelCount:1 }
  });
  _source = _ctx.createMediaStreamSource(_media);
  const bufferSize=2048; _proc=_ctx.createScriptProcessor(bufferSize,1,1);
  _source.connect(_proc); _proc.connect(_ctx.destination);
  _proc.onaudioprocess=(e)=>{
    if(!_armed) return;
    const x=e.inputBuffer.getChannelData(0); let s=0; for(let i=0;i<x.length;i++) s+=x[i]*x[i];
    const rms=Math.sqrt(s/x.length), dt=(bufferSize/_ctx.sampleRate)*1000;
    if (rms>=_currentThreshold){ _aboveMs+=dt; _belowMs=0; } else { _belowMs+=dt; _aboveMs=0; }
    if (_isSpeech){ _segMs+=dt; _rmsSum+=rms; _rmsCount++; if (rms>_rmsMax) _rmsMax=rms; }
    if (!_isSpeech && _aboveMs>=_minStartMs){ _isSpeech=true; _segMs=0; _rmsSum=0; _rmsCount=0; _rmsMax=0; _emit('speechstart'); }
    else if (_isSpeech && _belowMs>=_minEndMs){ _isSpeech=false; _lastSegmentMetrics={ speech_ms: Math.round(_segMs), avg_rms: (_rmsCount? _rmsSum/_rmsCount : 0), max_rms: _rmsMax }; try{ window.dispatchEvent(new CustomEvent('chip:vad_metrics',{detail:_lastSegmentMetrics})); }catch{} _emit('speechend'); }
  };
  _armed=true; _emitMic('armed');
}

export async function disarm(){
  if(!_armed) return; _armed=false;
  try{ _proc.disconnect(); }catch{} try{ _source.disconnect(); }catch{}
  try{ (_media?.getTracks?.()||[]).forEach(t=>t.stop()); }catch{} try{ await _ctx?.close?.(); }catch{}
  _proc=_source=_media=_ctx=null; _isSpeech=false; _aboveMs=_belowMs=0; _segMs=0; _rmsSum=0; _rmsCount=0; _rmsMax=0; _emitMic('idle');
}

export function isArmed(){ return _armed; }
export function on(ev,fn){ (_listeners[ev]||_listeners.speechstart).add(fn); }
export function off(ev,fn){ (_listeners[ev]||_listeners.speechstart).delete(fn); }
export function setSpeakingMode(isSpeaking, boost=1.8){
  if (isSpeaking){ _currentThreshold=Math.max(_threshold*boost,0.02); _minStartMs=200; _minEndMs=140; }
  else { _currentThreshold=_threshold; _minStartMs=120; _minEndMs=160; }
}
export function getStream(){ return _media || null; }
export function getLastSegmentMetrics(){ return _lastSegmentMetrics; }

function _emit(ev){ (_listeners[ev]||[]).forEach(fn=>{ try{ fn(); }catch{} }); try{ window.dispatchEvent(new CustomEvent(ev==='speechstart'?'chip:vad_speechstart':'chip:vad_speechend')); }catch{} }
function _emitMic(mode){ try{ window.dispatchEvent(new CustomEvent('chip:mic',{detail:{mode}})); }catch{} }

// Legacy shims (NO-OPs) so old imports won't crash
export function setRecordCallbacks(){ _warn('setRecordCallbacks() is deprecated (no-op).'); }
export function setMicUIUpdater(){ _warn('setMicUIUpdater() is deprecated (no-op).'); }
export function setGuide(){ _warn('setGuide() is deprecated (no-op).'); }
export async function _vm_armVAD(){ return arm(); }
export async function _vm_disarmVAD(){ return disarm(); }
export function _vm_setSpeakingMode(a,b){ return setSpeakingMode(a,b); }
export function _vm_isArmed(){ return isArmed(); }
export function _vm_updateMicUI(){ /* no-op */ }
function _warn(m){ try{ console.warn('[legacy stub]', m); }catch{} }
