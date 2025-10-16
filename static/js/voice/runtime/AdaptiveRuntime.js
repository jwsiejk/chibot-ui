import {
  EvidenceGate,
  ShadowBuffer,
  TtsMask,
  TurnState,
  bufferPreRollFrame,
  flushShadowBuffer,
  getConfig,
  nowMs,
} from '../core/index.js';
import { getEvidenceSnrRequirement, getShadowStats } from '../loops/VadLoop.js';
import { emitVoiceEvent } from '../ui/Events.js';
import {
  openWS,
  waitWSOpen,
  sendAudioChunk,
  sendJSON,
} from '../../ws_module.js';
// Guarded CloseStream wrapper so we never kill the session before the first user turn.
import { sendCloseStream as __sendCloseStream } from '../../ws.js';

function safeCloseStream(reason = '') {
  try {
    const opened = !!(globalThis.__askchip_has_opened_turn || globalThis.__askchip_turn_open);
    if (!opened) {
      console.debug('[voice] skip CloseStream (no user turn yet)', { reason });
      return;
    }
  } catch {}
  __sendCloseStream();
}
import { VAD } from '../vad.js';
import { stopPlayback } from '../../audio.js';
import { logIfEnabled } from '../../util/logging.js';
import { getSID } from '../../util/sid.js';

const PRE_ROLL_MS = 550,
  RECORD_TIMESLICE_MS = 150,
  SAFETY_CLOSE_DELAY_MS = 2200,
  EVIDENCE_MIN_SPEECH_MS = 480,
  EVIDENCE_MIN_BYTES = 8 * 1024,
  MIN_VALID_BLOB_BYTES = 1,
  BARGE_CONFIRM_MS = 420;

const ctxRef = { current: null };

const voiceLog = (level, ...args) => {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
};

const MASK_LOG_INTERVAL_MS = 180;
const TTS_POST_PLAY_HOLD_MS = 180;

const resolvePostTtsHoldMs = (ctx) => {
  const cfg = ctx?.config?.tts ?? {};
  let value;
  if (Number.isFinite(cfg.post_play_hold_ms)) {
    value = cfg.post_play_hold_ms;
  } else if (Number.isFinite(cfg.decay_ms)) {
    value = cfg.decay_ms;
  } else {
    value = TTS_POST_PLAY_HOLD_MS;
  }
  if (!Number.isFinite(value)) {
    return TTS_POST_PLAY_HOLD_MS;
  }
  if (value <= 0) {
    return 0;
  }
  return Math.min(200, value);
};

const setManualGate = (ctx, active) => {
  if (!ctx?.state) return;
  const value = !!active;
  ctx.state.manualGate = value;
  ctx.state.manualPttActive = value;
};

const logReadyState = (ctx) => {
  if (!ctx || !ctx.state) return;
  const tsLocal = nowMs();
  const last = Number.isFinite(ctx.state.lastReadyLogAt) ? ctx.state.lastReadyLogAt : 0;
  if (tsLocal - last < 25) {
    return;
  }
  ctx.state.lastReadyLogAt = tsLocal;
  const ts = Date.now();
  voiceLog('info', "state { phase:'ready' }", {
    ts_ms: ts,
    session_id: ctx.sessionId || null,
    turn_id: ctx.state.activeTurnId || null,
  });
};

const stopMaskLogging = (ctx) => {
  if (!ctx) return;
  if (ctx.maskLogTimer) {
    try { clearInterval(ctx.maskLogTimer); } catch {}
  }
  ctx.maskLogTimer = null;
};

const logMaskTick = (ctx) => {
  if (!ctx) return;
  const tsLocal = nowMs();
  if (!ctx.ttsMask?.isMasked(tsLocal)) {
    stopMaskLogging(ctx);
    return;
  }
  const boost = ctx.ttsMask.snrBoost(tsLocal);
  const decayUntil = ctx.ttsMask.decayUntil();
  const remaining = Number.isFinite(decayUntil)
    ? Math.max(0, Math.round(decayUntil - tsLocal))
    : null;
  const ts = Date.now();
  voiceLog('info', '[mask] active', {
    ts_ms: ts,
    session_id: ctx.sessionId || null,
    turn_id: ctx.state?.activeTurnId || null,
    boost_db: Number.isFinite(boost) ? Number.parseFloat(boost.toFixed(2)) : null,
    decay_remaining_ms: remaining,
  });
};

