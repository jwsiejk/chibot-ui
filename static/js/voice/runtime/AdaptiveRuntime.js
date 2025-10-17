console.log("[AdaptiveRuntime] build=2025-10-16T23:15Z");
import {
  EvidenceGate,
  ShadowBuffer,
  TtsMask,
  TurnState,
  bufferPreRollFrame,
  flushShadowBuffer,
  getConfig,
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

const clamp01 = (value) => {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return 0;
  }
  if (num <= 0) {
    return 0;
  }
  if (num >= 1) {
    return 1;
  }
  return num;
};

const clampRange = (value, min, max) => {
  const numeric = Number.isFinite(value) ? value : 0;
  return Math.min(max, Math.max(min, numeric));
};

const PRE_ROLL_MS = 550,
  RECORD_TIMESLICE_MS = 150,
  SAFETY_CLOSE_DELAY_MS = 2200,
  EVIDENCE_MIN_SPEECH_MS = 480,
  EVIDENCE_MIN_BYTES = 8 * 1024,
  MIN_VALID_BLOB_BYTES = 1,
  BARGE_CONFIRM_MS = 420;

const ctxRef = { current: null };

const initAsrState = () => ({
  speaking: false,
  lastPartialTs: 0,
  lastConf: 0,
  lastPartialLogTs: 0,
  lastVadLogTs: 0,
  lastVadLogType: null,
});

const ensureAsrState = (ctx) => {
  if (!ctx?.state) return initAsrState();
  if (!ctx.state.asr) {
    ctx.state.asr = initAsrState();
  }
  return ctx.state.asr;
};

const resetAsrState = (ctx) => {
  const target = ensureAsrState(ctx);
  target.speaking = false;
  target.lastPartialTs = 0;
  target.lastConf = 0;
  target.lastPartialLogTs = 0;
  target.lastVadLogTs = 0;
  target.lastVadLogType = null;
};

const dualVadEnabled = (ctx) => !!(ctx?.config?.dual_vad?.enabled);
const dualVadDebugEnabled = (ctx) => ctx?.config?.debug?.vad === true;

const asrStaleMs = (ctx) => {
  const raw = ctx?.config?.dual_vad?.asr_stale_ms;
  return Number.isFinite(raw) && raw > 0 ? raw : 800;
};

const commitConfidenceThreshold = (ctx) => {
  const raw = ctx?.config?.dual_vad?.commit_conf;
  return Number.isFinite(raw) ? raw : 0.6;
};

const quietCloseMs = (ctx) => {
  const raw = ctx?.config?.dual_vad?.close_quiet_ms;
  return Number.isFinite(raw) && raw >= 0 ? raw : 700;
};

const isAsrSpeakingNow = (ctx) => {
  if (!dualVadEnabled(ctx)) return true;
  const asr = ensureAsrState(ctx);
  const age = nowMs() - (Number.isFinite(asr.lastPartialTs) ? asr.lastPartialTs : 0);
  return !!asr.speaking && age < asrStaleMs(ctx);
};

const onAsrPartial = (ctx, partial = {}) => {
  const asr = ensureAsrState(ctx);
  const conf = Number.isFinite(partial?.confidence) ? partial.confidence : null;
  if (Number.isFinite(conf)) {
    asr.lastConf = Math.max(asr.lastConf ?? 0, conf);
  }
  const prevPartialTs = Number.isFinite(asr.lastPartialTs) ? asr.lastPartialTs : 0;
  const nowLocal = nowMs();
  const deltaMs = prevPartialTs ? Math.max(0, nowLocal - prevPartialTs) : null;
  const logTs = Date.now();
  if (!Number.isFinite(asr.lastPartialLogTs) || (logTs - asr.lastPartialLogTs) >= 150) {
    voiceLog('info', '[asr] partial', {
      ts_ms: logTs,
      session_id: ctx?.sessionId || null,
      turn_id: ctx?.state?.activeTurnId || null,
      confidence: Number.isFinite(conf) ? Number.parseFloat(conf.toFixed(3)) : null,
      delta_ms: deltaMs,
    });
    asr.lastPartialLogTs = logTs;
  }
  asr.lastPartialTs = nowLocal;
  asr.speaking = true;
};

