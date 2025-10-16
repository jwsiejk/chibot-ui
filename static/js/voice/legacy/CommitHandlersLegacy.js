import { getConfig } from '../core/index.js';

export async function onSpeechStartCommitted(ctx = {}, detail = {}) {
  const {
    onFrameSpeech,
    state,
    VadFrameUtils,
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
    onSpeechStartCommitted: nestedOnSpeechStartCommitted,
    now,
    logLifecycle,
    voiceLog,
    beginTurnTrace,
    completeGreetGate,
    getActiveTurnTraceId,
    ttsMask,
    autoVadMasked,
  } = ctx || {};

  if (typeof onFrameSpeech !== 'function') {
    throw new Error('CommitHandlersLegacy.onSpeechStartCommitted requires onFrameSpeech');
  }

  const config = getConfig();
  const commitConfig = (config && typeof config === 'object' ? config.commit : null) || {};
  const timeoutMsRaw = Number(commitConfig.no_partial_timeout_ms);
  const timeoutMs = Number.isFinite(timeoutMsRaw) ? Math.max(0, timeoutMsRaw) : 1200;
  const SMALL_MASS_BYTES = 12 * 1024;

  const clearNoPartialTimer = () => {
    if (ctx.__softNoPartialTimer) {
      try { clearTimeout(ctx.__softNoPartialTimer); } catch {}
      ctx.__softNoPartialTimer = null;
    }
  };

  const resetSoftNoPartialState = () => {
    ctx.__softNoPartialHandleOpen = null;
    clearNoPartialTimer();
  };

  ctx.__softNoPartialClear = resetSoftNoPartialState;

  const handleSoftNoPartialTimeout = () => {
    ctx.__softNoPartialTimer = null;
    if (ctx.hadPartial) {
      voiceLog?.('debug', 'soft no-partial timer ignored — partial received');
      return;
    }

    const gateInstance = state?.evidenceGate || ctx.evidenceGate || null;
    const threshold = Number(gateInstance?.evidence?.threshold);
    const score = Number(gateInstance?.evidenceScore);
    const evidenceBelowThreshold = Number.isFinite(threshold)
      && Number.isFinite(score)
      && score < threshold;

    if (!evidenceBelowThreshold) {
      voiceLog?.('debug', 'soft no-partial timer skipped — evidence still sufficient', {
        threshold: Number.isFinite(threshold) ? threshold : null,
        score: Number.isFinite(score) ? score : null,
      });
      resetSoftNoPartialState();
      return;
    }

    const bytesSent = Number.isFinite(state?.chunkBytesSent) ? state.chunkBytesSent : 0;
    if (bytesSent >= SMALL_MASS_BYTES) {
      voiceLog?.('debug', 'soft no-partial timer skipped — audio mass already sent', {
        bytesSent,
        limit: SMALL_MASS_BYTES,
      });
      resetSoftNoPartialState();
      return;
    }

    if (gateInstance && typeof gateInstance.isOpen === 'function' && !gateInstance.isOpen()) {
      voiceLog?.('debug', 'soft no-partial timer skipped — gate already closed');
      resetSoftNoPartialState();
      return;
    }

    const dropReason = 'soft_no_partial_timeout';
    voiceLog?.('info', 'soft no-partial timeout firing', {
      bytesSent,
      evidenceScore: Number.isFinite(score) ? score : null,
      threshold: Number.isFinite(threshold) ? threshold : null,
    });

    try { VadFrameUtils?.clearPendingEndTimer?.(); } catch {}
    try { VadFrameUtils?.safeClearTurnTimer?.(); } catch {}
    try { clearSafetyCloseTimer?.(); } catch {}

    if (state) {
      state.recStreaming = false;
      state.recStopShouldSend = false;
    }

    try {
      stopRecorder?.({ reason: dropReason });
    } catch {}

    if (state) {
      state.recStopping = false;
      state.turnOpen = false;
    }

    if (ctx?.shadowBuffer && typeof ctx.shadowBuffer.clear === 'function') {
      try { ctx.shadowBuffer.clear(); } catch {}
    }
    if (state?.shadowBuffer && typeof state.shadowBuffer.clear === 'function' && state.shadowBuffer !== ctx?.shadowBuffer) {
      try { state.shadowBuffer.clear(); } catch {}
    }

    if (ctx?.evidenceGate && typeof ctx.evidenceGate.reset === 'function') {
      try { ctx.evidenceGate.reset(dropReason); } catch {}
    } else if (typeof resetEvidenceGate === 'function') {
      try { resetEvidenceGate(dropReason); } catch {}
    }
    if (state?.evidenceGate && state.evidenceGate !== ctx?.evidenceGate && typeof state.evidenceGate.reset === 'function') {
      try { state.evidenceGate.reset(dropReason); } catch {}
    }

    emitVoiceEvent?.('state', { state: 'armed' });
    if (state) {
      state.currentCommitMode = 'idle';
    }

    resetSoftNoPartialState();
  };

  const startNoPartialTimer = () => {
    if (timeoutMs <= 0) {
      return;
    }
    clearNoPartialTimer();
    try {
      ctx.__softNoPartialTimer = setTimeout(handleSoftNoPartialTimeout, timeoutMs);
    } catch (err) {
      voiceLog?.('debug', 'failed to arm soft no-partial timer', { error: err?.message || err });
      ctx.__softNoPartialTimer = null;
      return;
    }
    voiceLog?.('debug', 'soft no-partial timer armed', { timeoutMs });
  };

  ctx.__softNoPartialHandleOpen = startNoPartialTimer;

  const descriptor = Object.getOwnPropertyDescriptor(ctx, 'hadPartial');
  let hadPartialValue = false;
  if (!descriptor || descriptor.configurable) {
    Object.defineProperty(ctx, 'hadPartial', {
      configurable: true,
      enumerable: true,
      get() {
        return hadPartialValue;
      },
      set(value) {
        hadPartialValue = !!value;
        if (hadPartialValue) {
          resetSoftNoPartialState();
        }
      },
    });
  } else {
    hadPartialValue = !!ctx.hadPartial;
  }
  ctx.hadPartial = false;

  const evidenceGate = state?.evidenceGate;
  if (evidenceGate && typeof evidenceGate.update === 'function' && !evidenceGate.__softNoPartialWrapped) {
    const originalUpdate = evidenceGate.update;
    evidenceGate.update = function softNoPartialWrappedUpdate(...updateArgs) {
      const wasOpen = typeof evidenceGate.isOpen === 'function' ? evidenceGate.isOpen() : false;
      const result = originalUpdate.apply(this, updateArgs);
      const nowOpen = typeof evidenceGate.isOpen === 'function' ? evidenceGate.isOpen() : false;
      if (!wasOpen && nowOpen) {
        try { ctx.__softNoPartialHandleOpen?.(); } catch {}
      }
      return result;
    };
    evidenceGate.__softNoPartialWrapped = true;
  }

  if (typeof evidenceGate?.isOpen === 'function' && evidenceGate.isOpen()) {
    try { startNoPartialTimer(); } catch {}
  }

  const manualPressDuringTts = !!(state?.manual?.buttonDown) && state?.ttsPlaying;
  if (manualPressDuringTts) {
    try { bargeIn?.(); } catch {}
    if (!ctx.ttsMask && state?.ttsMask) {
      ctx.ttsMask = state.ttsMask;
    }
    ctx.skipEvidenceGate = true;
  } else {
    ctx.skipEvidenceGate = false;
  }

  if (!ctx.ttsMask && ttsMask) {
    ctx.ttsMask = ttsMask;
  }
  if (typeof autoVadMasked === 'boolean' && typeof ctx.autoVadMasked === 'undefined') {
    ctx.autoVadMasked = autoVadMasked;
  }

  const nextHandler =
    typeof nestedOnSpeechStartCommitted === 'function'
      ? nestedOnSpeechStartCommitted
      : (nextDetail = {}) => onSpeechStartCommitted(ctx, nextDetail);

  return await onFrameSpeech(
    {
      state,
      VadFrameUtils,
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
      onSpeechStartCommitted: nextHandler,
      now,
      logLifecycle,
      voiceLog,
      beginTurnTrace,
      completeGreetGate,
      getActiveTurnTraceId,
      ttsMask: ctx.ttsMask || state?.ttsMask || null,
      skipEvidenceGate: manualPressDuringTts,
      manualPressingDuringTts: manualPressDuringTts,
      autoVadMasked: ctx.autoVadMasked === true,
    },
    detail,
  );
}