const startMaskLogging = (ctx) => {
  if (!ctx) return;
  if (!ctx.maskLogTimer) {
    ctx.maskLogTimer = setInterval(() => {
      logMaskTick(ctx);
    }, MASK_LOG_INTERVAL_MS);
  }
  logMaskTick(ctx);
};

const resolveSnrBoost = (ctx, baseSnrDb = 3.5) => {
  try {
    const requirement = getEvidenceSnrRequirement(ctx.state, nowMs, baseSnrDb);
    return Math.max(0, requirement - baseSnrDb);
  } catch {
    return 0;
  }
};
const setState = (ctx, stateName, detail) => {
  ctx.state.turnState = stateName;
  const ts = Date.now();
  const baseDetail = detail && typeof detail === 'object' ? { ...detail } : {};
  emitVoiceEvent('state', {
    state: stateName,
    ts_ms: ts,
    sessionId: ctx.sessionId || null,
    ...baseDetail,
  });
  if (stateName === TurnState.Ready) {
    logReadyState(ctx);
  }
};

const isTtsMaskActive = (ctx) => {
  if (!ctx?.state) {
    return false;
  }
  if (ctx.state.ttsPlaying) {
    return true;
  }
  const holdMs = resolvePostTtsHoldMs(ctx);
  const ts = nowMs();
  if (holdMs > 0 && Number.isFinite(ctx.ttsEndedAtMs) && ctx.ttsEndedAtMs > 0) {
    if (ts - ctx.ttsEndedAtMs <= holdMs) {
      return true;
    }
  }
  if (ctx.ttsMask && typeof ctx.ttsMask.isMasked === 'function') {
    return ctx.ttsMask.isMasked(ts);
  }
  return false;
};

const ensureCtx = () => {
  if (ctxRef.current) {
    return ctxRef.current;
  }
  const config = getConfig();
  let sessionId = null;
  try {
    sessionId = getSID();
  } catch {}
  const evidenceGate = new EvidenceGate({
    snrSigma: config.evidence?.snr_sigma,
    asrConf: config.evidence?.asr_conf,
    baseSnrDb: config.evidence?.baseSnrDb,
    evidence: config.evidence,
  });
  const shadowBuffer = new ShadowBuffer({ maxMs: config.shadow?.ms ?? PRE_ROLL_MS });
  const ttsMask = new TtsMask();
  try {
    if (typeof window !== 'undefined') {
      window.__askchip_voice_session_id = sessionId;
    }
  } catch {}

  const ctx = {
    config,
    sessionId,
    state: {
      turnState: TurnState.Ready, recording: false, vadArmed: false,
      vadBoostDb: 0, greetGateActive: false, greetGatePhase: 'idle',
      greetGateWaiters: [], manualBargeInUsed: false,
      bargeConfirmActive: false, bargeConfirmUntil: 0,
      wsReady: false, turnOpen: false, hasOpenedTurn: false,
      manualGate: false, manualPttActive: false, pttHeld: false,
      turnSeq: 0, activeTurnId: null,
      ttsPlaying: false, lastChunkAt: 0,
      lastReadyLogAt: 0,
      evidenceGate, shadowBuffer, ttsMask,
    },
    audio: {
      stream: null, context: null, source: null, analyser: null,
      vad: null, recorder: null, recTimeslice: RECORD_TIMESLICE_MS,
      lastTimecode: null,
    },
    transport: { wsPromise: null, connected: false, safetyTimer: null },
    evidenceGate,
    shadowBuffer,
    ttsMask,
    maskLogTimer: null,
  };

  registerTtsListener(ctx);
  ctx.ttsEndedAtMs = 0;
  ctxRef.current = ctx;
  return ctx;
};

const registerTtsListener = (ctx) => {
  if (ctx.ttsListenerRegistered) return;
  ctx.ttsListenerRegistered = true;
  const win = typeof window !== 'undefined' ? window : null;
  const listener = (event) => {
    const detail = event?.detail || {};
    const state = String(detail.state || '').toLowerCase();
    if (state === 'playing') {
      ctx.state.ttsPlaying = true;
      ctx.ttsEndedAtMs = 0;
      ctx.ttsMask.start();
      startMaskLogging(ctx);
    } else if (state === 'ended' || state === 'stopped' || state === 'done') {
      ctx.state.ttsPlaying = false;
      ctx.ttsEndedAtMs = nowMs();
      ctx.ttsMask.end({ decayMs: resolvePostTtsHoldMs(ctx), snrBoost: detail.snrBoost });
      startMaskLogging(ctx);
    } else if (state === 'ready') {
      ctx.state.ttsPlaying = false;
      ctx.ttsEndedAtMs = nowMs();
      ctx.ttsMask.clear();
      stopMaskLogging(ctx);
      logReadyState(ctx);
    }
  };
  try { win?.addEventListener?.('chip-tts', listener); } catch {}
};

