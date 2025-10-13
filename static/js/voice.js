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
import { stopPlayback, isPlaying as ttsIsPlaying, getPlaybackSignature } from './audio.js';
import { logIfEnabled } from './util/logging.js';
import { getSID } from './util/sid.js';

// Public API (matches prior usage)
export async function initMic(stream = null) { return await _ensureMic(stream); }
export async function armVAD(stream = null, opts = {}) { return await _arm(stream, opts); }
export function disarmVAD() { _disarm(); }
export function isRecording() { return !!(state.rec && state.rec.state === 'recording'); }
export function bargeIn() { try { console.info('barge_in'); console.info('tts_pause'); } catch {} try { console.info('barge_in'); console.info('tts_pause'); } catch {} try { console.info('barge_in'); console.info('tts_pause'); } catch {} try { console.info('barge_in'); console.info('tts_pause'); } catch {} _bargeIn(); }         // keeps API parity
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
const POST_TTS_HOLDOFF_MS = 600;    // grace window after Chip begins speaking

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
  postTtsHoldUntil: 0,
  postTtsHoldTimer: null,
  eligibility: 'blocked_pregreet', // 'blocked_pregreet' | 'holdoff' | 'eligible'
  refractoryUntil: 0,
  direct: {
    descriptor: null,
    fetchPromise: null,
    connectPromise: null,
    ws: null,
    ready: false,
    sendPromise: Promise.resolve(),
    lastError: null,
    container: 'webm',
    codec: 'opus',
    containerized: true,
    sanitizedUrl: null,
  },
};

let _overrideEchoSignatureFn = null;

// ---- Helpers ----------------------------------------------------------------

function _emitVoiceState(state, detail = {}) {
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice', { detail: { state, ...detail } }));
  } catch {}
}