const onAsrVad = (ctx, event = {}) => {
  const asr = ensureAsrState(ctx);
  const type = (event?.type || '').toLowerCase();
  if (type === 'speech_start' || type === 'start' || type === 'begin') {
    asr.speaking = true;
    asr.lastPartialTs = nowMs();
  } else if (type === 'speech_end' || type === 'end' || type === 'stop') {
    asr.speaking = false;
  }
  const logTs = Date.now();
  const normalized = type || 'unknown';
  if (asr.lastVadLogType !== normalized || !Number.isFinite(asr.lastVadLogTs) || (logTs - asr.lastVadLogTs) >= 150) {
    voiceLog('info', '[asr] vad', {
      ts_ms: logTs,
      session_id: ctx?.sessionId || null,
      turn_id: ctx?.state?.activeTurnId || null,
      event: normalized,
      speaking: !!asr.speaking,
    });
    asr.lastVadLogType = normalized;
    asr.lastVadLogTs = logTs;
  }
};

const clearDualVadTimer = (ctx) => {
  if (!ctx?.state?.dualVadCloseTimer) return;
  try { clearTimeout(ctx.state.dualVadCloseTimer); } catch {}
  ctx.state.dualVadCloseTimer = null;
};

const scheduleDualVadTimer = (ctx) => {
  if (!ctx?.state || !dualVadEnabled(ctx) || !ctx.state.turnOpen) return;
  const delay = quietCloseMs(ctx);
  if (!Number.isFinite(delay) || delay <= 0) {
    return;
  }
  clearDualVadTimer(ctx);
  ctx.state.dualVadCloseTimer = setTimeout(() => {
    ctx.state.dualVadCloseTimer = null;
    if (!attemptCloseWithReason(ctx, 'dual_vad_quiet', 'dual_vad_quiet')) {
      scheduleDualVadTimer(ctx);
    }
  }, delay);
};

const voiceLog = (level, ...args) => {
  logIfEnabled(() => {
    try {
      const method = typeof console?.[level] === 'function' ? console[level] : console.log;
      method?.apply(console, args);
    } catch {}
  });
};

const logPreCommitMode = (ctx, mode, extra = {}) => {
  if (!ctx?.state) return;
  const normalized = typeof mode === 'string' && mode ? mode : 'shadow_only';
  const previous = ctx.state.lastPreCommitFeedMode;
  if (previous === normalized) {
    return;
  }
  ctx.state.lastPreCommitFeedMode = normalized;
  voiceLog('info', '[asr] pre-commit feed', {
    ts_ms: Date.now(),
    session_id: ctx.sessionId || null,
    turn_id: ctx.state?.activeTurnId || null,
    mode: normalized,
    ...extra,
  });
};

const MASK_LOG_INTERVAL_MS = 180;
const TTS_POST_PLAY_HOLD_MS = 180;
const RMS_EPSILON = 1e-8;
const VAD_NOISE_SAMPLE_MS = 320;
const VAD_NOISE_SAMPLE_STEP_MS = 40;
const VAD_NOISE_DEADBAND_DB = 0.35;
const VAD_NOISE_DELTA_LIMIT_DB = 1.75;
const VAD_NOISE_SMOOTHING = 0.25;

const updateVadBoost = (ctx, boostDb) => {
  if (!ctx?.state) return;
  const clamped = clampRange(boostDb, -3, 3);
  ctx.state.vadBoostDb = clamped;
  if (ctx?.audio?.vad?.opts) {
    ctx.audio.vad.opts.startDbOffset = 6 + clamped;
    ctx.audio.vad.opts.stopDbOffset = 6 + clamped;
  }
};

const nowMs = () => {
  try {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
      return performance.now();
    }
  } catch {}
  return Date.now();
};