const ensureTransport = async (ctx) => {
  if (ctx.transport.connected) {
    return ctx.transport.wsPromise;
  }
  if (!ctx.transport.wsPromise) {
    ctx.transport.wsPromise = openWS();
  }
  try {
    const wsHandle = await waitWSOpen();
    ctx.transport.connected = true;
    ctx.state.wsReady = true;
    return wsHandle;
  } catch (err) {
    ctx.transport.wsPromise = null;
    ctx.transport.connected = false;
    ctx.state.wsReady = false;
    throw err;
  }
};

const teardownTransport = (ctx) => {
  ctx.transport.connected = false;
  ctx.state.wsReady = false;
  ctx.transport.wsPromise = null;
  if (ctx.transport.safetyTimer) {
    try { clearTimeout(ctx.transport.safetyTimer); } catch {}
    ctx.transport.safetyTimer = null;
  }
};
const ensureAudioGraph = (ctx, stream) => {
  const audio = ctx.audio;
  if (audio.context && audio.analyser && audio.source) return;
  const AudioCtx = typeof window !== 'undefined' && (window.AudioContext || window.webkitAudioContext);
  if (!AudioCtx) throw new Error('AudioContext not supported');
  audio.context = audio.context || new AudioCtx();
  try {
    if (typeof audio.context?.resume === 'function') {
      audio.context.resume().catch(() => {});
    }
  } catch {}
  try {
    if (audio.context?.state === 'suspended' && typeof audio.context.resume === 'function') {
      audio.context.resume().catch(() => {});
    }
  } catch {}
  audio.analyser = audio.context.createAnalyser();
  audio.analyser.fftSize = 2048;
  audio.analyser.smoothingTimeConstant = 0.4;
  audio.source = audio.context.createMediaStreamSource(stream);
  audio.source.connect(audio.analyser);
};

const startRecorder = (ctx, stream) => {
  const { audio } = ctx;
  if (audio.recorder) return;
  let mimeType = 'audio/webm; codecs=opus';
  try {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported
      && MediaRecorder.isTypeSupported('audio/ogg; codecs=opus')) {
      mimeType = 'audio/ogg; codecs=opus';
    }
  } catch {}
  const recorder = new MediaRecorder(stream, { mimeType });
  recorder.addEventListener('dataavailable', (event) => handleRecorderData(ctx, event));
  recorder.addEventListener('error', (event) => {
    emitVoiceEvent('recorder_error', { message: event?.error?.message || 'unknown' });
    stopRecorder(ctx);
  });
  recorder.start(audio.recTimeslice);
  voiceLog('info', '[recorder] started', {
    ts_ms: Date.now(),
    session_id: ctx.sessionId || null,
    mime_type: mimeType,
    timeslice_ms: audio.recTimeslice,
  });
  audio.recorder = recorder;
  ctx.state.recording = true;
};

const stopRecorder = (ctx) => {
  const { recorder } = ctx.audio;
  if (!recorder) return;
  try { if (recorder.state !== 'inactive') recorder.stop(); } catch {}
  ctx.audio.recorder = null;
  ctx.state.recording = false;
};

const handleRecorderData = (ctx, event) => {
  const blob = event?.data;
  if (!blob || typeof blob.size !== 'number' || blob.size < MIN_VALID_BLOB_BYTES) return;
  const timecode = Number.isFinite(event?.timecode) ? event.timecode : null;
  const { durationMs, nextTimecode } = bufferPreRollFrame({
    shadowBuffer: ctx.shadowBuffer,
    blob,
    timecode,
    timeslice: ctx.audio.recTimeslice,
    fallbackMs: PRE_ROLL_MS,
    lastTimecode: ctx.audio.lastTimecode,
    onBuffered: ({ durationMs: dur, byteLength }) => {
      ctx.evidenceGate.extendBuffer({ durationMs: dur, bytes: byteLength });
    },
  });
  ctx.audio.lastTimecode = nextTimecode;
  if (ctx.state.turnOpen) sendChunk(ctx, blob, { durationMs });
};

