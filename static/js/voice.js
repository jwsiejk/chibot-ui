/*
Citations for context (non-functional):
:contentReference[oaicite:0]{index=0}
:contentReference[oaicite:1]{index=1}
*/

/* static/js/voice.js — Production voice pipeline (VAD + one-turn recorder + WS)
   Goals satisfied:
    • Echo-aware VAD (threshold boost while TTS is playing)
    • Streaming Opus blobs per user turn (prefers OGG/Opus when supported; falls back to WebM/Opus)
    • Soft barge-in: pause Chip TTS on committed speech start
    • Turn timeout (safety), robust errors, clean session end
    • UI state events: 'askchip-voice' {state:'armed'|'recording'|'idle'}

   Notes:
    • Do NOT JSON-wrap audio; send raw binary via ws.send(ArrayBuffer) (see ws.js).
    • CloseStream is emitted AFTER all audio chunks are queued to the socket (keep WS stream open while draining).
*/

import { VAD } from './voice/vad.js';
import { sendAudioChunk, sendCloseStream } from './ws_module.js';
import { stopPlayback, pausePlayback, resumePlayback, isPlaying as ttsIsPlaying } from './audio.js';

// Public API (matches prior usage)
export async function initMic(stream = null) { return await _ensureMic(stream); }
export async function armVAD(stream = null, opts = {}) { return await _arm(stream, opts); }
export function disarmVAD() { _disarm(); }
export function isRecording() { return !!(state.rec && state.rec.state === 'recording'); }
export function bargeIn() { _bargeIn(); }         // keeps API parity
export function setVadBoost(_v) { /* kept for API parity; no-op */ }

// ---- Internal state ---------------------------------------------------------

// Prefer OGG/Opus where supported (provider-friendly); fallback to WebM/Opus.
const REC_MIME = (typeof MediaRecorder !== 'undefined'
  && typeof MediaRecorder.isTypeSupported === 'function'
  && MediaRecorder.isTypeSupported('audio/ogg; codecs=opus'))
  ? 'audio/ogg; codecs=opus'
  : 'audio/webm; codecs=opus';

const DEFAULT_MAX_TURN_MS = 90_000; // 90s guardrail
const MIN_VALID_BLOB_BYTES = 1;     // drop only truly empty blobs (preserve headers)

const state = {
  stream: null,
  ctx: null,
  source: null,
  analyser: null,
  vad: null,
  rec: null,
  chunkSendPromise: Promise.resolve(),
  chunkBytesSent: 0,
  chunkSendError: null,
  turnTimer: null,
  turnOpen: false,   // track whether a turn is currently open server-side
  deviceLogged: false,
  // NEW: min-turn gating
  recStartedAt: 0,
  pendingEndTimer: null,
  ttsPlaying: false,
  bargeConfirmTimer: null,
  bargeConfirmActive: false,
};

const BARGE_CONFIRM_DEFAULT_MS = 420;
let bargeConfirmMs = BARGE_CONFIRM_DEFAULT_MS;
try {
  const cfg = window.__askchip_config || {};
  if (cfg && typeof cfg.barge_confirm_ms === 'number') {
    bargeConfirmMs = cfg.barge_confirm_ms;
  }
} catch {}
bargeConfirmMs = Math.max(120, Number(bargeConfirmMs) || BARGE_CONFIRM_DEFAULT_MS);

try {
  window.addEventListener('chip-tts', (ev) => {
    const detail = ev?.detail || {};
    const playing = String(detail.state || '').toLowerCase() === 'playing';
    state.ttsPlaying = playing;
  });
} catch {}

// ---- Helpers ----------------------------------------------------------------

function _emitVoiceState(state, detail = {}) {
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice', { detail: { state, ...detail } }));
  } catch {}
}

function _logLifecycle(event, detail = {}, level = 'debug') {
  const payload = { event, ...(detail && typeof detail === 'object' ? detail : { detail }) };
  try {
    const method = (typeof console[level] === 'function') ? level : 'log';
    console[method]?.('[voice]', event, payload);
  } catch {}
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice-lifecycle', { detail: payload }));
  } catch {}
}

function _clearPendingEndTimer() {
  if (state.pendingEndTimer) {
    try { clearTimeout(state.pendingEndTimer); } catch {}
    state.pendingEndTimer = null;
  }
}

