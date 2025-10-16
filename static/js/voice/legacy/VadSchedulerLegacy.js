import { VAD } from '../vad.js';

const activeLoops = new WeakMap();

export function startVadLoop(ctx, onFrame) {
  if (!ctx || typeof onFrame !== 'function') {
    return null;
  }

  const previous = activeLoops.get(ctx);
  if (previous?.vad && typeof previous.vad.stop === 'function') {
    try { previous.vad.stop(); } catch {}
  }

  const config = onFrame();
  if (!config || typeof config !== 'object') {
    activeLoops.delete(ctx);
    return null;
  }

  const {
    analyser = ctx.analyser,
    cfg = {},
    pollMs: pollOverride,
    onSpeechStart,
    onSpeechEnd,
    ttsIsPlaying,
  } = config;

  const pollMs = Number.isFinite(pollOverride)
    ? pollOverride
    : (Number.isFinite(cfg.pollMs) ? cfg.pollMs : 33);

  const options = {
    pollMs,
    minSpeechMs: cfg.minSpeechMs ?? 280,
    minSilenceMs: cfg.minSilenceMs ?? 300,
    cooldownMs: cfg.cooldownMs ?? 380,
    startDbOffset: cfg.startDbOffset ?? 10,
    stopDbOffset: cfg.stopDbOffset ?? 6,
    minStartDb: cfg.minStartDb ?? -65,
    minStopDb: cfg.minStopDb ?? -70,
    echoBoostStartDb: cfg.echoBoostStartDb ?? 8,
    echoBoostStopDb: cfg.echoBoostStopDb ?? 6,
    noiseFloorAlpha: cfg.noiseFloorAlpha ?? 0.05,
    noiseFloorRiseAlpha: cfg.noiseFloorRiseAlpha ?? 0.01,
    noiseFloorGuardDb: cfg.noiseFloorGuardDb ?? 3,
    noiseFloorHangMs: cfg.noiseFloorHangMs ?? 600,
    initialNoiseFloorDb: cfg.initialNoiseFloorDb,
    startRms: cfg.startRms,
    stopRms: cfg.stopRms,
    echoStateFn: () => {
      if (typeof ttsIsPlaying === 'function') {
        try { return !!ttsIsPlaying(); } catch { return false; }
      }
      return false;
    },
  };

  const callbacks = {};
  if (typeof onSpeechStart === 'function') {
    callbacks.onSpeechStart = onSpeechStart;
  }
  if (typeof onSpeechEnd === 'function') {
    callbacks.onSpeechEnd = onSpeechEnd;
  }

  const vad = new VAD(analyser, options, callbacks);
  vad.start();

  const entry = { vad, pollMs };
  activeLoops.set(ctx, entry);
  return entry;
}

export function stopVadLoop(ctx) {
  if (!ctx) {
    return;
  }
  const entry = activeLoops.get(ctx);
  if (entry?.vad && typeof entry.vad.stop === 'function') {
    try { entry.vad.stop(); } catch {}
  }
  activeLoops.delete(ctx);
}

