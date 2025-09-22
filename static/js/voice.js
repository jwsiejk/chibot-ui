/*
Citations for context (non-functional):
:contentReference[oaicite:0]{index=0}
:contentReference[oaicite:1]{index=1}
*/

/* static/js/voice.js — Production voice pipeline (VAD + one-turn recorder + WS)
   Goals satisfied:
    • Echo-aware VAD (threshold boost while TTS is playing)
    • One Opus blob per user turn (prefers OGG/Opus when supported; falls back to WebM/Opus)
    • Soft barge-in: pause Chip TTS on committed speech start
    • Turn timeout (safety), robust errors, clean session end
    • UI state events: 'askchip-voice' {state:'armed'|'recording'|'idle'}

   Notes:
    • Do NOT JSON-wrap audio; send raw binary via ws.send(ArrayBuffer) (see ws.js).
    • CloseStream is emitted AFTER the blob is successfully queued to the socket.
*/

import { VAD } from './voice/vad.js';
import { sendAudioChunk, sendCloseStream } from './ws.js';
import { stopPlayback, isPlaying as ttsIsPlaying } from './audio.js';

// Public API (matches prior usage)
export async function initMic() { return await _ensureMic(); }
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
const MIN_VALID_BLOB_BYTES = 256;   // drop clearly-empty/prelude-only blobs

const state = {
  stream: null,
  ctx: null,
  source: null,
  analyser: null,
  vad: null,
  rec: null,
  recChunks: [],
  turnTimer: null,
  turnOpen: false,   // track whether a turn is currently open server-side
};

// ---- Helpers ----------------------------------------------------------------

function _emitVoiceState(s) {
  try { window.dispatchEvent(new CustomEvent('askchip-voice', { detail: { state: s }})); } catch {}
}

async function _ensureMic() {
  if (state.stream && state.stream.active) return state.stream;

  // Request a clean mono stream with echo/noise controls
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      sampleRate: 48000,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false
    }
  });

  // Build WebAudio chain
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC({ sampleRate: 48000 });
  if (ctx.state === 'suspended') { try { await ctx.resume(); } catch {} }

  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.03;
  source.connect(analyser);

  state.stream = stream;
  state.ctx = ctx;
  state.source = source;
  state.analyser = analyser;

  return stream;
}

function _safeClearTurnTimer() {
  if (state.turnTimer) { clearTimeout(state.turnTimer); state.turnTimer = null; }
}

function _stopRecorder() {
  try { if (state.rec && state.rec.state !== 'inactive') state.rec.stop(); } catch {}
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
}

function _disarm() {
  _safeClearTurnTimer();
  _stopRecorder();
  _teardownVADOnly();
  state.turnOpen = false; // ensure local state is clean
  _emitVoiceState('idle');
}

function _bargeIn() {
  // Soft barge-in: pause audio locally
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

  const vad = new VAD(
    state.analyser,
    {
      // Tunables (admin-configurable via opts or window.__askchip_config)
      startRms: opts.startRms ?? 0.015,
      stopRms: opts.stopRms ?? 0.010,
      minSpeechMs: opts.minSpeechMs ?? 220,
      minSilenceMs: opts.minSilenceMs ?? 420,
      pollMs: opts.pollMs ?? 33,
      echoBoostStart: opts.echoBoostStart ?? 1.5,
      echoBoostStop: opts.echoBoostStop ?? 1.3,
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
  _emitVoiceState('armed');

  return mic;
}

// ---- Recorder lifecycle -----------------------------------------------------

function _startRecorder() {
  if (!state.stream) return;
  if (state.rec && state.rec.state === 'recording') return; // guard duplicate starts

  state.recChunks = [];
  try {
    state.rec = new MediaRecorder(state.stream, { mimeType: REC_MIME, audioBitsPerSecond: 128000 });
  } catch {
    state.rec = new MediaRecorder(state.stream); // fallback, browser picks best
  }

  state.rec.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) state.recChunks.push(e.data);
  };

  state.rec.onstop = async () => {
    try {
      const blob = new Blob(state.recChunks, { type: REC_MIME });
      state.recChunks = [];
      // Drop obviously-empty/prelude-only blobs (rec preambles can be tiny)
      if (blob.size >= MIN_VALID_BLOB_BYTES) {
        // One blob per user turn → WS → server STT
        await sendAudioChunk(blob);
      }
    } catch (e) {
      console.warn('[voice] send audio failed', e);
    } finally {
      // IMPORTANT: close the turn *after* the blob has been sent to preserve ordering.
      if (state.turnOpen) {
        try { sendCloseStream(); } catch {}
        state.turnOpen = false;
      }
      _emitVoiceState('armed');
    }
  };

  try {
    // Small timeslice ensures non-empty dataavailable frames while still producing a single turn blob.
    state.rec.start(250);
    state.turnOpen = true; // mark an open ASR turn on the server
  } catch (e) {
    console.warn('[voice] recorder start failed', e);
    state.rec = null;
    state.turnOpen = false;
    return;
  }

  // Safety timeout to prevent runaway recordings
  const limitMs = Number(optsFromGlobal('max_turn_seconds', 90)) * 1000 || DEFAULT_MAX_TURN_MS;
  _safeClearTurnTimer();
  state.turnTimer = setTimeout(() => {
    try { _onSpeechEndCommitted(); } catch {}
  }, limitMs);
}

function _onSpeechStartCommitted() {
  // Pause Chip TTS; if a previous ASR turn somehow remained open, close it.
  _bargeIn();

  _emitVoiceState('recording');
  _startRecorder();
}

function _onSpeechEndCommitted() {
  _safeClearTurnTimer();
  _stopRecorder();
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
