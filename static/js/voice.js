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
import { sendAudioChunk, sendCloseStream, sendJSON } from './ws_module.js';
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
const PRE_ROLL_MS = 250;            // ~0.25s of pre-roll audio

const state = {
  stream: null,
  ctx: null,
  source: null,
  analyser: null,
  vad: null,
  rec: null,
  finalized: false,
  postFinalHoldUntil: 0,
  wsListener: null,
  chunkSendPromise: Promise.resolve(),
  chunkBytesSent: 0,
  chunkSendError: null,
  turnTimer: null,
  turnOpen: false,   // track whether a turn is currently open server-side
  turnClosePromise: null,
  turnHintSent: false,
  deviceLogged: false,
  // NEW: min-turn gating
  recStartedAt: 0,
  pendingEndTimer: null,
  ttsPlaying: false,
  bargeConfirmTimer: null,
  bargeConfirmActive: false,
  // Pre-roll tap state
  preRollNode: null,
  preRollGain: null,
  preRollBlobs: [],
  preRollDurationMs: 0,
  preRollLastTimecode: null,
  preRollTimeslice: 150,
  recStreaming: false,
  recStopping: false,
  recStopShouldSend: false,
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

function _ensureWSListener() {
  if (state.wsListener || typeof window === 'undefined') {
    return;
  }
  const handler = async (ev) => {
    const detail = ev?.detail || {};
    const type = detail?.type;
    const typeNorm = typeof type === 'string' ? type.toLowerCase() : '';

    let isFinal = false;
    if (typeNorm === 'utteranceend') {
      isFinal = true;
    } else if (typeNorm === 'results' || typeNorm === 'result') {
      const channelFinal = detail?.channel?.is_final === true;
      const payloadFinal = detail?.is_final === true;
      isFinal = channelFinal || payloadFinal;
    }

    if (!isFinal || state.finalized) {
      return;
    }

    _applyPostFinalHold('ws_final');

    const recorder = state.rec;
    const isRecording = !!(recorder && typeof recorder.state === 'string' && recorder.state !== 'inactive');
    if (!isRecording) {
      return;
    }

    try {
      _stopRecorder({ reason: 'server_final' });
    } catch (err) {
      try { console.warn('[voice] failed to stop recorder on server final', err); } catch {}
    }

    try {
      await Promise.resolve(state.chunkSendPromise).catch(() => {});
    } catch (err) {
      try { console.warn('[voice] chunk send did not settle after server final', err); } catch {}
    }
  };

  try { window.addEventListener('askchip-ws', handler); } catch {}
  state.wsListener = handler;
}

function _removeWSListener() {
  if (!state.wsListener || typeof window === 'undefined') {
    return;
  }
  try { window.removeEventListener('askchip-ws', state.wsListener); } catch {}
  state.wsListener = null;
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
  await _setupPreRollTap(ctx, source);

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

function _closeTurnIfOpen() {
  if (!state.turnOpen && !state.turnClosePromise) {
    return null;
  }
  if (state.turnClosePromise) {
    return state.turnClosePromise;
  }
  if (!state.turnOpen) {
    return null;
  }
  const closePromise = (async () => {
    try {
      const closeFrame = { type: 'CloseStream' };
      _logLifecycle('turn_close_signal', { frame: closeFrame }, 'info');
      await sendCloseStream();
    } finally {
      state.turnOpen = false;
      state.turnClosePromise = null;
    }
  })();
  state.turnClosePromise = closePromise;
  return closePromise;
}

async function _setupPreRollTap(ctx, source) {
  _teardownPreRollTap();

  if (!ctx || !source) {
    _resetPreRollBuffer();
    return;
  }

  _resetPreRollBuffer();

  const worklet = ctx.audioWorklet;
  if (!worklet || typeof worklet.addModule !== 'function') {
    // AudioWorklet unavailable; gracefully degrade without pre-roll.
    return;
  }

  try {
    const moduleUrl = new URL('./voice/pre_roll_processor.js', import.meta.url);
    await worklet.addModule(moduleUrl);
  } catch (err) {
    try { console.warn('[voice] failed to load pre-roll worklet', err); } catch {}
    return;
  }

  try {
    const node = new AudioWorkletNode(ctx, 'pre-roll-processor', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      outputChannelCount: [1],
    });
    // Preserve the tap for VAD/visualization without buffering PCM samples.
    node.port.onmessage = null;
    const silentGain = ctx.createGain();
    silentGain.gain.value = 0;
    source.connect(node);
    node.connect(silentGain);
    if (ctx.destination) {
      silentGain.connect(ctx.destination);
    }
    state.preRollNode = node;
    state.preRollGain = silentGain;
  } catch (err) {
    try { console.warn('[voice] pre-roll worklet unavailable', err); } catch {}
    _teardownPreRollTap();
  }
}

function _teardownPreRollTap() {
  if (state.preRollNode) {
    try { state.preRollNode.port.onmessage = null; } catch {}
    try { state.preRollNode.disconnect(); } catch {}
  }
  if (state.preRollGain) {
    try { state.preRollGain.disconnect(); } catch {}
  }
  state.preRollNode = null;
  state.preRollGain = null;
  _resetPreRollBuffer();
}

function _resetPreRollBuffer() {
  state.preRollBlobs = [];
  state.preRollDurationMs = 0;
  state.preRollLastTimecode = null;
}

function _computePreRollDuration(timecode) {
  const timeslice = state.preRollTimeslice || 0;
  let duration = timeslice || PRE_ROLL_MS;
  if (Number.isFinite(timecode)) {
    const last = state.preRollLastTimecode;
    if (Number.isFinite(last)) {
      duration = Math.max(0, timecode - last);
    } else if (timecode > 0) {
      duration = timecode;
    }
    state.preRollLastTimecode = timecode;
  }
  if (!Number.isFinite(duration) || duration <= 0) {
    duration = timeslice || PRE_ROLL_MS;
  }
  return duration;
}

function _bufferPreRollChunk(entry) {
  if (!entry || !entry.blob) {
    return;
  }
  const chunk = {
    blob: entry.blob,
    durationMs: Number.isFinite(entry.durationMs) ? Math.max(0, entry.durationMs) : 0,
    timecode: Number.isFinite(entry.timecode) ? entry.timecode : null,
  };
  state.preRollBlobs.push(chunk);
  state.preRollDurationMs += chunk.durationMs;
  while (state.preRollDurationMs > PRE_ROLL_MS && state.preRollBlobs.length > 1) {
    const removed = state.preRollBlobs.shift();
    state.preRollDurationMs -= removed?.durationMs || 0;
  }
  if (state.preRollDurationMs < 0) {
    state.preRollDurationMs = 0;
  }
}

function _enqueuePreRollBlobs() {
  const queued = state.preRollBlobs ? [...state.preRollBlobs] : [];
  const durationMs = queued.reduce((sum, chunk) => sum + (chunk?.durationMs || 0), 0);
  const totalBytes = queued.reduce((sum, chunk) => sum + (chunk?.blob?.size || 0), 0);
  const count = queued.length;
  _resetPreRollBuffer();
  for (const chunk of queued) {
    if (!chunk?.blob) continue;
    _sendRecorderChunk(chunk.blob, {
      preRoll: true,
      durationMs: chunk.durationMs,
      timecode: chunk.timecode,
    });
  }
  return { count, durationMs, totalBytes };
}

function _sendRecorderChunk(blob, meta = {}) {
  if (!blob || blob.size < MIN_VALID_BLOB_BYTES) {
    return;
  }
  const { preRoll = false, durationMs = null, timecode = null } = meta || {};
  const logLabel = preRoll ? 'streamed pre-roll chunk' : 'streamed audio chunk';
  state.chunkSendPromise = state.chunkSendPromise
    .catch(() => {})
    .then(async () => {
      try {
        await sendAudioChunk(blob);
        state.chunkBytesSent += blob.size;
        try {
          console.debug('[voice]', logLabel, {
            bytes: blob.size,
            durationMs,
            timecode,
            mime: blob.type,
          });
        } catch {}
      } catch (err) {
        state.chunkSendError = err;
        try {
          console.warn('[voice] failed to stream audio chunk', err);
        } catch {}
      }
    });
}

function _primeRecorderForPreRoll(options = {}) {
  const { resetBuffer = true } = options || {};
  if (!state.stream) {
    return false;
  }
  if (typeof MediaRecorder === 'undefined') {
    console.warn('[voice] MediaRecorder not supported in this browser');
    state.rec = null;
    return false;
  }
  if (state.rec && state.rec.state === 'recording') {
    if (resetBuffer) {
      _resetPreRollBuffer();
    }
    return true;
  }

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
  state.recStreaming = false;
  state.recStopping = false;
  state.recStopShouldSend = false;
  state.turnHintSent = false;
  if (resetBuffer) {
    _resetPreRollBuffer();
  }

  const timeslice = state.preRollTimeslice || 150;
  recorder.ondataavailable = _handleRecorderData;
  recorder.onstop = async () => {
    state.turnHintSent = false;
    state.recStreaming = false;
    state.recStopping = false;
    state.recStopShouldSend = false;
    state.rec = null;
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
            mime: (recorder && recorder.mimeType) || REC_MIME,
          });
        } catch {}
      }
      const pendingClose = _closeTurnIfOpen();
      if (pendingClose) {
        try {
          await pendingClose;
        } catch {}
      }
      _emitVoiceState('armed', finalDetail);
      if (state.vad && state.stream && state.stream.active) {
        try { _primeRecorderForPreRoll(); } catch (err) { try { console.warn('[voice] failed to re-prime recorder', err); } catch {} }
      }
    }
  };

  try {
    recorder.start(timeslice);
    state.preRollTimeslice = timeslice;
    try {
      console.debug('[voice] recorder primed', { mime: recorder.mimeType, timeslice });
    } catch {}
  } catch (err) {
    console.warn('[voice] recorder start failed', err);
    state.rec = null;
    return false;
  }

  return true;
}