export function createVadSchedulerLegacy(deps = {}) {
  const {
    state,
    emitVoiceEvent,
    sendJSON,
    stopPlayback,
    ttsIsPlaying,
    startVadLoop,
    facadeOnWsMessage,
    legacySetGreetGateActive,
    legacyAbortEvidenceGate,
    legacyEvaluateEvidenceGate,
    legacyCommitEvidenceGate,
    legacyUpdateEvidenceGateWithChunk,
    legacyUpdateEvidenceGateWithPartial,
    legacyPrimeRecorderForPreRoll,
    legacyStartRecorder,
    legacyStopRecorder,
    legacyClearManualTimers,
    legacyCloseTurnIfOpen,
    VadFrameUtils,
    now: nowFn,
    logLifecycle,
    voiceLog,
    MANUAL_DEBOUNCE_MS = 300,
    MANUAL_NO_AUDIO_CANCEL_MS = 500,
    MANUAL_VAD_IGNORE_MS = 600,
    onSpeechEndCommitted,
    onSpeechStartCommitted,
  } = deps;

  function setGreetGateActive(active) {
    if (typeof legacySetGreetGateActive === 'function') {
      legacySetGreetGateActive(active, { ensureWsListener: ensureWSListener });
    }
  }

  function abortEvidenceGate(reason, detail = null) {
    if (typeof legacyAbortEvidenceGate === 'function') {
      legacyAbortEvidenceGate(reason, detail);
    }
  }

  function evaluateEvidenceGate(trigger = 'poll') {
    if (typeof legacyEvaluateEvidenceGate !== 'function') {
      return false;
    }
    return legacyEvaluateEvidenceGate(trigger, {
      startRecorder: () => startRecorder(),
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
    });
  }

  async function commitEvidenceGate(trigger = 'unknown') {
    if (typeof legacyCommitEvidenceGate !== 'function') {
      return false;
    }
    return legacyCommitEvidenceGate(trigger, {
      startRecorder: () => startRecorder(),
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
    });
  }

  function updateEvidenceGateWithChunk(durationMs, bytes) {
    if (typeof legacyUpdateEvidenceGateWithChunk !== 'function') {
      return;
    }
    legacyUpdateEvidenceGateWithChunk(durationMs, bytes, {
      startRecorder: () => startRecorder(),
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
    });
  }

  function updateEvidenceGateWithPartial(confidence = null, transcript = '') {
    if (typeof legacyUpdateEvidenceGateWithPartial !== 'function') {
      return;
    }
    legacyUpdateEvidenceGateWithPartial(confidence, transcript, {
      startRecorder: () => startRecorder(),
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
    });
  }

  function manualAutoCancel(reason = 'no_audio') {
    const manual = state.manual || {};
    if (!manual.buttonDown && !manual.active) {
      return;
    }
    if (typeof logLifecycle === 'function') {
      logLifecycle('manual_barge_in_auto_cancel', { reason });
    }
    try {
      if (typeof forceBargeInEnd === 'function') {
        forceBargeInEnd({ autoCancel: true, reason });
      }
    } catch (err) {
      if (typeof voiceLog === 'function') {
        voiceLog('warn', 'manual auto cancel failed', { error: err?.message || err, reason });
      }
    }
  }

  function forceBargeInStart(meta = {}) {
    const manual = state.manual || {};
    const enabled = typeof VadFrameUtils?.refreshManualConfig === 'function'
      ? VadFrameUtils.refreshManualConfig()
      : true;
    if (!enabled) {
      if (typeof voiceLog === 'function') {
        voiceLog('debug', 'manual barge-in start ignored — feature disabled');
      }
      return false;
    }

    const now = Date.now();
    if (manual.buttonDown) {
      return true;
    }
    if (now < manual.debounceUntil) {
      const remaining = Math.max(0, manual.debounceUntil - now);
      if (typeof voiceLog === 'function') {
        voiceLog('debug', 'manual barge-in start debounced', { remainingMs: remaining });
      }
      return false;
    }

    manual.buttonDown = true;
    manual.active = false;
    manual.deferCloseStream = false;
    manual.sentStartFrame = false;
    manual.startAt = typeof nowFn === 'function' ? nowFn() : Date.now();
    manual.firstChunkAt = 0;
    manual.ignoreVadUntil = 0;
    manual.debounceUntil = now + MANUAL_DEBOUNCE_MS;
    if (typeof legacyClearManualTimers === 'function') {
      legacyClearManualTimers();
    }

    bargeIn();

    state.currentCommitMode = 'manual';
    state.assistantReady = false;

    const source = (typeof meta === 'object' && meta && meta.source)
      ? String(meta.source)
      : 'ui';
    if (typeof logLifecycle === 'function') {
      logLifecycle('manual_barge_in_start', { source });
    }
    try { console.info?.('manual_barge_in_start'); } catch {}

    try {
      const sent = typeof sendJSON === 'function'
        ? sendJSON({ type: 'Control', action: 'barge_in_start' })
        : null;
      manual.sentStartFrame = sent === true;
    } catch (err) {
      if (typeof voiceLog === 'function') {
        voiceLog('warn', 'failed to send manual barge-in start frame', { error: err?.message || err });
      }
      manual.sentStartFrame = false;
    }

    Promise.resolve(startRecorder()).then((started) => {
      if (!started) {
        manual.buttonDown = false;
        manual.active = false;
        manual.deferCloseStream = false;
        if (manual.sentStartFrame) {
          try { if (typeof sendJSON === 'function') { sendJSON({ type: 'Control', action: 'barge_in_end' }); } } catch {}
          manual.sentStartFrame = false;
        }
        if (typeof voiceLog === 'function') {
          voiceLog('warn', 'recorder unavailable — reverting to typing (manual barge-in)');
        }
        emitVoiceEvent?.('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
        return;
      }

      manual.active = true;
      manual.deferCloseStream = false;
      emitVoiceEvent?.('state', { state: 'recording' });
      if (typeof legacyClearManualTimers === 'function') {
        legacyClearManualTimers();
      }
      try {
        manual.noAudioTimer = setTimeout(() => manualAutoCancel('no_audio'), MANUAL_NO_AUDIO_CANCEL_MS);
      } catch {}
    }).catch((err) => {
      manual.buttonDown = false;
      manual.active = false;
      manual.deferCloseStream = false;
      if (typeof voiceLog === 'function') {
        voiceLog('warn', 'manual barge-in start failed', { error: err?.message || err });
      }
      emitVoiceEvent?.('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
    });

    return true;
  }

  function forceBargeInEnd(opts = {}) {
    const manual = state.manual || {};
    const enabled = typeof VadFrameUtils?.refreshManualConfig === 'function'
      ? VadFrameUtils.refreshManualConfig()
      : true;
    if (!enabled) {
      return false;
    }

    const active = manual.buttonDown || manual.active;
    if (!active) {
      return false;
    }

    const options = (opts && typeof opts === 'object') ? opts : {};
    const autoCancel = options.autoCancel === true;
    const reason = options.reason || (autoCancel ? 'auto_cancel' : 'release');

    manual.buttonDown = false;
    manual.ignoreVadUntil = (typeof nowFn === 'function' ? nowFn() : Date.now()) + MANUAL_VAD_IGNORE_MS;
    manual.debounceUntil = Date.now() + MANUAL_DEBOUNCE_MS;
    manual.deferCloseStream = true;
    if (typeof legacyClearManualTimers === 'function') {
      legacyClearManualTimers();
    }

    if (typeof logLifecycle === 'function') {
      logLifecycle('manual_barge_in_end', { reason, auto_cancel: autoCancel });
    }
    try { console.info?.('manual_barge_in_end'); } catch {}

    try {
      if (typeof sendJSON === 'function') {
        sendJSON({ type: 'Control', action: 'barge_in_end' });
      }
    } catch (err) {
      if (typeof voiceLog === 'function') {
        voiceLog('warn', 'failed to send manual barge-in end frame', { error: err?.message || err });
      }
    }
    manual.sentStartFrame = false;

    if (state.rec && state.rec.state === 'recording') {
      if (typeof legacyStopRecorder === 'function') {
        legacyStopRecorder({ reason: autoCancel ? 'manual_auto_cancel' : 'manual_release' });
      }
    } else {
      manual.active = false;
      manual.deferCloseStream = false;
    }

    return true;
  }

  function ensureWSListener() {
    if (state.wsListener || typeof window === 'undefined') {
      return;
    }
    const handler = async (ev) => {
      const detail = ev?.detail || {};
      if (typeof facadeOnWsMessage === 'function') {
        await facadeOnWsMessage(detail, {
          updateEvidenceGateWithPartial,
        });
      }
    };

    try { window.addEventListener('askchip-ws', handler); } catch {}
    state.wsListener = handler;
  }

  function primeRecorderForPreRoll(options = {}) {
    if (typeof legacyPrimeRecorderForPreRoll !== 'function') {
      return Promise.resolve(false);
    }
    return legacyPrimeRecorderForPreRoll(options, {
      updateEvidenceGateWithChunk,
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
    });
  }

  async function startRecorder() {
    if (typeof legacyStartRecorder !== 'function') {
      return false;
    }
    return legacyStartRecorder({
      primeRecorderForPreRoll: (opts = {}) => primeRecorderForPreRoll(opts),
      clearPendingEndTimer: () => VadFrameUtils?.clearPendingEndTimer?.(),
      ensureWSListener: ensureWSListener,
      onSpeechEndCommitted,
      clearTurnTimer: () => VadFrameUtils?.safeClearTurnTimer?.(),
    });
  }

  function bargeIn() {
    if (typeof VadFrameUtils?.clearBargeConfirm === 'function') {
      VadFrameUtils.clearBargeConfirm(false);
    }
    try { stopPlayback?.(); } catch {}
    const pendingClose = typeof legacyCloseTurnIfOpen === 'function'
      ? legacyCloseTurnIfOpen()
      : null;
    if (pendingClose) {
      pendingClose.catch(() => {});
    }
  }

  async function arm(stream = null, opts = {}) {
    const mic = stream || await VadFrameUtils?.ensureMic?.();

    VadFrameUtils?.teardownVadOnly?.();

    let globalVad = {};
    try { globalVad = (window.__askchip_config && window.__askchip_config.vad) || {}; } catch {}
    const cfg = { ...globalVad, ...opts };

    const pollMs = cfg.pollMs ?? 33;
    const vadLoop = typeof startVadLoop === 'function'
      ? startVadLoop(state, () => ({
        analyser: state.analyser,
        cfg,
        pollMs,
        onSpeechStart: onSpeechStartCommitted,
        onSpeechEnd: onSpeechEndCommitted,
        ttsIsPlaying,
      }))
      : null;

    if (!vadLoop || !vadLoop.vad) {
      return mic;
    }

    state.vad = vadLoop.vad;
    const effectivePollMs = Number.isFinite(vadLoop.pollMs) ? vadLoop.pollMs : pollMs;

    if (typeof logLifecycle === 'function') {
      logLifecycle('mic_start', {
        sampleRate: state.ctx?.sampleRate,
        pollMs: effectivePollMs,
      });
    }
    emitVoiceEvent?.('state', { state: 'armed' });

    await primeRecorderForPreRoll();

    return mic;
  }

  return {
    setGreetGateActive,
    abortEvidenceGate,
    evaluateEvidenceGate,
    commitEvidenceGate,
    updateEvidenceGateWithChunk,
    updateEvidenceGateWithPartial,
    manualAutoCancel,
    forceBargeInStart,
    forceBargeInEnd,
    ensureWSListener,
    primeRecorderForPreRoll,
    startRecorder,
    bargeIn,
    arm,
  };
}