function _console(level, ...args) {
  logIfEnabled(() => {
    try {
      const method = (typeof console?.[level] === 'function') ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
}

function _logLifecycle(event, detail = {}, level = 'debug') {
  const payload = { event, ...(detail && typeof detail === 'object' ? detail : { detail }) };
  _console(level, '[voice]', event, payload);
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice-lifecycle', { detail: payload }));
  } catch {}
}

function _now() {
  try {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
      return performance.now();
    }
  } catch {}
  return Date.now();
}

function _clearPendingEndTimer() {
  if (state.pendingEndTimer) {
    try { clearTimeout(state.pendingEndTimer); } catch {}
    state.pendingEndTimer = null;
  }
}

function _clearPostTtsHoldTimer() {
  const hadTimer = !!state.postTtsHoldTimer;
  if (hadTimer) {
    try { clearTimeout(state.postTtsHoldTimer); } catch {}
  }
  state.postTtsHoldTimer = null;
  return hadTimer;
}

async function _fetchAsrClientSession(force = false) {
  const direct = state.direct;
  if (!direct) return null;
  if (!force && direct.descriptor) return direct.descriptor;
  if (direct.fetchPromise) return direct.fetchPromise;

  const sid = (() => {
    try { return getSID(); } catch { return null; }
  })();
  const qs = sid ? `?session_id=${encodeURIComponent(sid)}` : '';

  const url = `/api/v1/asr/client-session${qs}`;
  direct.fetchPromise = fetch(url, { credentials: 'include' })
    .then(async (res) => {
      if (!res.ok) {
        const err = new Error(`fetch_failed_${res.status}`);
        err.status = res.status;
        throw err;
      }
      return res.json();
    })
    .then((body) => {
      const session = body?.session || body?.descriptor || null;
      if (!session || typeof session !== 'object') {
        throw new Error('bad_descriptor');
      }
      direct.descriptor = session;
      direct.container = (session?.transport?.container || 'webm');
      direct.codec = (session?.transport?.codec || 'opus');
      direct.containerized = session?.transport?.containerized !== false;
      direct.sanitizedUrl = session?.sanitized_url || session?.url || null;

      const diagContainer = `${direct.container || 'webm'}/${direct.codec || 'opus'}`;
      _console('info', '[voice] direct ASR descriptor', {
        sanitizedUrl: direct.sanitizedUrl,
        container: diagContainer,
        containerized: direct.containerized,
      });

      const diagFrame = {
        type: 'ClientAsrSession',
        sanitized_url: direct.sanitizedUrl || null,
        container: diagContainer,
        containerized: direct.containerized !== false,
      };
      try { sendJSON(diagFrame); } catch {}
      return session;
    })
    .catch((err) => {
      direct.lastError = err;
      throw err;
    })
    .finally(() => {
      direct.fetchPromise = null;
    });

  return direct.fetchPromise;
}

function _handleDirectWsMessage(evt) {
  if (!evt) return;
  const data = evt.data;
  if (typeof data !== 'string') {
    return;
  }
  try {
    const parsed = JSON.parse(data);
    _console('debug', '[voice] direct ASR frame', parsed);
  } catch (err) {
    _console('debug', '[voice] direct ASR frame (raw)', { error: err?.message || err });
  }
}

async function _ensureDirectWs() {
  const direct = state.direct;
  if (!direct || !direct.descriptor) return null;
  if (direct.ws && direct.ws.readyState === WebSocket.OPEN) return direct.ws;
  if (direct.connectPromise) return direct.connectPromise;

  if (typeof WebSocket === 'undefined') {
    return null;
  }

  const descriptor = direct.descriptor;
  const wsUrl = descriptor?.url;
  if (!wsUrl || typeof wsUrl !== 'string') {
    return null;
  }

  const protocols = (() => {
    const raw = descriptor?.protocols;
    if (Array.isArray(raw)) {
      return raw.filter((item) => typeof item === 'string' && item.trim()).map((item) => item.trim());
    }
    if (typeof raw === 'string' && raw.trim()) {
      return [raw.trim()];
    }
    return [];
  })();

  direct.connectPromise = new Promise((resolve, reject) => {
    let ws;
    try {
      ws = protocols.length ? new WebSocket(wsUrl, protocols) : new WebSocket(wsUrl);
    } catch (err) {
      direct.lastError = err;
      direct.connectPromise = null;
      return reject(err);
    }

    direct.ws = ws;
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      direct.ready = true;
      try {
        if (descriptor?.configure) {
          ws.send(JSON.stringify(descriptor.configure));
        }
      } catch (err) {
        _console('warn', '[voice] failed to send direct ASR configure', err);
      }
      resolve(ws);
    };

    ws.onmessage = (evt) => {
      try { _handleDirectWsMessage(evt); } catch {}
    };

    ws.onerror = (evt) => {
      direct.lastError = evt;
    };

    ws.onclose = () => {
      direct.ready = false;
      direct.ws = null;
    };
  }).finally(() => {
    direct.connectPromise = null;
  });

  return direct.connectPromise.catch(() => null);
}

async function _directSendChunk(blob) {
  const direct = state.direct;
  if (!direct) return;
  const ws = direct.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    const buf = await blob.arrayBuffer();
    ws.send(buf);
  } catch (err) {
    direct.lastError = err;
    _console('warn', '[voice] direct ASR send failed', err);
  }
}

function _closeDirectWs() {
  const direct = state.direct;
  if (!direct) return;
  const ws = direct.ws;
  direct.ws = null;
  direct.ready = false;
  if (ws) {
    try { ws.close(); } catch {}
  }
}

try {
  window.addEventListener('chip-tts', (ev) => {
    const detail = ev?.detail || {};
    const rawState = detail.state;
    const ttsState = typeof rawState === 'string' ? rawState.toLowerCase() : '';
    if (ttsState === 'playing') {
      state.postTtsHoldUntil = _now() + POST_TTS_HOLDOFF_MS;

      const isPrime = detail && detail.prime === true;
      const playbackConfirmed = (() => {
        if (detail && (detail.confirmed === true || detail.playbackConfirmed === true)) {
          return true;
        }
        try { return !!ttsIsPlaying(); } catch { return false; }
      })();

      if (!isPrime && playbackConfirmed && state.eligibility === 'blocked_pregreet') {
        state.eligibility = 'holdoff';
      }
      return;
    }
    if (!ttsState || ttsState === 'ended' || ttsState === 'stopped' || ttsState === 'idle' || ttsState === 'paused') {
      state.postTtsHoldUntil = 0;
      _clearPostTtsHoldTimer();
      if (state.eligibility === 'holdoff') state.eligibility = 'eligible'; 
    }
  });
} catch {}