const resolvePostTtsHoldMs = (ctx) => {
  const cfg = ctx?.config?.tts ?? {};
  let value;
  if (Number.isFinite(cfg.mask_decay_ms)) {
    value = cfg.mask_decay_ms;
  } else if (Number.isFinite(cfg.post_play_hold_ms)) {
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
  ctx.state.pendingCommitReason = null;
  clearDualVadTimer(ctx);
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
  const decayRaw = ctx.config?.tts?.mask_decay_ms ?? 200;
  const decay = Math.min(200, Math.max(0, Number.isFinite(decayRaw) ? decayRaw : 200)); // keep ≤ 200ms
  const endedAt = ctx.ttsEndedAtMs ?? 0;
  return !!(ctx.state.ttsPlaying || (endedAt && (nowMs() - endedAt) < decay));
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
      vadBoostDb: 0, vadNoiseBaselineDb: null, greetGateActive: false, greetGatePhase: 'idle',
      greetGateWaiters: [], manualBargeInUsed: false,
      bargeConfirmActive: false, bargeConfirmUntil: 0,
      wsReady: false, turnOpen: false, hasOpenedTurn: false,
      manualGate: false, manualPttActive: false, pttHeld: false,
      turnSeq: 0, activeTurnId: null,
      ttsPlaying: false, lastChunkAt: 0,
      lastReadyLogAt: 0,
      vadRecording: false,
      lastUserAudioMs: 0,
      preCommitASRFeed: false,
      pendingCommitReason: null,
      asr: initAsrState(),
      dualVadCloseTimer: null,
      dualVadLogSnapshot: null,
      lastDualVadLogTs: 0,
      lastPreCommitFeedMode: 'shadow_only',
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
  registerWsListener(ctx);
  ctx.ttsEndedAtMs = 0;
  ctxRef.current = ctx;
  return ctx;
};

const registerTtsListener = (ctx) => {
  if (ctx.ttsListenerRegistered) return;
  ctx.ttsListenerRegistered = true;
  const win = typeof window !== 'undefined' ? window : null;
  const listener = (ev) => {
    const { detail } = ev || {};
    const { state } = detail || {};
    const normalizedState = String(state || '').toLowerCase();
    if (normalizedState === 'playing') {
      ctx.state.ttsPlaying = true;
      ctx.ttsEndedAtMs = undefined;
      ctx.ttsMask.start();
      startMaskLogging(ctx);
    } else if (
      normalizedState === 'ended' ||
      normalizedState === 'stopped' ||
      normalizedState === 'done'
    ) {
      ctx.state.ttsPlaying = false;
      ctx.ttsEndedAtMs = nowMs();
      ctx.ttsMask.end({ decayMs: resolvePostTtsHoldMs(ctx), snrBoost: detail?.snrBoost });
      try {
        ctx.state.vadBoostDb = Math.max(0, (ctx.state.vadBoostDb ?? 0) - 3);
        setTimeout(() => {
          try {
            ctx.state.vadBoostDb = 0;
          } catch {}
        }, 250);
      } catch {}
      startMaskLogging(ctx);
    } else if (normalizedState === 'ready') {
      ctx.state.ttsPlaying = false;
      ctx.ttsEndedAtMs = nowMs();
      ctx.ttsMask.clear();
      stopMaskLogging(ctx);
      logReadyState(ctx);
    }
  };
  try { win?.addEventListener?.('chip-tts', listener, { passive: true }); } catch {}
};

const normalizeVadLabel = (value) => (typeof value === 'string' ? value.toLowerCase() : '');

const resolveVadEventType = (frame) => {
  if (!frame || typeof frame !== 'object') return null;
  const metaVad = normalizeVadLabel(frame?.meta?.local_vad);
  if (metaVad) {
    if (['start', 'active', 'speech_start', 'begin'].includes(metaVad)) return 'speech_start';
    if (['stop', 'end', 'inactive', 'speech_end'].includes(metaVad)) return 'speech_end';
  }

  const candidates = [
    frame?.type,
    frame?.event,
    frame?.kind,
    frame?.label,
    frame?.state,
    frame?.signal,
    frame?.detail?.type,
    frame?.detail?.event,
    frame?.payload?.type,
    frame?.payload?.event,
  ].map(normalizeVadLabel);

  for (const token of candidates) {
    if (!token) continue;
    if (token.includes('speech_start')) return 'speech_start';
    if (token.includes('speech_end')) return 'speech_end';
    if (token.includes('vad')) {
      if (token.includes('start') || token.includes('begin') || token.includes('active')) {
        return 'speech_start';
      }
      if (token.includes('stop') || token.includes('end') || token.includes('inactive')) {
        return 'speech_end';
      }
    }
  }
  return null;
};

const extractAsrConfidence = (frame) => {
  const alt = Array.isArray(frame?.channel?.alternatives)
    ? frame.channel.alternatives[0]
    : (Array.isArray(frame?.alternatives) ? frame.alternatives[0] : null);
  const confs = [
    alt?.confidence,
    frame?.confidence,
    frame?.detail?.confidence,
    frame?.payload?.confidence,
  ];
  for (const value of confs) {
    if (Number.isFinite(value)) {
      return value;
    }
  }
  return null;
};

function shouldCloseTurn(ctx) {
  if (!ctx?.state) return true;
  if (!dualVadEnabled(ctx)) return true;
  const clientSilent = ctx.state.vadRecording === false;
  const asrSilent = !isAsrSpeakingNow(ctx);
  const lastAudio = Number.isFinite(ctx.state.lastUserAudioMs) ? ctx.state.lastUserAudioMs : 0;
  const quietMs = lastAudio > 0 ? Math.max(0, nowMs() - lastAudio) : 0;
  if (dualVadDebugEnabled(ctx)) {
    let quietLogMs = quietMs;
    try {
      if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
        quietLogMs = performance.now() - (ctx.state.lastUserAudioMs || 0);
      } else {
        quietLogMs = Date.now() - (ctx.state.lastUserAudioMs || 0);
      }
    } catch {}
    try {
      console.debug('[dual-vad] close?', {
        clientSilent,
        asrSilent,
        quietMs: quietLogMs,
      });
    } catch {}
  }
  return (clientSilent && asrSilent) || quietMs > quietCloseMs(ctx);
}