function _clearBargeConfirm(resume = false) {
  if (state.bargeConfirmTimer) {
    try { clearTimeout(state.bargeConfirmTimer); } catch {}
    state.bargeConfirmTimer = null;
  }
  if (state.bargeConfirmActive) {
    state.bargeConfirmActive = false;
    if (resume) {
      try { resumePlayback(); } catch {}
    }
  }
}

async function _ensureMic(externalStream = null) {
  if (state.stream && state.stream.active) return state.stream;

  if (state.stream && !state.stream.active) {
    _teardownAudioGraph();
    state.stream = null;
  }

  let stream = externalStream;

  if (!stream || !stream.active) {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      _logLifecycle('mic_perm_denied', { reason: 'mediaDevices_unavailable' }, 'warn');
      throw new Error('Media devices API unavailable');
    }

    const constraints = {
      audio: {
        channelCount: 1,
        sampleRate: 48000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false
      }
    };

    _logLifecycle('mic_request_perm', { constraints });
    try {
      // Request a clean mono stream with echo/noise controls
      stream = await navigator.mediaDevices.getUserMedia(constraints);
      _logLifecycle('mic_perm_granted');
    } catch (err) {
      _logLifecycle('mic_perm_denied', {
        name: err?.name,
        message: err?.message,
        constraints,
      }, 'warn');
      throw err;
    }
  }

  // Build WebAudio chain
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC({ sampleRate: 48000 });
  if (ctx.state === 'suspended') { try { await ctx.resume(); } catch {} }

  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.06;          // LESS twitchy (was 0.03)
  source.connect(analyser);

  state.stream = stream;
  state.ctx = ctx;
  state.source = source;
  state.analyser = analyser;

  if (!state.deviceLogged) {
    const [track] = stream.getAudioTracks();
    let settings = {};
    try { settings = track?.getSettings?.() || {}; } catch {}
    const detail = {
      label: (track?.label && track.label.trim()) || settings.label || settings.deviceId || 'unknown',
      sampleRate: settings.sampleRate ?? ctx?.sampleRate ?? null,
      channels: settings.channelCount ?? settings.channels ?? ctx?.destination?.channelCount ?? 1,
    };
    _logLifecycle('mic_device_selected', detail);
    state.deviceLogged = true;
  }

  return stream;
}

function _safeClearTurnTimer() {
  if (state.turnTimer) { clearTimeout(state.turnTimer); state.turnTimer = null; }
}

function _stopRecorder(detail = null) {
  const recorder = state.rec;
  const wasActive = !!recorder && recorder.state !== 'inactive';
  const payload = Object.assign({
    active: wasActive,
    hasRecorder: !!recorder,
  }, detail || {});
  _logLifecycle('mic_stop', payload, wasActive ? 'debug' : 'info');

  try { if (state.rec && state.rec.state !== 'inactive') state.rec.stop(); } catch {}
  // intentionally keep state.rec reference nullable here; onstop handler handles final close
  state.rec = null;
}

function _teardownVADOnly() {
  try { state.vad && state.vad.stop(); } catch {}
  state.vad = null;
}

function _teardownAudioGraph() {
  try { state.source && state.source.disconnect(); } catch {}
  try { state.analyser && state.analyser.disconnect(); } catch {}
  try { state.ctx && state.ctx.close && state.ctx.close(); } catch {}
  state.source = null;
  state.analyser = null;
  state.ctx = null;
  state.deviceLogged = false;
}

function _disarm() {
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _clearBargeConfirm(false);
  _stopRecorder({ reason: 'manual_disarm' });
  _teardownVADOnly();
  state.turnOpen = false; // ensure local state is clean
  state.recStartedAt = 0;
  state.ttsPlaying = false;
  _emitVoiceState('idle');
}

function _bargeIn() {
  // Soft barge-in: pause audio locally
  _clearBargeConfirm(false);
  try { stopPlayback(); } catch {}
  // If a prior ASR turn is somehow still open, politely close it.
  // (Harmless if no turn is open; guarded to avoid duplicate closes.)
  if (state.turnOpen) {
    try { sendCloseStream(); } catch {}
    state.turnOpen = false;
  }
}

// ---- VAD wiring -------------------------------------------------------------

