import { EvidenceGate, ShadowBuffer, flushShadowBuffer } from '../core/index.js';

const EMPTY_STATS = Object.freeze({ count: 0, durationMs: 0, totalBytes: 0 });

const isStreaming = (ctx = {}) => !!ctx.__adaptiveStreaming;

function setStreaming(ctx = {}, value = false) {
  if (ctx && typeof ctx === 'object') {
    ctx.__adaptiveStreaming = !!value;
  }
}

function ensureAdaptiveArtifacts(ctx = {}) {
  if (!ctx || typeof ctx !== 'object') {
    return { gate: null, buffer: null };
  }
  let gate = ctx.evidenceGate instanceof EvidenceGate ? ctx.evidenceGate : null;
  if (!gate) {
    gate = new EvidenceGate();
    ctx.evidenceGate = gate;
  }
  const maxMs = Number.isFinite(ctx?.PRE_ROLL_MS) ? ctx.PRE_ROLL_MS : undefined;
  let buffer = ctx.shadowBuffer instanceof ShadowBuffer ? ctx.shadowBuffer : null;
  if (!buffer) {
    buffer = new ShadowBuffer({ maxMs });
    ctx.shadowBuffer = buffer;
  }
  return { gate, buffer };
}

function firstFinite(...values) {
  for (const value of values) {
    if (Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function extractChunkMeta(frame = {}) {
  if (!frame || typeof frame !== 'object') {
    return { chunk: null, durationMs: null, timecode: null };
  }
  const meta = (frame.meta && typeof frame.meta === 'object') ? frame.meta : {};
  let chunk = frame.chunk ?? frame.audio ?? frame.buffer ?? frame.blob ?? meta.blob ?? null;
  if (chunk && typeof chunk === 'object' && chunk.blob) {
    chunk = chunk.blob;
  }
  return {
    chunk,
    durationMs: firstFinite(frame.durationMs, frame.chunkDurationMs, meta.durationMs),
    timecode: firstFinite(frame.timecode, frame.chunkTimecode, meta.timecode),
  };
}

function resolveSnr(ctx = {}, frame = {}) {
  const direct = [frame?.snrDb, frame?.snr, frame?.detail?.snrDb, frame?.metrics?.snrDb];
  for (const value of direct) {
    if (Number.isFinite(value)) {
      return value;
    }
  }
  const noiseModel = ctx?.state?.noiseModel;
  if (!noiseModel || typeof noiseModel.snr !== 'function') {
    return null;
  }
  for (const sample of [frame?.energy, frame?.samples, frame?.pcm, frame?.rms, frame?.audioSamples]) {
    if (!sample) {
      continue;
    }
    try {
      const snr = noiseModel.snr(sample);
      if (Number.isFinite(snr)) {
        return snr;
      }
    } catch {}
  }
  return null;
}

function sendChunkViaTransport(ctx = {}, chunk = null, meta = {}) {
  if (!ctx || !chunk) {
    return false;
  }
  const transport = ctx.transport && typeof ctx.transport === 'object' ? ctx.transport : null;
  const candidates = [
    transport && typeof transport.sendChunk === 'function' ? transport.sendChunk.bind(transport) : null,
    transport && typeof transport.sendAudioChunk === 'function' ? transport.sendAudioChunk.bind(transport) : null,
    transport && typeof transport.enqueue === 'function' ? transport.enqueue.bind(transport) : null,
    typeof ctx.sendChunk === 'function' ? ctx.sendChunk.bind(ctx) : null,
    typeof ctx.sendAudioChunk === 'function' ? ctx.sendAudioChunk.bind(ctx) : null,
  ];
  for (const fn of candidates) {
    if (!fn) {
      continue;
    }
    try {
      const result = fn(chunk, meta);
      if (result && typeof result.then === 'function') {
        result.catch(() => {});
      }
      return true;
    } catch {}
  }
  return false;
}

function flushContextShadowBuffer(ctx = {}, buffer = null) {
  if (!buffer) {
    return EMPTY_STATS;
  }
  let stats = EMPTY_STATS;
  try {
    stats = flushShadowBuffer(buffer, (chunk, meta) => {
      sendChunkViaTransport(ctx, chunk, { ...meta, preRoll: true });
    }) || EMPTY_STATS;
  } catch {}
  return {
    count: Number.isFinite(stats.count) ? stats.count : 0,
    durationMs: Number.isFinite(stats.durationMs) ? stats.durationMs : 0,
    totalBytes: Number.isFinite(stats.totalBytes) ? stats.totalBytes : 0,
  };
}

function processAdaptiveFrame(ctx = {}, frame = {}, vadState = 'silence') {
  const { gate, buffer } = ensureAdaptiveArtifacts(ctx);
  if (!gate || !buffer) {
    return { gateOpened: false };
  }

  const wasStreaming = isStreaming(ctx);
  const wasOpen = typeof gate.isOpen === 'function' ? gate.isOpen() : false;

  const manualOverride = !!(ctx?.state?.manual?.buttonDown);
  const mask = ctx && typeof ctx === 'object' ? ctx.ttsMask : null;
  const maskActive = !manualOverride && typeof mask?.isMasked === 'function' ? mask.isMasked() : false;
  const snrBoost = typeof mask?.snrBoost === 'function' ? mask.snrBoost() : 0;
  const skipGate = ctx?.skipEvidenceGate === true;

  const { chunk, durationMs, timecode } = extractChunkMeta(frame);
  if (!wasStreaming && chunk) {
    try {
      buffer.push(chunk, { durationMs, timecode });
    } catch {}
  }

  let stats = EMPTY_STATS;
  try {
    stats = buffer.stats() || EMPTY_STATS;
  } catch {
    stats = EMPTY_STATS;
  }

  const frameVadState = typeof frame?.vadState === 'string'
    ? frame.vadState
    : (typeof frame?.vad?.state === 'string' ? frame.vad.state : null);
  const effectiveVadState = frameVadState || vadState;
  const asrCue = frame?.asrCue ?? frame?.cue ?? frame?.detail?.asrCue ?? null;

  const updatePayload = {
    vadState: effectiveVadState,
    snr: resolveSnr(ctx, frame),
    asrCue,
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
  };

  if (updatePayload.asrCue && updatePayload.asrCue.type === 'partial') {
    ctx.hadPartial = true;
    if (ctx?.state) {
      ctx.state.turnMetricsHadPartial = true;
    }
  }

  if (snrBoost > 0) {
    updatePayload.snrBoost = snrBoost;
  }

  if (skipGate) {
    if (!wasStreaming) {
      flushContextShadowBuffer(ctx, buffer);
      setStreaming(ctx, true);
    }
    if (chunk) {
      sendChunkViaTransport(ctx, chunk, { durationMs, timecode, preRoll: false, vadState });
    }
    return {
      gateOpened: !wasStreaming,
      streamingNow: true,
      wasStreaming,
    };
  }

  try {
    gate.update(updatePayload);
  } catch {}

  if (maskActive) {
    setStreaming(ctx, false);
    return {
      gateOpened: false,
      streamingNow: false,
      wasStreaming,
    };
  }

  const nowOpen = typeof gate.isOpen === 'function' ? gate.isOpen() : false;

  if (!wasStreaming && !wasOpen && nowOpen) {
    flushContextShadowBuffer(ctx, buffer);
    setStreaming(ctx, true);
  } else if (!nowOpen) {
    setStreaming(ctx, false);
  }

  const streamingNow = isStreaming(ctx);
  if (streamingNow && wasStreaming && chunk) {
    sendChunkViaTransport(ctx, chunk, { durationMs, timecode, preRoll: false, vadState });
  }

  return {
    gateOpened: !wasOpen && nowOpen,
    streamingNow,
    wasStreaming,
  };
}

export function onFrameSilence(ctx, frame) {
  processAdaptiveFrame(ctx, frame, 'silence');
}

export async function onFrameSpeech(ctx = {}, frame = {}) {
  processAdaptiveFrame(ctx, frame, 'speech');

  const metrics = (frame && typeof frame === 'object') ? frame : {};
  const {
    state,
    VadFrameUtils = {},
    updateSessionNoise,
    getShadowStats,
    getEvidenceSnrRequirement,
    PRE_ROLL_MS,
    EVIDENCE_MIN_SNR_DB,
    EVIDENCE_MIN_SPEECH_MS,
    EVIDENCE_MIN_BYTES,
    GREET_BARGE_MIN_SNR_DB,
    REC_MIME,
    optsFromGlobal,
    emitVoiceEvent,
    startRecorder,
    primeRecorderForPreRoll,
    bargeIn,
    resetEvidenceGate,
    evaluateEvidenceGate,
    onSpeechStartCommitted,
    now,
    logLifecycle,
    voiceLog,
    beginTurnTrace,
    completeGreetGate,
    getActiveTurnTraceId,
  } = ctx || {};

  if (!state || !state.evidenceGate) {
    return;
  }

  const logLifecycleFn = typeof logLifecycle === 'function' ? logLifecycle : () => {};
  const voiceLogFn = typeof voiceLog === 'function' ? voiceLog : () => {};
  const beginTurnTraceFn = typeof beginTurnTrace === 'function' ? beginTurnTrace : () => {};
  const completeGreetGateFn = typeof completeGreetGate === 'function' ? completeGreetGate : () => {};
  const getActiveTurnTraceIdFn = typeof getActiveTurnTraceId === 'function' ? getActiveTurnTraceId : () => null;
  const nowFn = typeof now === 'function' ? now : () => Date.now();
  const updateSessionNoiseFn = typeof updateSessionNoise === 'function' ? updateSessionNoise : () => {};
  const getShadowStatsFn = typeof getShadowStats === 'function'
    ? getShadowStats
    : () => ({ count: 0, durationMs: 0, totalBytes: 0 });
  const evidenceSnrFallback = Number.isFinite(EVIDENCE_MIN_SNR_DB) ? EVIDENCE_MIN_SNR_DB : 0;
  const getEvidenceSnrRequirementFn = typeof getEvidenceSnrRequirement === 'function'
    ? getEvidenceSnrRequirement
    : () => evidenceSnrFallback;
  const optsFromGlobalFn = typeof optsFromGlobal === 'function' ? optsFromGlobal : () => undefined;
  const emitVoiceEventFn = typeof emitVoiceEvent === 'function' ? emitVoiceEvent : () => {};
  const startRecorderFn = typeof startRecorder === 'function' ? startRecorder : null;
  const primeRecorderForPreRollFn = typeof primeRecorderForPreRoll === 'function' ? primeRecorderForPreRoll : null;
  const bargeInFn = typeof bargeIn === 'function' ? bargeIn : null;
  const resetEvidenceGateFn = typeof resetEvidenceGate === 'function' ? resetEvidenceGate : null;
  const evaluateEvidenceGateFn = typeof evaluateEvidenceGate === 'function' ? evaluateEvidenceGate : null;
  const onSpeechStartCommittedFn = typeof onSpeechStartCommitted === 'function' ? onSpeechStartCommitted : null;

  VadFrameUtils?.refreshManualConfig?.();
  state.vadMetrics = { ...metrics, phase: 'start' };

  const shadowStats = getShadowStatsFn(state) || {};
  const bufferedMsRaw = Number.isFinite(shadowStats.durationMs) ? shadowStats.durationMs : 0;
  const totalBytes = Number.isFinite(shadowStats.totalBytes) ? shadowStats.totalBytes : 0;
  const preRollCount = Number.isFinite(shadowStats.count) ? shadowStats.count : 0;

  const round = (value) => (Number.isFinite(value) ? Math.round(value * 100) / 100 : 0);
  const roundTenths = (value) => (Number.isFinite(value) ? Math.round(value * 10) / 10 : null);

  const nowValue = nowFn();

  updateSessionNoiseFn(state, metrics);

  const manual = state.manual || {};
  const manualOnlyMode = !!manual.modeManualOnly;
  const allowAutoCommit = manualOnlyMode && !!manual.autoCommitWhenReady;
  if (manualOnlyMode && state.ttsPlaying) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'tts_active_manual_mode' });
    voiceLogFn('debug', 'speech start ignored while TTS playing (manual mode)');
    return;
  }
  if (manualOnlyMode && !allowAutoCommit) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'manual_mode_vad_disabled' });
    voiceLogFn('debug', 'speech start ignored — manual mode without auto-commit');
    return;
  }
  if (allowAutoCommit && !state.assistantReady) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'assistant_not_ready' });
    voiceLogFn('debug', 'speech start ignored — assistant not ready for auto-commit');
    return;
  }

  if (manual.ignoreVadUntil && nowValue < manual.ignoreVadUntil) {
    const remaining = Math.max(0, Math.round(manual.ignoreVadUntil - nowValue));
    voiceLogFn('debug', 'speech start suppressed during manual cooldown', { remainingMs: remaining });
    return;
  }

  const holdUntilTts = state.postTtsHoldUntil || 0;
  const waitMs = Math.max(0, holdUntilTts - nowValue);
  if (waitMs > 0) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'post_tts_hold', holdUntil: holdUntilTts, waitMs });
    VadFrameUtils?.clearPostTtsHoldTimer?.();
    try {
      voiceLogFn('info', 'speech start deferred by post-TTS hold', { holdUntil: holdUntilTts, waitMs });
    } catch {}
    state.postTtsHoldTimer = setTimeout(() => {
      state.postTtsHoldTimer = null;
      try {
        onSpeechStartCommittedFn?.(frame);
      } catch {}
    }, waitMs);
    return;
  }

  VadFrameUtils?.clearPostTtsHoldTimer?.();
  state.postTtsHoldUntil = 0;

  if (state.eligibility === 'holdoff') {
    state.eligibility = 'eligible';
  }

  const greetActive = state.greetGateActive
    && (state.greetGatePhase === 'pending' || state.greetGatePhase === 'calibrating');
  if (state.eligibility === 'blocked_pregreet' && !greetActive) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'pregreet_block' });
    return;
  }

  const holdUntil = state.postFinalHoldUntil || 0;

  if (state.finalized) {
    if (nowValue < holdUntil) {
      logLifecycleFn('vad_speech_start_suppressed', {
        reason: 'post_final_hold_finalized',
        holdUntil,
        now: nowValue,
      });
      return;
    }
    state.finalized = false;
  }

  if (nowValue < holdUntil) {
    logLifecycleFn('vad_speech_start_suppressed', {
      reason: 'post_final_hold',
      holdUntil,
      now: nowValue,
    });
    return;
  }

  if (state.postFinalHoldUntil) {
    state.postFinalHoldUntil = 0;
  }

  const traceActive = getActiveTurnTraceIdFn();
  if (!state.recStreaming || !traceActive) {
    beginTurnTraceFn('speech_start');
  }

  const commitMode = allowAutoCommit ? 'auto_commit' : 'vad';
  state.currentCommitMode = commitMode;
  if (commitMode === 'auto_commit') {
    state.assistantReady = false;
  }

  const preRollMs = Number.isFinite(PRE_ROLL_MS) ? PRE_ROLL_MS : 0;

  logLifecycleFn('vad_speech_start', {
    preRollBufferedMs: round(bufferedMsRaw),
    preRollSentMs: round(Math.min(bufferedMsRaw, preRollMs)),
    preRollChunks: preRollCount,
    preRollBytes: totalBytes,
    preRollEnabled: preRollCount > 0,
    preRollMime: (state.rec && state.rec.mimeType) || REC_MIME,
    snrDb: roundTenths(metrics?.snrDb),
    noiseFloorDb: roundTenths(metrics?.noiseFloorDb),
    thresholdStartDb: roundTenths(metrics?.thresholds?.startDb),
    commitMode,
  });
  voiceLogFn('info', 'speech started', {
    preRollChunks: preRollCount,
    preRollBytes: totalBytes,
    snrDb: roundTenths(metrics?.snrDb),
    commitMode,
  });

  if (commitMode === 'auto_commit') {
    if (!startRecorderFn) {
      voiceLogFn('warn', 'recorder unavailable — reverting to typing');
      emitVoiceEventFn('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
      return;
    }
    let ok = await startRecorderFn();
    if (!ok && primeRecorderForPreRollFn) {
      try {
        await primeRecorderForPreRollFn({ resetBuffer: false });
      } catch {}
      ok = await startRecorderFn();
    }
    if (ok) {
      emitVoiceEventFn('state', { state: 'recording' });
      return;
    }
    voiceLogFn('warn', 'recorder unavailable — reverting to typing');
    emitVoiceEventFn('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
    return;
  }

  if (state.greetGateActive) {
    if (state.greetGatePhase === 'calibrating') {
      voiceLogFn('info', 'speech start suppressed during greet calibration', {
        calibrateUntil: state.greetGateCalibrateUntil,
        snrDb: roundTenths(metrics?.snrDb),
      });
      return;
    }
    if (state.greetGatePhase === 'pending') {
      const snrDb = Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null;
      if (state.ttsPlaying) {
        voiceLogFn('info', 'speech start suppressed by greet gate while TTS playing', {
          snrDb: roundTenths(snrDb),
          ttsPlaying: true,
        });
        return;
      }
      const greetSnrThreshold = Number.isFinite(GREET_BARGE_MIN_SNR_DB) ? GREET_BARGE_MIN_SNR_DB : 0;
      if (Number.isFinite(snrDb) && snrDb >= greetSnrThreshold) {
        voiceLogFn('info', 'greet gate bypassed via barge-in', {
          snrDb: roundTenths(snrDb),
        });
        completeGreetGateFn('barge_in');
      } else {
        voiceLogFn('info', 'speech start suppressed by greet gate', {
          snrDb: roundTenths(metrics?.snrDb),
        });
        return;
      }
    }
  }

  const manualPressing = !!manual.buttonDown;
  if (state.ttsPlaying && !manualPressing) {
    logLifecycleFn('vad_speech_start_suppressed', { reason: 'tts_playback_active' });
    voiceLogFn('debug', 'speech start ignored while TTS playback active');
    return;
  }

  const ttsMaskActive = typeof state.ttsMask?.isMasked === 'function'
    ? state.ttsMask.isMasked(nowValue)
    : false;
  if (ttsMaskActive && !manualPressing) {
    const requiredSnr = getEvidenceSnrRequirementFn(state, nowFn, evidenceSnrFallback);
    const snrDb = Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null;
    if (!Number.isFinite(snrDb) || snrDb < requiredSnr) {
      logLifecycleFn('vad_speech_start_suppressed', {
        reason: 'tts_decay_guard',
        snrDb: roundTenths(snrDb),
        requiredSnrDb: roundTenths(requiredSnr),
        decayUntil: state.ttsMask.decayUntil?.(),
      });
      return;
    }
  } else if (ttsMaskActive && manualPressing) {
    voiceLogFn('debug', 'manual barge-in bypassed TTS decay guard');
  }

  bargeInFn?.();

  if (!state.evidenceGate.isOpen()) {
    resetEvidenceGateFn?.();
    state.evidenceGate.start({
      startedAt: nowValue,
      detail: metrics || null,
      bufferedMs: bufferedMsRaw,
      bufferedBytes: totalBytes,
    });
    const minSpeechDefault = Number.isFinite(EVIDENCE_MIN_SPEECH_MS) ? EVIDENCE_MIN_SPEECH_MS : 0;
    const minBytesDefault = Number.isFinite(EVIDENCE_MIN_BYTES) ? EVIDENCE_MIN_BYTES : 0;
    const requiredSnr = getEvidenceSnrRequirementFn(state, nowFn, evidenceSnrFallback);
    const minSpeechOpt = Number(optsFromGlobalFn('evidence_min_speech_ms', minSpeechDefault));
    const minSpeechMs = Number.isFinite(minSpeechOpt) ? Math.max(0, minSpeechOpt) : minSpeechDefault;
    const minBytesOpt = Number(optsFromGlobalFn('evidence_min_bytes', minBytesDefault));
    const minBytes = Number.isFinite(minBytesOpt) ? Math.max(0, minBytesOpt) : minBytesDefault;
    state.evidenceGate.update({
      vadState: 'speech',
      snr: Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null,
      snrBoost: Math.max(0, requiredSnr - evidenceSnrFallback),
      bufferedMs: bufferedMsRaw,
      bufferedBytes: totalBytes,
      minSpeechMs,
      minBytes,
    });
    emitVoiceEventFn('state', { state: 'recording', gated: true });
  } else if (typeof state.evidenceGate.setDetail === 'function') {
    state.evidenceGate.setDetail(metrics || state.evidenceGate.lastDetail);
  }

  evaluateEvidenceGateFn?.('vad_start');
}