const frameIndicatesAsrResult = (frame) => {
  const type = normalizeVadLabel(frame?.type);
  const event = normalizeVadLabel(frame?.event);
  return type === 'result' || type === 'results' || event === 'result' || event === 'results';
};

function handleWsFrame(ctx, frame) {
  if (!ctx?.state) return;
  const type = normalizeVadLabel(frame?.type);
  const event = normalizeVadLabel(frame?.event);

  if (frameIndicatesAsrResult(frame)) {
    const confidence = extractAsrConfidence(frame);
    onAsrPartial(ctx, { confidence });
    if (dualVadEnabled(ctx) && Number.isFinite(confidence) && confidence >= commitConfidenceThreshold(ctx)) {
      if (!ctx.state.pendingCommitReason) {
        ctx.state.pendingCommitReason = 'asr_confidence';
      }
    }
    if (dualVadEnabled(ctx)) {
      const attempt = maybeCommitSpeech(ctx);
      if (attempt && typeof attempt.then === 'function') {
        attempt.catch(() => {});
      }
    }
    const isFinal = Boolean(
      frame?.channel?.is_final ?? frame?.is_final ?? frame?.final ?? frame?.detail?.is_final
    );
    if (isFinal) {
      ensureAsrState(ctx).speaking = false;
    }
    return;
  }

  const vadType = resolveVadEventType(frame);
  if (vadType) {
    onAsrVad(ctx, { type: vadType });
    if (dualVadEnabled(ctx) && vadType === 'speech_start') {
      const attempt = maybeCommitSpeech(ctx);
      if (attempt && typeof attempt.then === 'function') {
        attempt.catch(() => {});
      }
    } else if (dualVadEnabled(ctx) && vadType === 'speech_end') {
      if (!attemptCloseWithReason(ctx, 'asr_vad_end', 'asr_vad_end')) {
        scheduleDualVadTimer(ctx);
      }
    }
    return;
  }

  if (type === 'utteranceend' || event === 'utteranceend') {
    ensureAsrState(ctx).speaking = false;
    if (dualVadEnabled(ctx)) {
      if (!attemptCloseWithReason(ctx, 'utterance_end', 'utterance_end')) {
        scheduleDualVadTimer(ctx);
      }
    }
  }
}