async function _arm(stream = null, opts = {}) {
  const mic = stream || await _ensureMic();

  // Build / rebuild VAD
  _teardownVADOnly();

  // Merge runtime globals so admins can tune without rebuilds:
  let globalVad = {};
  try { globalVad = (window.__askchip_config && window.__askchip_config.vad) || {}; } catch {}
  const cfg = { ...globalVad, ...opts };

  const pollMs = cfg.pollMs ?? 33;
  const vad = new VAD(
    state.analyser,
    {
      // Tunables (admin-configurable via opts or window.__askchip_config.vad)
      startRms: cfg.startRms ?? 0.012,
      stopRms:  cfg.stopRms  ?? 0.006,   // LOWER = less twitchy end
      minSpeechMs: cfg.minSpeechMs ?? 220,
      minSilenceMs: cfg.minSilenceMs ?? 900, // HIGHER = needs longer quiet
      pollMs,
      echoBoostStart: cfg.echoBoostStart ?? 1.5,
      echoBoostStop:  cfg.echoBoostStop  ?? 1.3,
      echoStateFn: () => {
        // treat "TTS is playing" as echo present
        try { return !!ttsIsPlaying(); } catch { return false; }
      }
    },
    {
      onSpeechStart: _onSpeechStartCommitted,
      onSpeechEnd: _onSpeechEndCommitted,
    }
  );

  state.vad = vad;
  state.vad.start();
  _logLifecycle('mic_start', {
    sampleRate: state.ctx?.sampleRate,
    pollMs,
  });
  _emitVoiceState('armed');

  return mic;
}

// ---- Recorder lifecycle -----------------------------------------------------

function _startRecorder() {
  if (!state.stream) return false;
  if (state.rec && state.rec.state === 'recording') return true; // guard duplicate starts

  if (typeof MediaRecorder === 'undefined') {
    console.warn('[voice] MediaRecorder not supported in this browser');
    state.rec = null;
    return false;
  }

  state.chunkSendPromise = Promise.resolve();
  state.chunkBytesSent = 0;
  state.chunkSendError = null;
  _clearPendingEndTimer();               // NEW: clear any delayed-end from prior turn
  state.recStartedAt = performance.now();// NEW: start timestamp for min-turn gate

  let recorder;
  try {
    recorder = new MediaRecorder(state.stream, { mimeType: REC_MIME, audioBitsPerSecond: 128000 });
  } catch (primaryErr) {
    try {
      recorder = new MediaRecorder(state.stream); // fallback, browser picks best
    } catch (fallbackErr) {
      console.warn('[voice] MediaRecorder init failed', fallbackErr || primaryErr);
      state.rec = null;
      return false;
    }
  }

  state.rec = recorder;

  state.rec.ondataavailable = (e) => {
    const blob = e.data;
    if (!blob || blob.size < MIN_VALID_BLOB_BYTES) {
      return;
    }

    // Chain chunk sends to preserve ordering across the WS.
    state.chunkSendPromise = state.chunkSendPromise
      .catch(() => {}) // allow queue to continue even if a prior chunk failed
      .then(async () => {
        try {
          await sendAudioChunk(blob);
          state.chunkBytesSent += blob.size;
          try {
            console.debug('[voice] streamed audio chunk', { bytes: blob.size });
          } catch {}
        } catch (err) {
          state.chunkSendError = err;
          try {
            console.warn('[voice] failed to stream audio chunk', err);
          } catch {}
        }
      });
  };

  state.rec.onstop = async () => {
    let finalDetail;
    try {
      await state.chunkSendPromise.catch((err) => {
        state.chunkSendError = state.chunkSendError || err;
      });
      if (state.chunkBytesSent < MIN_VALID_BLOB_BYTES && !state.chunkSendError) {
        console.warn('[voice] recorded chunks too small', state.chunkBytesSent);
        finalDetail = { statusText: 'Listening… (heard silence — please try again)' };
      }
    } catch (e) {
      console.warn('[voice] send audio failed', e);
      state.chunkSendError = state.chunkSendError || e;
    } finally {
      if (state.chunkSendError && !finalDetail) {
        finalDetail = { statusText: 'Listening… (audio send failed — please try again)' };
      }
      if (state.chunkSendError || state.chunkBytesSent < MIN_VALID_BLOB_BYTES) {
        try {
          console.warn('[voice] recorder stopped with issues', {
            bytesSent: state.chunkBytesSent,
            error: state.chunkSendError,
          });
        } catch {}
      } else {
        try {
          console.debug('[voice] recorder stopped', {
            bytesSent: state.chunkBytesSent,
            // state.rec may be nulled by _stopRecorder; fall back to selected REC_MIME
            mime: (state.rec && state.rec.mimeType) || REC_MIME,
          });
        } catch {}
      }
      // IMPORTANT: close the turn *after* the blob has been sent to preserve ordering.
      if (state.turnOpen) {
        try {
          await sendCloseStream();
        } catch {}
        state.turnOpen = false;
      }
      _emitVoiceState('armed', finalDetail);
    }
  };

  try {
    // Small timeslice ensures non-empty dataavailable frames while still producing a single turn blob.
    const timeslice = 150; // 150 ms sits comfortably within the 100–200 ms target window
    state.rec.start(timeslice);
    state.turnOpen = true; // mark an open ASR turn on the server
    try {
      console.debug('[voice] recorder started', { mime: state.rec.mimeType, timeslice });
    } catch {}
  } catch (e) {
    console.warn('[voice] recorder start failed', e);
    state.rec = null;
    state.turnOpen = false;
    return false;
  }

  // Safety timeout to prevent runaway recordings
  const limitMs = Number(optsFromGlobal('max_turn_seconds', 90)) * 1000 || DEFAULT_MAX_TURN_MS;
  _safeClearTurnTimer();
  state.turnTimer = setTimeout(() => {
    try { _onSpeechEndCommitted({ reason: 'turn_timeout' }); } catch {}
  }, limitMs);

  return true;
}