const sendChunk = (ctx, blob, { durationMs = 0 } = {}) => {
  if (!blob) {
    return;
  }
  sendAudioChunk(blob);
  ctx.state.lastChunkAt = nowMs();
  ctx.evidenceGate.extendBuffer({ durationMs });
  scheduleSafetyClose(ctx);
};

const scheduleSafetyClose = (ctx) => {
  if (ctx.transport.safetyTimer) { try { clearTimeout(ctx.transport.safetyTimer); } catch {} }
  ctx.transport.safetyTimer = null;
  if (!ctx.state.hasOpenedTurn || ctx.state.manualGate) {
    return;
  }
  ctx.transport.safetyTimer = setTimeout(() => {
    if (!ctx.state.turnOpen) return;
    safeCloseStream('adaptive');
    ctx.state.turnOpen = false;
    teardownTransport(ctx);
  }, SAFETY_CLOSE_DELAY_MS);
};

const openTurn = async (ctx, reason = 'speech_commit') => {
  if (ctx.state.turnOpen) return;
  await ensureTransport(ctx);
  ctx.state.turnOpen = true;
  ctx.state.hasOpenedTurn = true;
  ctx.state.turnSeq = (ctx.state.turnSeq || 0) + 1;
  const turnId = ctx.state.turnSeq;
  ctx.state.activeTurnId = turnId;
  const ts = Date.now();
  emitVoiceEvent('turn_open', {
    reason,
    turnId,
    sessionId: ctx.sessionId || null,
    ts_ms: ts,
  });
  voiceLog('info', '[turn] open', {
    ts_ms: ts,
    session_id: ctx.sessionId || null,
    turn_id: turnId,
    reason,
  });
  try {
    if (typeof window !== 'undefined') {
      window.__askchip_turn_trace_id = String(turnId);
    }
  } catch {}
  const stats = flushShadowBuffer(ctx.shadowBuffer, (entry) => {
    if (entry?.buffer) sendChunk(ctx, entry.buffer, { durationMs: entry.durationMs });
  });
  ctx.evidenceGate.setBufferStats(stats);
};

const closeTurn = (ctx, reason = 'vad_end') => {
  if (!ctx.state.turnOpen) {
    if (!ctx.state.hasOpenedTurn) {
      voiceLog('info', '[ws] CloseStream suppressed: no user turn yet', {
        ts_ms: Date.now(),
        session_id: ctx.sessionId || null,
      });
    }
    return;
  }
  const turnId = ctx.state.activeTurnId || ctx.state.turnSeq || null;
  safeCloseStream('adaptive');
  ctx.state.turnOpen = false;
  const ts = Date.now();
  const stats = getShadowStats(ctx.state);
  emitVoiceEvent('turn_close', {
    reason,
    stats,
    turnId,
    sessionId: ctx.sessionId || null,
    ts_ms: ts,
  });
  voiceLog('info', '[turn] close', {
    ts_ms: ts,
    session_id: ctx.sessionId || null,
    turn_id: turnId,
    reason,
  });
  ctx.state.activeTurnId = null;
  try {
    if (typeof window !== 'undefined') {
      window.__askchip_turn_trace_id = null;
    }
  } catch {}
  scheduleSafetyClose(ctx);
};

const evaluateEvidenceGate = async (ctx, { detail = null, vadState = 'speech', asrCue = null } = {}) => {
  if (ctx.state.manualGate) {
    ctx.evidenceGate.reset('manual_gate');
    return;
  }
  if (ctx.state.ttsPlaying) {
    ctx.evidenceGate.reset('tts_playing');
    return;
  }
  if (isTtsMaskActive(ctx)) {
    ctx.evidenceGate.reset('tts_mask');
    return;
  }
  const snr = Number.isFinite(detail?.snrDb) ? detail.snrDb : null;
  const snrBoost = resolveSnrBoost(ctx, ctx.evidenceGate?.config?.baseSnrDb ?? 3.5);
  const stats = getShadowStats(ctx.state);
  const result = ctx.evidenceGate.update({
    vadState,
    snr,
    snrBoost,
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
    minSpeechMs: EVIDENCE_MIN_SPEECH_MS,
    minBytes: EVIDENCE_MIN_BYTES,
    asrCue,
  });
  if (result.shouldCommit) {
    ctx.evidenceGate.satisfy('commit');
    await openTurn(ctx, 'evidence_gate_commit');
    if (detail) ctx.evidenceGate.setDetail(detail);
  }
};