function registerWsListener(ctx) {
  if (!ctx || ctx.wsListenerRegistered) return;
  const win = typeof window !== 'undefined' ? window : null;
  const handler = (ev) => {
    try { handleWsFrame(ctx, ev?.detail); } catch {}
  };
  try { win?.addEventListener?.('askchip-ws', handler, { passive: true }); } catch {}
  ctx.wsListenerRegistered = true;
  ctx.wsListener = handler;
}

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
  const feedMode = ctx.state.turnOpen
    ? 'streaming'
    : (ctx.state.preCommitASRFeed ? 'asr_priming' : 'shadow_only');
  logPreCommitMode(ctx, feedMode, {
    source: ctx.state.turnOpen ? 'turn_stream' : 'precommit_buffer',
    chunk_bytes: blob.size,
    duration_ms: durationMs,
    timecode,
  });
  if (!ctx.state.turnOpen) {
    if (ctx.state.preCommitASRFeed) {
      try {
        const maybePromise = sendAudioChunk(blob);
        if (maybePromise && typeof maybePromise.catch === 'function') {
          maybePromise.catch(() => {});
        }
      } catch {}
    }
    return;
  }
  if (ctx.state.turnOpen) sendChunk(ctx, blob, { durationMs });
};

const sendChunk = (ctx, blob, { durationMs = 0 } = {}) => {
  if (!blob) {
    return;
  }
  const tsNow = nowMs();
  sendAudioChunk(blob);
  ctx.state.lastChunkAt = tsNow;
  ctx.state.lastUserAudioMs = tsNow;
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
  ctx.state.preCommitASRFeed = false;
  ctx.state.turnSeq = (ctx.state.turnSeq || 0) + 1;
  const turnId = ctx.state.turnSeq;
  ctx.state.activeTurnId = turnId;
  logPreCommitMode(ctx, 'streaming', { reason: 'turn_open' });
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
  clearDualVadTimer(ctx);
  ctx.state.turnOpen = false;
  ctx.state.preCommitASRFeed = false;
  logPreCommitMode(ctx, 'shadow_only', { reason: 'turn_close' });
  ctx.state.pendingCommitReason = null;
  ctx.state.vadRecording = false;
  ctx.state.lastUserAudioMs = 0;
  resetAsrState(ctx);
  ctx.state.dualVadLogSnapshot = null;
  ctx.state.lastDualVadLogTs = 0;
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
  onTurnClosed(ctx);
};

const median = (values) => {
  if (!Array.isArray(values) || values.length === 0) {
    return null;
  }
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2) {
    return sorted[mid];
  }
  if (mid === 0) {
    return sorted[0];
  }
  return (sorted[mid - 1] + sorted[mid]) / 2;
};

