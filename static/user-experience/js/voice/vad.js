
/**
 * vad.js (backward-compatible, echo-aware)
 *
 * New API (used by the soft/echo-aware barge-in):
 *   - arm(), disarm(), setSpeakingMode(isSpeaking, boost), isArmed()
 *   - on(event, handler), off(event, handler)           // events: 'speechstart', 'speechend'
 *   - Emits DOM events 'chip:vad_speechstart'/'chip:vad_speechend'
 *
 * Legacy API (temporary, no-op unless noted):
 *   - _vm_armVAD()           -> alias of arm()
 *   - _vm_disarmVAD()        -> alias of disarm()
 *   - _vm_setSpeakingMode()  -> alias of setSpeakingMode()
 *   - _vm_isArmed()          -> alias of isArmed()
 *   - _vm_updateMicUI(state) -> updates body CSS class + emits 'chip:mic' (safe helper)
 *   - setMicUIUpdater(fn)    -> stores fn but is not invoked by the new pipeline
 *   - setGuide(guide)        -> no-op stub for legacy "voice guide"
 *   - setRecordCallbacks(cbs)-> no-op stub for legacy recorder callbacks
 */

const _listeners = { speechstart: new Set(), speechend: new Set() };
let _armed = false;
let _ctx, _media, _source, _proc;
let _threshold = 0.015;           // base RMS threshold
let _currentThreshold = _threshold;
let _minStartMs = 120;            // min duration above threshold to start
let _minEndMs = 160;              // min duration below threshold to end
let _aboveMs = 0;
let _belowMs = 0;
let _isSpeech = false;

// Legacy placeholders (intentionally unused by the new pipeline)
let __legacyMicUIUpdater = null;
let __legacyGuide = null;
let __legacyRecordCallbacks = null;

/** Arm the VAD and request mic permissions */
export async function arm() {
  if (_armed) return;
  _ctx = new (window.AudioContext || window.webkitAudioContext)();
  _media = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      channelCount: 1
    }
  });
  _source = _ctx.createMediaStreamSource(_media);

  const bufferSize = 2048;
  _proc = _ctx.createScriptProcessor(bufferSize, 1, 1);
  _source.connect(_proc);
  _proc.connect(_ctx.destination);

  _proc.onaudioprocess = (e) => {
    if (!_armed) return;
    const input = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < input.length; i++) { const s = input[i]; sum += s * s; }
    const rms = Math.sqrt(sum / input.length);
    const dt = (bufferSize / _ctx.sampleRate) * 1000; // ms
    const thr = _currentThreshold;

    if (rms >= thr) { _aboveMs += dt; _belowMs = 0; }
    else { _belowMs += dt; _aboveMs = 0; }

    if (!_isSpeech && _aboveMs >= _minStartMs) {
      _isSpeech = true; _emit('speechstart');
    } else if (_isSpeech && _belowMs >= _minEndMs) {
      _isSpeech = false; _emit('speechend');
    }
  };

  _armed = true;
}

/** Disarm and release mic */
export async function disarm() {
  if (!_armed) return;
  _armed = false;
  try { _proc.disconnect(); } catch {}
  try { _source.disconnect(); } catch {}
  try {
    const tracks = _media?.getTracks?.() || [];
    tracks.forEach(t => t.stop());
  } catch {}
  try { await _ctx?.close?.(); } catch {}
  _proc = _source = _media = _ctx = null;
  _isSpeech = false;
  _aboveMs = 0; _belowMs = 0;
}

/** Raise thresholds while assistant is speaking to resist echo */
export function setSpeakingMode(isSpeaking, boost = 1.8) {
  if (isSpeaking) {
    _currentThreshold = Math.max(_threshold * boost, 0.02);
    _minStartMs = 200;  // require a little longer before starting
    _minEndMs = 140;    // release quickly after user stops
  } else {
    _currentThreshold = _threshold;
    _minStartMs = 120;
    _minEndMs = 160;
  }
}

export function isArmed() { return _armed; }

export function on(event, handler) { (_listeners[event] || _listeners.speechstart).add(handler); }
export function off(event, handler) { (_listeners[event] || _listeners.speechstart).delete(handler); }

function _emit(event) {
  (_listeners[event] || []).forEach(fn => { try { fn(); } catch {} });
  try {
    window.dispatchEvent(new CustomEvent(event === 'speechstart' ? 'chip:vad_speechstart' : 'chip:vad_speechend'));
  } catch {}
}

/* ---------------- Legacy compatibility shims ---------------- */

export async function _vm_armVAD()            { return arm(); }
export async function _vm_disarmVAD()         { return disarm(); }
export function _vm_setSpeakingMode(a, b)     { return setSpeakingMode(a, b); }
export function _vm_isArmed()                 { return isArmed(); }

/**
 * Legacy UI helper. Accepts either a string ('idle','listening','speaking','muted','error','armed'),
 * a boolean (true -> 'listening', false -> 'idle'), or an object with {mode:string}.
 * Toggles body CSS classes and emits 'chip:mic' for listeners.
 */
export function _vm_updateMicUI(state) {
  let mode = 'idle';
  if (typeof state === 'string') mode = state;
  else if (typeof state === 'boolean') mode = state ? 'listening' : 'idle';
  else if (state && typeof state === 'object') mode = state.mode || mode;

  const classes = ['chip-mic-idle','chip-mic-listening','chip-mic-speaking','chip-mic-muted','chip-mic-error','chip-mic-armed'];
  try {
    const b = document.body;
    if (b) {
      b.classList.remove(...classes);
      const normalized = String(mode).toLowerCase();
      const map = {
        'idle':'chip-mic-idle',
        'listening':'chip-mic-listening',
        'speaking':'chip-mic-speaking',
        'muted':'chip-mic-muted',
        'error':'chip-mic-error',
        'armed':'chip-mic-armed'
      };
      const cls = map[normalized] || 'chip-mic-idle';
      b.classList.add(cls);
    }
  } catch {}

  try {
    window.dispatchEvent(new CustomEvent('chip:mic', { detail: { mode } }));
  } catch {}
}

// Additional legacy no-op exports (do not affect the new pipeline)

export function setMicUIUpdater(fn) {
  __legacyMicUIUpdater = typeof fn === 'function' ? fn : null;
  try { console.warn("[legacy stub] setMicUIUpdater() registered but is not used by the new VAD."); } catch {}
}

export function setGuide(guide) {
  __legacyGuide = guide || null;
  try { console.warn("[legacy stub] setGuide() is deprecated and ignored."); } catch {}
}

export function setRecordCallbacks(cbs) {
  __legacyRecordCallbacks = cbs || null;
  try { console.warn("[legacy stub] setRecordCallbacks() is deprecated and ignored."); } catch {}
}