function _handleRecorderData(event) {
  if (!event) {
    return;
  }
  if (state.finalized) {
    return;
  }
  const blob = event.data;
  if (!blob || blob.size < MIN_VALID_BLOB_BYTES) {
    return;
  }

  const timecode = Number.isFinite(event.timecode) ? event.timecode : null;

  if (state.recStopping && !state.recStopShouldSend) {
    return;
  }

  if (state.recStopping && state.recStopShouldSend) {
    state.recStopShouldSend = false;
    state.recStopping = false;
    _sendRecorderChunk(blob, { preRoll: false, durationMs: null, timecode });
    return;
  }

  if (state.recStreaming) {
    _sendRecorderChunk(blob, { preRoll: false, durationMs: null, timecode });
    return;
  }

  const durationMs = _computePreRollDuration(timecode);
  _bufferPreRollChunk({ blob, durationMs, timecode });
}

function _stopRecorder(detail = null) {
  const recorder = state.rec;
  const wasActive = !!recorder && recorder.state !== 'inactive';
  const payload = Object.assign({
    active: wasActive,
    hasRecorder: !!recorder,
  }, detail || {});
  _logLifecycle('mic_stop', payload, wasActive ? 'debug' : 'info');

  if (detail?.reason === 'server_final') {
    _applyPostFinalHold('stop_recorder');
  } else if (state.finalized) {
    if (!recorder || recorder.state === 'inactive') {
      state.rec = null;
      state.turnHintSent = false;
      return;
    }
  }

  if (!recorder) {
    state.rec = null;
    state.turnHintSent = false;
    return;
  }

  if (recorder.state === 'inactive') {
    state.rec = null;
    state.turnHintSent = false;
    return;
  }

  const shouldSendFinal = !!state.recStreaming;
  state.recStopShouldSend = shouldSendFinal;
  state.recStopping = true;
  state.recStreaming = false;
  if (!shouldSendFinal) {
    _resetPreRollBuffer();
  }

  try {
    _logLifecycle('recorder_stop_invoked', {
      reason: detail?.reason || null,
    }, 'info');
    try { console.debug('[voice] recorder.stop()'); } catch {}     
    recorder.stop();
  } catch {}
  // intentionally keep state.rec reference nullable here; onstop handler handles final close
  state.rec = null;
  state.turnHintSent = false;
}