function onTurnClosed(ctx) {
  if (!ctx?.state) {
    return;
  }

  const previousBoost = Number.isFinite(ctx.state.vadBoostDb) ? ctx.state.vadBoostDb : 0;
  updateVadBoost(ctx, 0);

  const analyser = ctx?.audio?.analyser;
  if (!analyser) {
    updateVadBoost(ctx, previousBoost);
    return;
  }

  if (ctx.vadCalibrator && typeof ctx.vadCalibrator.cancel === 'function') {
    try { ctx.vadCalibrator.cancel(); } catch {}
  } else if (ctx.vadCalibrator) {
    ctx.vadCalibrator.cancelled = true;
  }

  const token = {
    cancelled: false,
    cancel() { this.cancelled = true; },
  };
  ctx.vadCalibrator = token;

  const scratch = new Float32Array(analyser.fftSize || 2048);
  const samples = [];
  const steps = Math.max(1, Math.round(VAD_NOISE_SAMPLE_MS / VAD_NOISE_SAMPLE_STEP_MS));
  let done = false;

  const finalize = () => {
    if (done) return;
    done = true;
    token.cancelled = true;
    if (ctx.vadCalibrator === token) {
      ctx.vadCalibrator = null;
    }

    let nextBoost = clampRange(previousBoost, -3, 3);

    const medianRms = median(samples);
    if (Number.isFinite(medianRms) && medianRms > 0) {
      const noiseDb = 20 * Math.log10(Math.max(RMS_EPSILON, medianRms));
      const prevBaseline = Number.isFinite(ctx.state.vadNoiseBaselineDb)
        ? ctx.state.vadNoiseBaselineDb
        : null;
      let deltaDb = Number.isFinite(prevBaseline) ? noiseDb - prevBaseline : 0;
      const updatedBaseline = Number.isFinite(prevBaseline)
        ? prevBaseline + (VAD_NOISE_SMOOTHING * (noiseDb - prevBaseline))
        : noiseDb;
      ctx.state.vadNoiseBaselineDb = updatedBaseline;

      if (Math.abs(deltaDb) < VAD_NOISE_DEADBAND_DB) {
        deltaDb = 0;
      } else {
        deltaDb = clampRange(deltaDb, -VAD_NOISE_DELTA_LIMIT_DB, VAD_NOISE_DELTA_LIMIT_DB);
      }

      nextBoost = clampRange(previousBoost + deltaDb, -3, 3);

      voiceLog('info', '[vad] ambient recalibration', {
        ts_ms: Date.now(),
        session_id: ctx.sessionId || null,
        noise_db: Number.isFinite(noiseDb) ? Number.parseFloat(noiseDb.toFixed(2)) : null,
        baseline_db: Number.isFinite(updatedBaseline) ? Number.parseFloat(updatedBaseline.toFixed(2)) : null,
        delta_db: Number.isFinite(deltaDb) ? Number.parseFloat(deltaDb.toFixed(2)) : null,
        boost_db: Number.isFinite(nextBoost) ? Number.parseFloat(nextBoost.toFixed(2)) : null,
        samples: samples.length,
      });
    }

    updateVadBoost(ctx, nextBoost);
  };

  const takeSample = () => {
    if (token.cancelled || done) {
      return;
    }
    if (!ctx?.state || ctx.state.turnOpen) {
      finalize();
      return;
    }
    try {
      analyser.getFloatTimeDomainData(scratch);
    } catch {
      finalize();
      return;
    }
    let sum = 0;
    for (let i = 0; i < scratch.length; i += 1) {
      const v = scratch[i];
      sum += v * v;
    }
    const rms = Math.sqrt(sum / scratch.length);
    if (Number.isFinite(rms) && rms > 0) {
      samples.push(rms);
    }
  };

  const schedule = (index) => {
    if (token.cancelled || done) {
      return;
    }
    takeSample();
    if (index + 1 >= steps) {
      finalize();
      return;
    }
    setTimeout(() => schedule(index + 1), VAD_NOISE_SAMPLE_STEP_MS);
  };

  schedule(0);
}

function finalizeTurnClose(ctx, reason = 'dual_vad_quiet', resetReason = reason) {
  closeTurn(ctx, reason);
  ctx.evidenceGate.reset(resetReason);
  ctx.shadowBuffer.clear();
  ctx.audio.lastTimecode = null;
  setState(ctx, TurnState.Listening);
}

function attemptCloseWithReason(ctx, reason = 'dual_vad_quiet', resetReason = reason) {
  if (!ctx?.state?.turnOpen) {
    return false;
  }
  if (!shouldCloseTurn(ctx)) {
    return false;
  }
  finalizeTurnClose(ctx, reason, resetReason);
  return true;
}