export function onSpeechEndCommitted(ctx = {}, detail = null) {
  const {
    state,
    voiceLog,
    updateSessionNoise,
    abortEvidenceGate,
    VadFrameUtils,
    optsFromGlobal,
    clearSafetyCloseTimer,
    stopRecorder,
    onSpeechEndCommitted: nestedOnSpeechEndCommitted,
    logLifecycle,
    resetEvidenceGate,
    emitVoiceEvent,
    closeTurnIfOpen,
  } = ctx || {};
  if (typeof ctx?.__softNoPartialClear === 'function') {
    try { ctx.__softNoPartialClear(); } catch {}
  }
  ctx.__softNoPartialHandleOpen = null;
  const perf = ctx?.performance ?? (typeof performance !== 'undefined' ? performance : null);
  const metrics = detail && typeof detail === 'object' ? detail : {};
  const reason =
    detail && typeof detail === 'object' && detail.reason
      ? detail.reason
      : typeof detail === 'string'
      ? detail
      : 'vad_silence';
  const now = typeof perf?.now === 'function' ? perf.now() : Date.now();
  const minTurnMs = Number(optsFromGlobal?.('min_turn_ms', 1200));

  if (state?.manual?.ignoreVadUntil && now < state.manual.ignoreVadUntil) {
    const remaining = Math.max(0, Math.round(state.manual.ignoreVadUntil - now));
    voiceLog?.('debug', 'speech end suppressed during manual cooldown', { remainingMs: remaining });
    return;
  }

  if (state) {
    state.vadMetrics = { ...metrics, phase: 'end' };
  }

  const roundTenths = (v) => {
    if (!Number.isFinite(v)) return null;
    return Math.round(v * 10) / 10;
  };

  updateSessionNoise?.(state, metrics);

  voiceLog?.('info', 'speech ended', {
    source: 'vad',
    reason,
    snrDb: roundTenths(metrics?.snrDb),
    durationMs: Number.isFinite(metrics?.speechDurationMs) ? Math.round(metrics.speechDurationMs) : null,
  });

  if (!state?.recStreaming && state?.evidenceGate?.isOpen?.()) {
    abortEvidenceGate?.(reason || 'evidence_not_met', metrics);
    return;
  }

  if (state?.bargeConfirmActive && typeof VadFrameUtils?.clearBargeConfirm === 'function') {
    VadFrameUtils.clearBargeConfirm(true);
  }

  if (state?.rec && typeof state.rec.state === 'string' && state.rec.state === 'recording') {
    const elapsed = Math.max(0, now - (state.recStartedAt || now));
    const wait = Math.max(0, minTurnMs - elapsed);
    if (wait > 0) {
      voiceLog?.('debug', 'delaying VAD end', { waitMs: wait, elapsed });
      VadFrameUtils?.clearPendingEndTimer?.();
      const next =
        typeof nestedOnSpeechEndCommitted === 'function'
          ? () => nestedOnSpeechEndCommitted(detail)
          : () => onSpeechEndCommitted(ctx, detail);
      state.pendingEndTimer = setTimeout(next, wait);
      return;
    }
  }

  logLifecycle?.(
    'vad_speech_end',
    {
      reason,
      snrDb: roundTenths(metrics?.snrDb),
      noiseFloorDb: roundTenths(metrics?.noiseFloorDb),
      speechDurationMs: Number.isFinite(metrics?.speechDurationMs) ? Math.round(metrics.speechDurationMs) : null,
      thresholdStopDb: roundTenths(metrics?.thresholds?.stopDb),
    },
    'info',
  );

  const config = getConfig();
  const rawMinMs = Number(config?.commit?.min_ms);
  const commitMinMs = Number.isFinite(rawMinMs) ? Math.max(0, rawMinMs) : 0;
  let totalSpeechMs = null;
  if (detail && typeof detail === 'object' && Number.isFinite(detail.totalSpeechMs)) {
    totalSpeechMs = detail.totalSpeechMs;
  } else if (Number.isFinite(metrics?.speechDurationMs)) {
    totalSpeechMs = metrics.speechDurationMs;
  }
  const dropForMinSpeech =
    !ctx?.hadPartial && Number.isFinite(totalSpeechMs) && totalSpeechMs < commitMinMs;

  const dropReason = 'commit_min_duration';

  if (dropForMinSpeech) {
    if (typeof ctx?.__softNoPartialClear === 'function') {
      try { ctx.__softNoPartialClear(); } catch {}
    }
    VadFrameUtils?.maybeSendAudioStop?.({ reason: dropReason });
    VadFrameUtils?.safeClearTurnTimer?.();
    VadFrameUtils?.clearPendingEndTimer?.();
    clearSafetyCloseTimer?.();
    stopRecorder?.({ reason: dropReason });
    if (ctx?.shadowBuffer && typeof ctx.shadowBuffer.clear === 'function') {
      try { ctx.shadowBuffer.clear(); } catch {}
    }
    if (state?.shadowBuffer && typeof state.shadowBuffer.clear === 'function' && state.shadowBuffer !== ctx?.shadowBuffer) {
      try { state.shadowBuffer.clear(); } catch {}
    }
    if (ctx?.evidenceGate && typeof ctx.evidenceGate.reset === 'function') {
      try { ctx.evidenceGate.reset(dropReason); } catch {}
    } else if (typeof resetEvidenceGate === 'function') {
      try { resetEvidenceGate(dropReason); } catch {}
    }
    if (state?.evidenceGate && typeof state.evidenceGate.reset === 'function' && state.evidenceGate !== ctx?.evidenceGate) {
      try { state.evidenceGate.reset(dropReason); } catch {}
    }
    if (state) {
      state.currentCommitMode = 'idle';
    }
    ctx.hadPartial = false;
    emitVoiceEvent?.('state', { state: 'armed' });
    emitVoiceEvent?.('debug', {
      event: 'commit_drop_min_duration',
      totalSpeechMs: Number.isFinite(totalSpeechMs) ? Math.round(totalSpeechMs) : null,
      minSpeechMs: commitMinMs,
      hadPartial: false,
    });
    return;
  }

  VadFrameUtils?.maybeSendAudioStop?.({ reason });
  VadFrameUtils?.safeClearTurnTimer?.();
  VadFrameUtils?.clearPendingEndTimer?.();
  clearSafetyCloseTimer?.();
  stopRecorder?.({ reason });
  closeTurnIfOpen?.();
}
