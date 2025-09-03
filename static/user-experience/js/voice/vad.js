
/**
 * vad.js
 * Simple VAD with echo-aware thresholding and DOM events.
 *
 * Exposes:
 *   - arm() / disarm()
 *   - on(event, handler) / off(event, handler)   // events: 'speechstart', 'speechend'
 *   - setSpeakingMode(isSpeaking, boost=1.8)     // raises threshold while assistant speaks
 *   - isArmed()
 *
 * Emits DOM CustomEvents for loose coupling as well:
 *   - 'chip:vad_speechstart'
 *   - 'chip:vad_speechend'
 */

const _listeners = { speechstart: new Set(), speechend: new Set() };
let _armed = false;
let _ctx, _media, _source, _proc;
let _threshold = 0.015;           // base RMS threshold
let _thresholdSpeaking = 0.027;   // auto-calculated
let _currentThreshold = _threshold;
let _minStartMs = 120;            // min duration above threshold to start
let _minEndMs = 160;              // min duration below threshold to end
let _aboveMs = 0;
let _belowMs = 0;
let _isSpeech = false;

export async function arm() {
  if (_armed) return;
  _ctx = new (window.AudioContext || window.webkitAudioContext)();

  // Try to prefer hardware echo canceller
  _media = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      channelCount: 1
    }
  });

  _source = _ctx.createMediaStreamSource(_media);

  // ScriptProcessorNode is simple and widely supported
  const bufferSize = 2048;
  _proc = _ctx.createScriptProcessor(bufferSize, 1, 1);
  _source.connect(_proc);
  _proc.connect(_ctx.destination);

  _proc.onaudioprocess = (e) => {
    if (!_armed) return;
    const input = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < input.length; i++) {
      const s = input[i];
      sum += s * s;
    }
    const rms = Math.sqrt(sum / input.length);

    const dt = (bufferSize / _ctx.sampleRate) * 1000; // ms
    const thr = _currentThreshold;

    if (rms >= thr) {
      _aboveMs += dt;
      _belowMs = 0;
    } else {
      _belowMs += dt;
      _aboveMs = 0;
    }

    if (!_isSpeech && _aboveMs >= _minStartMs) {
      _isSpeech = true;
      _emit('speechstart');
    } else if (_isSpeech && _belowMs >= _minEndMs) {
      _isSpeech = false;
      _emit('speechend');
    }
  };

  _armed = true;
}

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
  _aboveMs = 0;
  _belowMs = 0;
}

/**
 * While the assistant is speaking, raise threshold & lengthen start gate
 * to resist echo false-positives.
 * @param {boolean} isSpeaking
 * @param {number} boost multiplier for threshold (default 1.8)
 */
export function setSpeakingMode(isSpeaking, boost = 1.8) {
  if (isSpeaking) {
    _currentThreshold = Math.max(_threshold * boost, 0.02);
    _minStartMs = 200;  // require a little longer above threshold
    _minEndMs = 140;    // but still release quickly if the user stops
  } else {
    _currentThreshold = _threshold;
    _minStartMs = 120;
    _minEndMs = 160;
  }
}

export function isArmed() { return _armed; }

export function on(event, handler) {
  (_listeners[event] || _listeners.speechstart).add(handler);
}

export function off(event, handler) {
  (_listeners[event] || _listeners.speechstart).delete(handler);
}

function _emit(event) {
  (_listeners[event] || []).forEach(fn => { try { fn(); } catch {} });
  try {
    window.dispatchEvent(new CustomEvent(event === 'speechstart' ? 'chip:vad_speechstart' : 'chip:vad_speechend'));
  } catch {}
}