function _toFiniteNumber(value) {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function _resolveNumber(value, fallback) {
  const num = _toFiniteNumber(value);
  return num === null ? fallback : num;
}

function _selectRecorderMime() {
  try {
    const preferWebm = !!(state.direct && state.direct.descriptor && state.direct.containerized);
    const webmMime = 'audio/webm; codecs=opus';
    if (preferWebm && typeof MediaRecorder !== 'undefined' && typeof MediaRecorder.isTypeSupported === 'function') {
      if (MediaRecorder.isTypeSupported(webmMime)) {
        return webmMime;
      }
    }
  } catch {}
  return REC_MIME;
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
  // 1) Hard stop + clear all timers
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _clearPostTtsHoldTimer();

  // 2) Stop capture/VAD cleanly
  _stopRecorder({ reason: 'manual_disarm' });
  _teardownVADOnly();
  _closeDirectWs();

  // 3) Reset local state
  state.turnOpen = false;
  state.recStartedAt = 0;

  // 4) Block any pre-greet starts, and enforce a refractory lockout
  //    Use your configured cooldown (default 900ms) so we can't instantly re-arm.
  const cooldown = _resolveNumber(cfg.cooldownMs, 900);
  state.refractoryUntil = Date.now() + cooldown;  // prevent immediate re-starts
  state.postTtsHoldUntil = 0;                     // no pending hold
  state.eligibility = 'blocked_pregreet';         // require greet/tts to begin before starts are allowed

  // 5) Final UI state
  try { console.info('[voice] state=idle'); } catch {}
_emitVoiceState('idle');
}

function _bargeIn() {
  _clearPostTtsHoldTimer();
  state.postTtsHoldUntil = 0;
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

  try {
    _fetchAsrClientSession().then(() => {
      _ensureDirectWs().catch(() => {});
    }).catch(() => {});
  } catch {}

  // Merge runtime globals so admins can tune without rebuilds:
  let globalVad = {};
  try { globalVad = (window.__askchip_config && window.__askchip_config.vad) || {}; } catch {}
  const cfg = { ...globalVad, ...opts };

  const pollMs = _resolveNumber(cfg.pollMs, 33);
  const baseThresholdDb = _toFiniteNumber(cfg.baseThresholdDb ?? cfg.startDbOffset);
  const exitThresholdDb = _toFiniteNumber(cfg.exitThresholdDb ?? cfg.stopDbOffset);
  const ttsBoostDb = _toFiniteNumber(cfg.ttsBoostDb);
  const echoBoostStartDb = _resolveNumber(
    cfg.echoBoostStartDb ?? cfg.echoBoostStart ?? ttsBoostDb,
    8
  );
  const hasExplicitStartBoost = cfg.echoBoostStartDb !== undefined || cfg.echoBoostStart !== undefined;
  let stopFallback = 6;
  if (ttsBoostDb !== null || hasExplicitStartBoost) {
    stopFallback = echoBoostStartDb;
  }
  const echoBoostStopDb = _resolveNumber(
    cfg.echoBoostStopDb ?? cfg.echoBoostStop ?? (ttsBoostDb !== null ? ttsBoostDb : null),
    stopFallback
  );

  const echoSuppressDb = _resolveNumber(
    cfg.ECHO_SUPPRESS_DB ?? cfg.echoSuppressDb,
    15
  );

  const vadOpts = {
    // Tunables (admin-configurable via opts or window.__askchip_config.vad)
    minSpeechMs: _resolveNumber(cfg.minSpeechMs, 360),
    minSilenceMs: _resolveNumber(cfg.minSilenceMs, 900),
    cooldownMs:  _resolveNumber(cfg.cooldownMs, 900),     
    pollMs,
    startDbOffset: baseThresholdDb !== null ? baseThresholdDb : 10,
    stopDbOffset: exitThresholdDb !== null ? exitThresholdDb : 6,
    echoBoostStartDb,
    echoBoostStopDb,
    echoSuppressDb,
    echoSignatureFn: () => {
      if (typeof _overrideEchoSignatureFn === 'function') {
        try { return _overrideEchoSignatureFn(); } catch { return null; }
      }
      try { return getPlaybackSignature?.(); } catch { return null; }
    },
    echoStateFn: () => {
      // treat "TTS is playing" as echo present
      try { return !!ttsIsPlaying(); } catch { return false; }
    }
  };

  const startRms = _toFiniteNumber(cfg.startRms);
  if (startRms !== null) vadOpts.startRms = startRms;
  const stopRms = _toFiniteNumber(cfg.stopRms);
  if (stopRms !== null) vadOpts.stopRms = stopRms;

  const passthroughKeys = [
    'cooldownMs',
    'minStartDb',
    'minStopDb',
    'noiseFloorAlpha',
    'noiseFloorRiseAlpha',
    'noiseFloorGuardDb',
    'noiseFloorHangMs',
    'initialNoiseFloorDb',
  ];
  for (const key of passthroughKeys) {
    if (key in cfg) {
      const value = _toFiniteNumber(cfg[key]);
      if (value !== null) {
        vadOpts[key] = value;
      }
    }
  }

  const vad = new VAD(
    state.analyser,
    vadOpts,
    {
      onSpeechStart: _onSpeechStartCommitted,
      onSpeechEnd: _onSpeechEndCommitted,
      onSuppressed: (detail) => {
        const payload = Object.assign({ reason: 'echo' }, detail || {});
        _logLifecycle('vad_echo_suppressed', payload);
      },
    }
  );

  state.vad = vad;
  state.vad.start();
  _logLifecycle('mic_start', {
    sampleRate: state.ctx?.sampleRate,
    pollMs,
  });
  try { console.info('[voice] state=armed'); } catch {}
_emitVoiceState('armed');

  return mic;
}

// ---- Recorder lifecycle -----------------------------------------------------

function _startRecorder() {
  if (!state.stream) return false;
  if (state.rec && state.rec.state === 'recording') return true; // guard duplicate starts

  if (typeof MediaRecorder === 'undefined') {
    _console('warn', '[voice] MediaRecorder not supported in this browser');
    state.rec = null;
    return false;
  }

  state.chunkSendPromise = Promise.resolve();
  state.chunkBytesSent = 0;
  state.chunkSendError = null;
  _clearPendingEndTimer();               // NEW: clear any delayed-end from prior turn
  state.recStartedAt = performance.now();// NEW: start timestamp for min-turn gate

  let recorder;
  const mimeType = _selectRecorderMime();
  try { console.info('containerized=true'); } catch {}
  try { console.info(String('container=' + (mimeType.includes('webm') ? 'webm/opus' : 'ogg/opus'))); } catch {}


  try {
    recorder = new MediaRecorder(state.stream, { mimeType, audioBitsPerSecond: 128000 });
  } catch (primaryErr) {
    try {
      recorder = new MediaRecorder(state.stream); // fallback, browser picks best
    } catch (fallbackErr) {
      _console('warn', '[voice] MediaRecorder init failed', fallbackErr || primaryErr);
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
        const direct = state.direct;
        if (direct && direct.descriptor) {
          try {
            if (!direct.ws || direct.ws.readyState !== WebSocket.OPEN) {
              await _ensureDirectWs();
            }
            if (direct.ws && direct.ws.readyState === WebSocket.OPEN) {
              await _directSendChunk(blob);
            }
          } catch (err) {
            direct.lastError = err;
            _console('debug', '[voice] direct ASR chunk failed', err);
          }
        }

        try {
          await sendAudioChunk(blob);
          state.chunkBytesSent += blob.size;
          try {
            _console('debug', '[voice] streamed audio chunk', { bytes: blob.size });
          } catch {}
        } catch (err) {
          state.chunkSendError = err;
          try {
            _console('warn', '[voice] failed to stream audio chunk', err);
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
        _console('warn', '[voice] recorded chunks too small', state.chunkBytesSent);
        finalDetail = { statusText: 'Listening… (heard silence — please try again)' };
      }
    } catch (e) {
      _console('warn', '[voice] send audio failed', e);
      state.chunkSendError = state.chunkSendError || e;
    } finally {
      if (state.chunkSendError && !finalDetail) {
        finalDetail = { statusText: 'Listening… (audio send failed — please try again)' };
      }
      if (state.chunkSendError || state.chunkBytesSent < MIN_VALID_BLOB_BYTES) {
        try {
          _console('warn', '[voice] recorder stopped with issues', {
            bytesSent: state.chunkBytesSent,
            error: state.chunkSendError,
          });
        } catch {}
      } else {
        try {
          _console('debug', '[voice] recorder stopped', {
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
      _console('debug', '[voice] recorder started', { mime: state.rec.mimeType, timeslice });
    } catch {}
  } catch (e) {
    _console('warn', '[voice] recorder start failed', e);
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

function _canStartSpeech() {
  if (Date.now() < state.refractoryUntil) return false;     // hard refractory
  if (typeof ttsIsPlaying === 'function' && ttsIsPlaying()) return false; // never start while TTS plays
  if (state.eligibility === 'blocked_pregreet') return false; // wait until greet has actually started
  if (state.eligibility === 'holdoff' && _now() < state.postTtsHoldUntil) return false; // during post-TTS hold
  return true;
} 

function _onSpeechStartCommitted() {
  if (!_canStartSpeech()) return; 
  const now = _now();
  const holdUntil = state.postTtsHoldUntil || 0;
  const wait = Math.max(0, holdUntil - now);
  if (wait > 0) {
    _clearPostTtsHoldTimer();
    state.postTtsHoldTimer = setTimeout(() => {
      state.postTtsHoldTimer = null;
      try { _onSpeechStartCommitted(); } catch {}
    }, wait);
    return;
  }

  _clearPostTtsHoldTimer();
  state.postTtsHoldUntil = 0;
  _logLifecycle('vad_speech_start');
  // Pause Chip TTS; if a previous ASR turn somehow remained open, close it.
  try { console.info('barge_in'); console.info('tts_pause'); } catch {} try { console.info('barge_in'); console.info('tts_pause'); } catch {} try { console.info('barge_in'); console.info('tts_pause'); } catch {} _bargeIn();

  const started = _startRecorder();
  if (started) {
    try { console.info('[voice] state=recording'); } catch {}
_emitVoiceState('recording');
    return;
  }

  _console('warn', '[voice] recorder unavailable — reverting to typing');
  _emitVoiceState('armed', { statusText: 'Listening… (mic unavailable — please type)' });
}

function _onSpeechEndCommitted(detail = null) {
  const reason = detail?.reason || 'vad_silence';
  const now = performance.now ? performance.now() : Date.now();
  const minTurnMs = Number(optsFromGlobal('min_turn_ms', 1200)); // NEW: min turn length (default 1.2s)

  // If we haven't recorded at least minTurnMs, delay honoring VAD-end.
  // Only applies while recorder is actually running.
  if (state.rec && typeof state.rec.state === 'string' && state.rec.state === 'recording') {
    const elapsed = Math.max(0, now - (state.recStartedAt || now));
    const wait = Math.max(0, minTurnMs - elapsed);
    if (wait > 0) {
      try { _console('debug', '[voice] delaying VAD end', { waitMs: wait, elapsed }); } catch {}
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

export const __TEST_ONLY__ = {
  state,
  startRecorder: _startRecorder,
  stopRecorder: _stopRecorder,
  logLifecycle: _logLifecycle,
  onSpeechStartCommitted: _onSpeechStartCommitted,
  setEchoSignatureOverride(fn) {
    _overrideEchoSignatureFn = typeof fn === 'function' ? fn : null;
  },
};