const handleSpeechStart = async (ctx, detail) => {
  if (ctx.state.manualGate) {
    return;
  }
  if (ctx.state.ttsPlaying) {
    return;
  }
  if (isTtsMaskActive(ctx)) {
    return;
  }
  ctx.state.bargeConfirmActive = true;
  ctx.state.bargeConfirmUntil = nowMs() + BARGE_CONFIRM_MS;
  setState(ctx, TurnState.Recording, { detail });
  const stats = ctx.shadowBuffer.stats();
  ctx.evidenceGate.start({ startedAt: nowMs(), detail, bufferedMs: stats.durationMs, bufferedBytes: stats.totalBytes });
  emitVoiceEvent('speech_start', detail);
  await evaluateEvidenceGate(ctx, { detail, vadState: 'speech' });
};

const handleSpeechEnd = async (ctx, detail) => {
  if (ctx.state.manualGate) {
    return;
  }
  ctx.state.bargeConfirmActive = false;
  ctx.state.bargeConfirmUntil = 0;
  emitVoiceEvent('speech_end', detail);
  await evaluateEvidenceGate(ctx, { detail, vadState: 'silence', asrCue: { type: 'vad_end' } });
  closeTurn(ctx, 'speech_end');
  ctx.evidenceGate.reset('vad_end');
  ctx.shadowBuffer.clear();
  ctx.audio.lastTimecode = null;
  setState(ctx, TurnState.Listening);
};

const ensureVad = (ctx, opts = {}) => {
  const { audio } = ctx;
  if (audio.vad) return;
  const echoStateFn = () => isTtsMaskActive(ctx);
  audio.vad = new VAD(audio.analyser, {
    startDbOffset: 10 + ctx.state.vadBoostDb,
    stopDbOffset: 6 + ctx.state.vadBoostDb,
    echoStateFn,
    minSpeechMs: Math.max(180, opts.minSpeechMs ?? 280),
    minSilenceMs: Math.max(180, opts.minSilenceMs ?? 300),
    gateFn: () => !isTtsMaskActive(ctx),
  }, {
    onSpeechStart: (detail) => { handleSpeechStart(ctx, detail); },
    onSpeechEnd: (detail) => { handleSpeechEnd(ctx, detail); },
  });
  audio.vad.start();
  ctx.state.vadArmed = true;
  setState(ctx, TurnState.Listening);
};

const teardownVad = (ctx) => {
  const { audio } = ctx;
  if (audio.vad) { try { audio.vad.stop(); } catch {} }
  audio.vad = null;
  ctx.state.vadArmed = false;
};

const ensureGreetGate = (ctx, active) => {
  ctx.state.greetGateActive = !!active;
  if (!active) { ctx.state.greetGatePhase = 'idle'; ctx.state.greetGateWaiters = []; }
  else if (ctx.state.greetGatePhase === 'idle') ctx.state.greetGatePhase = 'calibrating';
};

const manualBargeAllowed = (ctx) => isTtsMaskActive(ctx);

export async function initMic(stream = null) {
  const ctx = ensureCtx();
  if (stream) ctx.audio.stream = stream;
  if (!ctx.audio.stream) {
    const constraints = { audio: { echoCancellation: true, noiseSuppression: true } };
    ctx.audio.stream = await navigator.mediaDevices.getUserMedia(constraints);
  }
  ensureAudioGraph(ctx, ctx.audio.stream);
  return ctx.audio.stream;
}

export async function armVAD(stream = null, opts = {}) {
  const ctx = ensureCtx();
  const mic = await initMic(stream);
  ensureAudioGraph(ctx, mic);
  try {
    if (typeof ctx.audio?.context?.resume === 'function') {
      await ctx.audio.context.resume();
    }
  } catch {}
  if (ctx.audio?.context) {
    const contextState = ctx.audio.context.state || 'unknown';
    voiceLog('info', `[audio] context state: ${contextState}`, {
      ts_ms: Date.now(),
      session_id: ctx.sessionId || null,
    });
  }
  ensureVad(ctx, opts);
  startRecorder(ctx, mic);
  await ensureTransport(ctx).catch(() => {});
  emitVoiceEvent('armed', { mode: 'adaptive' });
  return mic;
}

export function disarmVAD() {
  const ctx = ensureCtx();
  teardownVad(ctx); stopRecorder(ctx);
  closeTurn(ctx, 'manual_disarm');
  teardownTransport(ctx);
  setManualGate(ctx, false);
  ctx.state.pttHeld = false;
  ctx.state.hasOpenedTurn = false;
  stopMaskLogging(ctx);
  setState(ctx, TurnState.Ready);
}