function logDualVadDecision(ctx, {
  clientSaysSpeech,
  asrSpeaking,
  asrConf,
  asrConfOK,
  decision,
}) {
  if (!ctx?.state || !dualVadEnabled(ctx)) {
    return;
  }
  const now = Date.now();
  const snapshot = [
    clientSaysSpeech ? '1' : '0',
    asrSpeaking ? '1' : '0',
    Number.isFinite(asrConf) ? asrConf.toFixed(3) : 'na',
    decision ? '1' : '0',
  ].join('|');
  const lastTs = Number.isFinite(ctx.state.lastDualVadLogTs) ? ctx.state.lastDualVadLogTs : 0;
  if (ctx.state.dualVadLogSnapshot === snapshot && now - lastTs < 120) {
    return;
  }
  ctx.state.dualVadLogSnapshot = snapshot;
  ctx.state.lastDualVadLogTs = now;
  const detail = {
    ts_ms: now,
    session_id: ctx.sessionId || null,
    turn_id: ctx.state?.activeTurnId || null,
    client_speech: !!clientSaysSpeech,
    asr_speaking: !!asrSpeaking,
    asr_conf: Number.isFinite(asrConf) ? Number.parseFloat(asrConf.toFixed(3)) : null,
    conf_threshold: commitConfidenceThreshold(ctx),
    decision: decision ? 'commit' : 'hold',
    decision_via: decision ? (asrConfOK ? 'asr_confidence' : 'dual_active') : 'awaiting_alignment',
    pending_reason: ctx.state?.pendingCommitReason || null,
  };
  voiceLog('info', '[dual-vad] decision', detail);
}

function shouldCommitTurn(ctx) {
  if (!ctx?.state) return false;
  if (ctx.state.turnOpen) return false;
  if (ctx.state.ttsPlaying || ctx.state.manualGate) return false;
  if (!dualVadEnabled(ctx)) {
    return true;
  }
  const clientSaysSpeech = ctx.state.vadRecording === true;
  const asrSpeaking = isAsrSpeakingNow(ctx);
  const asrState = ensureAsrState(ctx);
  const asrConf = asrState.lastConf ?? 0;
  const asrConfOK = asrConf >= commitConfidenceThreshold(ctx);
  if (dualVadDebugEnabled(ctx)) {
    try {
      console.debug('[dual-vad] commit?', {
        client: ctx.state.vadRecording,
        asrSpeaking,
        asrConf,
      });
    } catch {}
  }
  const decision = (clientSaysSpeech && asrSpeaking) || asrConfOK;
  logDualVadDecision(ctx, { clientSaysSpeech, asrSpeaking, asrConf, asrConfOK, decision });
  return decision;
}