function _onSpeechStartCommitted() {
  _logLifecycle('vad_speech_start');

  if (state.ttsPlaying && !state.bargeConfirmActive) {
    state.bargeConfirmActive = true;
    try { pausePlayback(); } catch {}
    state.bargeConfirmTimer = setTimeout(() => {
      state.bargeConfirmTimer = null;
      if (!state.bargeConfirmActive) return;
      if (state.vad && typeof state.vad.isRecording === 'function' && !state.vad.isRecording()) {
        state.bargeConfirmActive = false;
        try { resumePlayback(); } catch {}
        return;
      }
      state.bargeConfirmActive = false;
      _bargeIn();
      const started = _startRecorder();
      if (started) {
        _emitVoiceState('recording');
        return;
      }
      console.warn('[voice] recorder unavailable — reverting to typing');
      _emitVoiceState('armed', { statusText: 'Listening… (mic unavailable — please type)' });
    }, bargeConfirmMs);
    return;
  }

  if (state.bargeConfirmActive) {
    return;
  }

  _bargeIn();

  const started = _startRecorder();
  if (started) {
    _emitVoiceState('recording');
    return;
  }

  console.warn('[voice] recorder unavailable — reverting to typing');
  _emitVoiceState('armed', { statusText: 'Listening… (mic unavailable — please type)' });
}

function _onSpeechEndCommitted(detail = null) {
  const reason = detail?.reason || 'vad_silence';
  const now = performance.now ? performance.now() : Date.now();
  const minTurnMs = Number(optsFromGlobal('min_turn_ms', 1200)); // NEW: min turn length (default 1.2s)

  if (state.bargeConfirmActive) {
    _clearBargeConfirm(true);
  }

  // If we haven't recorded at least minTurnMs, delay honoring VAD-end.
  // Only applies while recorder is actually running.
  if (state.rec && typeof state.rec.state === 'string' && state.rec.state === 'recording') {
    const elapsed = Math.max(0, now - (state.recStartedAt || now));
    const wait = Math.max(0, minTurnMs - elapsed);
    if (wait > 0) {
      try { console.debug('[voice] delaying VAD end', { waitMs: wait, elapsed }); } catch {}
      _clearPendingEndTimer();
      state.pendingEndTimer = setTimeout(() => _onSpeechEndCommitted(detail), wait);
      return; // do not stop yet
    }
  }

  _logLifecycle('vad_speech_end', { reason });
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _stopRecorder({ reason });
  // Do NOT send CloseStream here; we send it in rec.onstop AFTER the blob is delivered.
}

// ---- Utilities --------------------------------------------------------------

function optsFromGlobal(key, fallback) {
  // Allow admin-configurable values to seep in (if app exposes them)
  try {
    const cfg = window.__askchip_config || {};
    if (key in cfg) return cfg[key];
  } catch {}
  return fallback;
}