export function isRecording() {
  const ctx = ensureCtx();
  return !!ctx.state.recording;
}

export function bargeIn() {
  const ctx = ensureCtx();
  const shouldNotify = manualBargeAllowed(ctx);
  ctx.state.manualBargeInUsed = true;
  ctx.state.bargeConfirmActive = false;
  ctx.state.bargeConfirmUntil = 0;
  try { stopPlayback(); } catch {}
  if (ctx.state.turnOpen) {
    closeTurn(ctx, 'manual_barge_preempt');
  }
  ctx.state.ttsPlaying = false;
  ctx.ttsEndedAtMs = nowMs();
  ctx.ttsMask.clear();
  if (shouldNotify) {
    sendJSON({ type: 'manual_barge_in' });
  }
  ctx.evidenceGate.reset('manual_barge_in');
  ctx.shadowBuffer.clear();
  ctx.audio.lastTimecode = null;
  emitVoiceEvent('barge_in', { reason: 'manual' });
  return true;
}

export function setVadBoost(value) {
  const ctx = ensureCtx();
  const numeric = Number.isFinite(value) ? value : 0;
  ctx.state.vadBoostDb = Math.max(0, numeric);
  if (ctx.audio.vad) {
    teardownVad(ctx); ensureVad(ctx, {});
  }
}

export function setGreetGateActive(active = true) {
  const ctx = ensureCtx();
  ensureGreetGate(ctx, active);
  emitVoiceEvent('greet_gate', { active: ctx.state.greetGateActive });
}

export function forceBargeInStart(meta = {}) {
  const ctx = ensureCtx();
  if (ctx.state.manualGate) {
    ctx.state.pttHeld = true;
    return false;
  }

  ctx.state.pttHeld = true;
  setManualGate(ctx, true);

  bargeIn();

  ctx.state.manualBargeInUsed = true;
  ctx.state.ttsPlaying = false;
  ctx.ttsMask.clear();
  ctx.ttsEndedAtMs = nowMs();

  const controlFrame = { type: 'Control', action: 'barge_in_start' };
  const sent = sendJSON(controlFrame);
  if (!sent) {
    ensureTransport(ctx).then(() => {
      try { sendJSON(controlFrame); } catch {}
    }).catch(() => {});
  }

  setState(ctx, TurnState.Recording, { reason: 'manual_start' });
  emitVoiceEvent('barge_in_start', meta);

  const open = openTurn(ctx, 'manual_start');
  if (open && typeof open.then === 'function') {
    open.catch(() => {});
  }

  return true;
}

export function forceBargeInEnd(opts = {}) {
  const ctx = ensureCtx();
  const wasActive = ctx.state.manualGate;
  const stillHeld = !!opts?.pttHeld;
  ctx.state.pttHeld = stillHeld;
  if (!stillHeld) {
    setManualGate(ctx, false);
  }
  ctx.state.ttsPlaying = false;
  ctx.ttsMask.end({ decayMs: resolvePostTtsHoldMs(ctx), snrBoost: opts.snrBoost });
  ctx.ttsEndedAtMs = nowMs();

  const reason = typeof opts?.reason === 'string' ? opts.reason : 'manual_release';
  const released = wasActive && !ctx.state.manualGate;
  if (released) {
    const controlFrame = { type: 'Control', action: 'barge_in_end' };
    const sent = sendJSON(controlFrame);
    if (!sent) {
      ensureTransport(ctx).then(() => {
        try { sendJSON(controlFrame); } catch {}
      }).catch(() => {});
    }

    if (ctx.state.turnOpen) {
      closeTurn(ctx, reason);
    }
    setState(ctx, TurnState.Listening, { reason });
  }

  if (released) {
    ctx.state.bargeConfirmActive = false;
    ctx.state.bargeConfirmUntil = 0;
    ctx.evidenceGate.reset('manual_release');
    ctx.shadowBuffer.clear();
    ctx.audio.lastTimecode = null;
    emitVoiceEvent('barge_in_end', opts);
  }
  return wasActive;
}

const getCtx = () => ctxRef.current;

export const __TEST_ONLY__ = Object.freeze({
  getCtx: () => (globalThis.ADVANCED_LOGGING_ENABLED ? getCtx() : null),
  nowMs,
});