function maybeCommitSpeech(ctx, reason = 'speech_commit') {
  if (!ctx?.state) return null;
  if (ctx.state.turnOpen) {
    ctx.state.pendingCommitReason = null;
    return null;
  }

  if (!dualVadEnabled(ctx)) {
    if (!reason && !ctx.state.pendingCommitReason) {
      return null;
    }
    if (reason && !ctx.state.pendingCommitReason) {
      ctx.state.pendingCommitReason = reason;
    }
    if (!shouldCommitTurn(ctx)) {
      return null;
    }
    const commitReason = reason || ctx.state.pendingCommitReason || 'speech_commit';
    ctx.state.pendingCommitReason = null;
    return openTurn(ctx, commitReason);
  }

  if (reason && !ctx.state.pendingCommitReason) {
    ctx.state.pendingCommitReason = reason;
  }

  if (!ctx.state.pendingCommitReason) {
    return null;
  }

  if (!shouldCommitTurn(ctx)) {
    return null;
  }

  const commitReason = ctx.state.pendingCommitReason || reason || 'speech_commit';
  ctx.state.pendingCommitReason = null;
  return openTurn(ctx, commitReason);
}

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
  const evidenceCfg = ctx.config?.evidence ?? {};
  const snrDetail = Number.isFinite(detail?.snr)
    ? detail.snr
    : Number.isFinite(detail?.snrDb)
      ? detail.snrDb
      : 0;
  const snr01 = clamp01(snrDetail / 20);
  const voiced = Number.isFinite(detail?.voicedRatio) ? detail.voicedRatio : 0;
  const asrConf = Number.isFinite(ctx.state?.asr?.lastConf) ? ctx.state.asr.lastConf : 0;
  const w1 = Number.isFinite(evidenceCfg.w_snr) ? evidenceCfg.w_snr : 0.4;
  const w2 = Number.isFinite(evidenceCfg.w_voiced) ? evidenceCfg.w_voiced : 0.2;
  const w3 = Number.isFinite(evidenceCfg.w_asr) ? evidenceCfg.w_asr : 0.6;
  const score = (w1 * snr01) + (w2 * voiced) + (w3 * asrConf);
  const commitScore = Number.isFinite(evidenceCfg.commit_score) ? evidenceCfg.commit_score : 1.0;

  if (score >= commitScore) {
    openTurn(ctx);
  }
  if (result.shouldCommit) {
    ctx.evidenceGate.satisfy('commit');
    const pending = maybeCommitSpeech(ctx, 'evidence_gate_commit');
    if (pending && typeof pending.then === 'function') {
      pending.catch(() => {});
    }
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
  clearDualVadTimer(ctx);
  ctx.state.bargeConfirmActive = true;
  ctx.state.bargeConfirmUntil = nowMs() + BARGE_CONFIRM_MS;
  setState(ctx, TurnState.Recording, { detail });
  const stats = ctx.shadowBuffer.stats();
  ctx.evidenceGate.start({ startedAt: nowMs(), detail, bufferedMs: stats.durationMs, bufferedBytes: stats.totalBytes });
  emitVoiceEvent('speech_start', detail);
  await evaluateEvidenceGate(ctx, { detail, vadState: 'speech' });
  const commitAttempt = maybeCommitSpeech(ctx);
  if (commitAttempt && typeof commitAttempt.then === 'function') {
    commitAttempt.catch(() => {});
  }
};

const handleSpeechEnd = async (ctx, detail) => {
  if (ctx.state.manualGate) {
    return;
  }
  ctx.state.bargeConfirmActive = false;
  ctx.state.bargeConfirmUntil = 0;
  emitVoiceEvent('speech_end', detail);
  await evaluateEvidenceGate(ctx, { detail, vadState: 'silence', asrCue: { type: 'vad_end' } });
  if (attemptCloseWithReason(ctx, 'vad_end', 'vad_end')) {
    return;
  }
  setState(ctx, TurnState.Listening);
  scheduleDualVadTimer(ctx);
};

const ensureVad = (ctx, opts = {}) => {
  const { audio } = ctx;
  if (audio.vad) return;
  const echoStateFn = () => isTtsMaskActive(ctx);
  audio.vad = new VAD(audio.analyser, {
    startDbOffset: 6 + ctx.state.vadBoostDb,
    stopDbOffset: 6 + ctx.state.vadBoostDb,
    echoStateFn,
    minSpeechMs: Math.max(160, opts.minSpeechMs ?? 200),
    minSilenceMs: Math.max(180, opts.minSilenceMs ?? 300),
    gateFn: () => !isTtsMaskActive(ctx),
  }, {
    onSpeechStart: (detail) => {
      ctx.state.vadRecording = true;
      ctx.state.preCommitASRFeed = true;
      logPreCommitMode(ctx, ctx.state.turnOpen ? 'streaming' : 'asr_priming', {
        reason: 'speech_start',
      });
      handleSpeechStart(ctx, detail);
    },
    onSpeechEnd: (detail) => {
      ctx.state.vadRecording = false;
      if (!ctx.state.turnOpen) {
        ctx.state.preCommitASRFeed = false;
      }
      logPreCommitMode(ctx, ctx.state.turnOpen ? 'streaming' : 'shadow_only', {
        reason: 'speech_end',
      });
      handleSpeechEnd(ctx, detail);
    },
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
  ctx.state.pendingCommitReason = null;
  clearDualVadTimer(ctx);
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