function _teardownVADOnly() {
  try { state.vad && state.vad.stop(); } catch {}
  state.vad = null;
}

function _teardownAudioGraph() {
  _teardownPreRollTap();
  try { state.source && state.source.disconnect(); } catch {}
  try { state.analyser && state.analyser.disconnect(); } catch {}
  try { state.ctx && state.ctx.close && state.ctx.close(); } catch {}
  state.source = null;
  state.analyser = null;
  state.ctx = null;
  state.deviceLogged = false;
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  _removeWSListener();
}

function _disarm() {
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _clearBargeConfirm(false);
  _stopRecorder({ reason: 'manual_disarm' });
  _teardownVADOnly();
  state.turnOpen = false; // ensure local state is clean
  state.turnClosePromise = null;
  state.recStartedAt = 0;
  state.ttsPlaying = false;
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  _removeWSListener();
  _emitVoiceState('idle');
}

function _bargeIn() {
  // Soft barge-in: pause audio locally
  _clearBargeConfirm(false);
  try { stopPlayback(); } catch {}
  // If a prior ASR turn is somehow still open, politely close it.
  // (Harmless if no turn is open; guarded to avoid duplicate closes.)
  const pendingClose = _closeTurnIfOpen();
  if (pendingClose) {
    pendingClose.catch(() => {});
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

  _primeRecorderForPreRoll();

  return mic;
}

// ---- Recorder lifecycle -----------------------------------------------------

function _startRecorder() {
  if (!state.stream) return false;

  const primed = _primeRecorderForPreRoll({ resetBuffer: false });
  if (!primed || !state.rec || state.rec.state !== 'recording') {
    return false;
  }

  if (state.recStreaming) {
    return true;
  }

  state.chunkSendPromise = Promise.resolve();
  state.chunkBytesSent = 0;
  state.chunkSendError = null;
  state.turnClosePromise = null;
  _clearPendingEndTimer();
  state.recStartedAt = performance.now ? performance.now() : Date.now();
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.recStreaming = true;
  state.recStopping = false;
  state.recStopShouldSend = false;
  _ensureWSListener();

  if (!state.turnHintSent) {
    const recorderMime = (state.rec && state.rec.mimeType) || REC_MIME;
    try {
      sendJSON({ type: 'AudioStart', mime: recorderMime });
    } catch (err) {
      try { console.warn('[voice] failed to send AudioStart hint', err); } catch {}
    }
    state.turnHintSent = true;
  }

  const preRollStats = _enqueuePreRollBlobs();
  if (preRollStats?.count) {
    try {
      console.debug('[voice] flushed pre-roll buffer', {
        chunks: preRollStats.count,
        durationMs: preRollStats.durationMs,
        bytes: preRollStats.totalBytes,
      });
    } catch {}
  }

  state.turnOpen = true;
  try {
    console.debug('[voice] recorder streaming', {
      mime: (state.rec && state.rec.mimeType) || REC_MIME,
    });
  } catch {}

  const limitMs = Number(optsFromGlobal('max_turn_seconds', 90)) * 1000 || DEFAULT_MAX_TURN_MS;
  _safeClearTurnTimer();
  state.turnTimer = setTimeout(() => {
    try { _onSpeechEndCommitted({ reason: 'turn_timeout' }); } catch {}
  }, limitMs);

  return true;
}

function _onSpeechStartCommitted() {
  const bufferedMsRaw = Number.isFinite(state.preRollDurationMs) ? state.preRollDurationMs : 0;
  const preRollBlobs = state.preRollBlobs || [];
  const totalBytes = preRollBlobs.reduce((sum, chunk) => sum + (chunk?.blob?.size || 0), 0);
  const round = (v) => {
    if (!Number.isFinite(v)) return 0;
    return Math.round(v * 100) / 100;
  };
  _logLifecycle('vad_speech_start', {
    preRollBufferedMs: round(bufferedMsRaw),
    preRollSentMs: round(Math.min(bufferedMsRaw, PRE_ROLL_MS)),
    preRollChunks: preRollBlobs.length,
    preRollBytes: totalBytes,
    preRollEnabled: preRollBlobs.length > 0,
    preRollMime: (state.rec && state.rec.mimeType) || REC_MIME,
  });

  const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
    ? performance.now()
    : Date.now();
  const holdUntil = state.postFinalHoldUntil || 0;

  if (state.finalized) {
    if (now < holdUntil) {
      _logLifecycle('vad_speech_start_suppressed', {
        reason: 'post_final_hold_finalized',
        holdUntil,
        now,
      });
      return;
    }
    state.finalized = false;
  }

  if (now < holdUntil) {
    _logLifecycle('vad_speech_start_suppressed', {
      reason: 'post_final_hold',
      holdUntil,
      now,
    });
    return;
  }

  if (state.postFinalHoldUntil) {
    state.postFinalHoldUntil = 0;
  }

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

  try { console.debug('[voice] vad_speech_end'); } catch {}   

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

  _logLifecycle('vad_speech_end', { reason }, 'info');
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

function _applyPostFinalHold(source = 'unknown') {
  const rawHold = Number(optsFromGlobal('post_final_hold_ms', 600));
  const holdMs = Number.isFinite(rawHold) ? Math.max(0, rawHold) : 0;
  const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
    ? performance.now()
    : Date.now();
  const targetUntil = now + holdMs;
  const previousUntil = state.postFinalHoldUntil || 0;
  const nextUntil = Math.max(targetUntil, previousUntil);
  const wasFinalized = !!state.finalized;

  state.finalized = true;
  state.postFinalHoldUntil = nextUntil;

  if (!wasFinalized || nextUntil !== previousUntil) {
    _logLifecycle('post_final_hold_applied', {
      holdMs,
      holdUntil: nextUntil,
      source,
    });
  }

  return nextUntil;
}

export const __TEST_ONLY__ = {
  state,
  startRecorder: _startRecorder,
  stopRecorder: _stopRecorder,
  ensureWSListener: _ensureWSListener,
  closeTurnIfOpen: _closeTurnIfOpen,
};
