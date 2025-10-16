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
  } = ctx || {};
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
  VadFrameUtils?.maybeSendAudioStop?.({ reason });
  VadFrameUtils?.safeClearTurnTimer?.();
  VadFrameUtils?.clearPendingEndTimer?.();
  clearSafetyCloseTimer?.();
  stopRecorder?.({ reason });
}
