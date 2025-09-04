
/**
 * vad.js — New API (event-driven, echo-aware) with per-turn metrics + NO-OP legacy shims
 */
const _listeners = { speechstart: new Set(), speechend: new Set() };
let _armed=false, _ctx, _media, _source, _proc,
    _threshold=0.015, _currentThreshold=_threshold,
    _minStartMs=120, _minEndMs=160, _aboveMs=0, _belowMs=0, _isSpeech=false;

// expose simple per-turn metrics (updated each turn)
let _turnAvgRms = null, _turnMaxRms = null, _turnSpeechMs = null;
Object.defineProperty(window, "__AC_lastAvgRms", { get(){ return _turnAvgRms; } });
Object.defineProperty(window, "__AC_lastMaxRms", { get(){ return _turnMaxRms; } });
Object.defineProperty(window, "__AC_lastSpeechMs", { get(){ return _turnSpeechMs; } });

export async function arm(){
  if (_armed) return;
  _ctx = new (window.AudioContext||window.webkitAudioContext)();
  _media = await navigator.mediaDevices.getUserMedia({ audio:{ echoCancellation:true, noiseSuppression:true, autoGainControl:false, channelCount:1 }});
  _source = _ctx.createMediaStreamSource(_media);
  const bufferSize=2048; _proc=_ctx.createScriptProcessor(bufferSize,1,1);
  _source.connect(_proc); _proc.connect(_ctx.destination);

  let _accumEnergy = 0, _accumSamples = 0, _max = 0, _speechStartTs = 0;
  _proc.onaudioprocess=(e)=>{
    if (!_armed) return;
    const x=e.inputBuffer.getChannelData(0); let s=0; for(let i=0;i<x.length;i++){ const v=x[i]; s+=v*v; if (v>_max) _max=v; }
    const rms=Math.sqrt(s/x.length);
    const dt=(bufferSize/_ctx.sampleRate)*1000;
    if (rms>=_currentThreshold){ _aboveMs+=dt; _belowMs=0; } else { _belowMs+=dt; _aboveMs=0; }

    // accumulate simple prosody while in speech segment
    if (_isSpeech){ _accumEnergy += s; _accumSamples += x.length; }

    if (!_isSpeech && _aboveMs>=_minStartMs){
      _isSpeech = true; _emit('speechstart'); _speechStartTs = performance.now(); _accumEnergy = 0; _accumSamples = 0; _max = 0;
    } else if (_isSpeech && _belowMs>=_minEndMs){
      _isSpeech = false; _emit('speechend');
      const dur = Math.max(0, performance.now() - _speechStartTs);
      _turnSpeechMs = dur;
      _turnAvgRms = _accumSamples ? Math.sqrt(_accumEnergy/_accumSamples) : null;
      _turnMaxRms = _max || null;
    }
  };
  _armed=true; _emitMic('armed');
}
export async function disarm(){
  if (!_armed) return; _armed=false;
  try{ _proc.disconnect(); }catch{} try{ _source.disconnect(); }catch{}
  try{ (_media?.getTracks?.()||[]).forEach(t=>t.stop()); }catch{} try{ await _ctx?.close?.(); }catch{}
  _proc=_source=_media=_ctx=null; _isSpeech=false; _aboveMs=_belowMs=0; _emitMic('idle');
}
export function isArmed(){ return _armed; }
export function on(ev,fn){ (_listeners[ev]||_listeners.speechstart).add(fn); }
export function off(ev,fn){ (_listeners[ev]||_listeners.speechstart).delete(fn); }
export function setSpeakingMode(isSpeaking,boost=1.8){
  if (isSpeaking){ _currentThreshold=Math.max(_threshold*boost,0.02); _minStartMs=200; _minEndMs=140; }
  else { _currentThreshold=_threshold; _minStartMs=120; _minEndMs=160; }
}
export function getStream(){ return _media || null; }

function _emit(ev){ (_listeners[ev]||[]).forEach(fn=>{ try{ fn(); }catch{} }); try{ window.dispatchEvent(new CustomEvent(ev==='speechstart'?'chip:vad_speechstart':'chip:vad_speechend')); }catch{} }
function _emitMic(mode){ try{ window.dispatchEvent(new CustomEvent('chip:mic',{detail:{mode}})); }catch{} }

// Legacy shims (NO-OPs)
export function setRecordCallbacks(){ _warn('setRecordCallbacks() is deprecated (no-op).'); }
export function setMicUIUpdater(){ _warn('setMicUIUpdater() is deprecated (no-op).'); }
export function setGuide(){ _warn('setGuide() is deprecated (no-op).'); }
export async function _vm_armVAD(){ return arm(); }
export async function _vm_disarmVAD(){ return disarm(); }
export function _vm_setSpeakingMode(a,b){ return setSpeakingMode(a,b); }
export function _vm_isArmed(){ return isArmed(); }
export function _vm_updateMicUI(){ /* no-op */ }
function _warn(m){ try{ console.warn('[legacy stub]', m); }catch{} }
