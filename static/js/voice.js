import { emitVoiceEvent } from './voice/ui/Events.js';
import {
  TurnState,
  bufferPreRollFrame,
  flushShadowBuffer,
  resetShadowBufferState,
} from './voice/core/index.js';
import { sendAudioChunk, sendCloseStream, sendJSON, waitWSOpen } from './ws_module.js';
import { stopPlayback, pausePlayback, resumePlayback, isPlaying as ttsIsPlaying } from './audio.js';
import { onTtsStart, onTtsEnd, registerTtsEventListener } from './voice/tts/TtsHandlers.js';
import {
  startVadLoop,
  stopVadLoop,
  updateSessionNoise,
  getEvidenceSnrRequirement,
  getShadowStats,
} from './voice/loops/VadLoop.js';
import {
  armVAD as facadeArmVAD,
  bargeIn as facadeBargeIn,
  disarmVAD as facadeDisarmVAD,
  forceBargeInEnd as facadeForceBargeInEnd,
  forceBargeInStart as facadeForceBargeInStart,
  initMic as facadeInitMic,
  isRecording as facadeIsRecording,
  registerVoiceLegacyFacade,
  setGreetGateActive as facadeSetGreetGateActive,
  setVadBoost as facadeSetVadBoost,
} from './voice/legacy/VoiceLegacyFacade.js';
import {
  DEFAULT_MAX_TURN_MS,
  EVIDENCE_MIN_BYTES,
  EVIDENCE_MIN_SNR_DB,
  EVIDENCE_MIN_SPEECH_MS,
  GREET_BARGE_MIN_SNR_DB,
  MANUAL_DEBOUNCE_MS,
  MANUAL_NO_AUDIO_CANCEL_MS,
  MANUAL_VAD_IGNORE_MS,
  MIN_VALID_BLOB_BYTES,
  PARTIAL_CONF_RISE_DELTA,
  PARTIAL_CONF_THRESHOLD,
  PRE_ROLL_MS,
  REC_MIME,
  SAFETY_CLOSE_DELAY_MS,
  WEBM_MIME,
  _beginTurnTrace,
  _cancelGreetGate,
  _clearGreetGateWaiters,
  _clearTurnTrace,
  _completeGreetGate,
  _handleGreetGateStateFrame,
  _handleGreetGateUtteranceEnd,
  _logLifecycle,
  _now,
  _resetGreetGateState,
  _updateAssistantPhaseFromDetail,
  _voiceLog,
  _waitForGreetGate,
  optsFromGlobal,
  state,
} from './voice/legacy/VoiceLegacyTopLevel.js';

export async function initMic(stream = null) { return await facadeInitMic(stream); }
export async function armVAD(stream = null, opts = {}) { return await facadeArmVAD(stream, opts); }
export function disarmVAD() { facadeDisarmVAD(); }
export function isRecording() { return !!facadeIsRecording(); }
export function bargeIn() { facadeBargeIn(); }
export function setVadBoost(value) { facadeSetVadBoost(value); }
export function setGreetGateActive(active = true) { facadeSetGreetGateActive(!!active); }
export function forceBargeInStart(meta = {}) { return facadeForceBargeInStart(meta); }
export function forceBargeInEnd(opts = {}) { return facadeForceBargeInEnd(opts); }

_refreshManualConfig();
registerTtsEventListener({ createContext: () => ({ state, now: _now, abortEvidenceGate: _abortEvidenceGate, ttsIsPlaying, clearPostTtsHoldTimer: _clearPostTtsHoldTimer, TurnState }), onTtsStart, onTtsEnd });

function _setGreetGateActive(active) {
  if (active) {
    _voiceLog('debug', 'greet gate activation requested', {
      alreadyActive: state.greetGateActive,
      turnHintSent: state.turnHintSent,
      turnHintAwaitingWS: state.turnHintAwaitingWS,
    });
    _clearGreetGateWaiters(false);
    _resetGreetGateState();
    state.greetGateActive = true;
    state.greetGatePhase = 'pending';
    state.greetGateLastReason = 'armed';
    state.greetGateLastSignal = 'armed';
    state.eligibility = 'blocked_pregreet';
    state.postTtsHoldUntil = 0;
    _clearPostTtsHoldTimer();
    _voiceLog('info', 'greet gate armed', {
      greetGateActive: state.greetGateActive,
      turnHintSent: state.turnHintSent,
      turnHintAwaitingWS: state.turnHintAwaitingWS,
    });
    _ensureWSListener();
    return;
  }
  if (state.greetGateActive) {
    _completeGreetGate('manual_release');
  } else {
    _resetGreetGateState();
  }
}

function _resetEvidenceGate(reason = null) {
  state.evidenceGate.reset(reason);
  state.evidenceGateCommitPromise = null;
}
function _abortEvidenceGate(reason, detail = null) {
  if (!state.evidenceGate.isOpen()) {
    return;
  }
  const stats = getShadowStats(state);
  state.evidenceGate.abort(reason || 'aborted', detail, stats);
  _voiceLog('info', 'evidence gate aborted', {
    reason: state.evidenceGate.reason(),
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
  });
  resetShadowBufferState(state);
  _resetEvidenceGate(state.evidenceGate.reason());
  emitVoiceEvent('state', { state: 'armed' });
}
function _evaluateEvidenceGate(trigger = 'poll') {
  if (!state.evidenceGate.isOpen()) {
    return false;
  }
  const stats = getShadowStats(state);
  const snrDb = Number.isFinite(state.evidenceGate.lastDetail?.snrDb)
    ? state.evidenceGate.lastDetail.snrDb
    : null;
  const requiredSnr = getEvidenceSnrRequirement(state, _now, EVIDENCE_MIN_SNR_DB);
  const minMsRaw = Number(optsFromGlobal('evidence_min_speech_ms', EVIDENCE_MIN_SPEECH_MS));
  const minBytesRaw = Number(optsFromGlobal('evidence_min_bytes', EVIDENCE_MIN_BYTES));
  const minMs = Number.isFinite(minMsRaw) ? Math.max(0, minMsRaw) : EVIDENCE_MIN_SPEECH_MS;
  const minBytes = Number.isFinite(minBytesRaw) ? Math.max(0, minBytesRaw) : EVIDENCE_MIN_BYTES;
  const { shouldCommit } = state.evidenceGate.update({
    vadState: 'speech',
    snr: snrDb,
    snrBoost: Math.max(0, requiredSnr - EVIDENCE_MIN_SNR_DB),
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
    minSpeechMs: minMs,
    minBytes,
  });
  if (shouldCommit && !state.evidenceGateCommitPromise) {
    const commitPromise = _commitEvidenceGate(trigger).catch((err) => {
      _voiceLog('warn', 'evidence gate commit failed', { error: err?.message || err });
      throw err;
    });
    state.evidenceGateCommitPromise = commitPromise;
    commitPromise.finally(() => {
      if (state.evidenceGateCommitPromise === commitPromise) {
        state.evidenceGateCommitPromise = null;
      }
    });
  }
  return shouldCommit;
}
async function _commitEvidenceGate(trigger = 'unknown') {
  const gate = state.evidenceGate;
  if (gate.satisfied) {
    return true;
  }
  const snapshot = {
    bufferedMs: gate.bufferedMs,
    bufferedBytes: gate.bufferedBytes,
    snrOk: gate.snrOk,
    minMassOk: gate.minMassOk,
    partialGateOk: gate.partialGateOk,
  };
  gate.satisfy(trigger);
  _voiceLog('info', 'evidence gate satisfied', { trigger, ...snapshot });

  let ok = await _startRecorder();
  if (!ok) {
    try {
      await _primeRecorderForPreRoll({ resetBuffer: false });
    } catch {}
    ok = await _startRecorder();
  }

  if (ok) {
    state.evidenceGateCommitPromise = null;
    gate.reset('committed');
    emitVoiceEvent('state', { state: 'recording' });
    return true;
  }

  _voiceLog('warn', 'recorder unavailable after evidence gate');
  emitVoiceEvent('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
  state.evidenceGateCommitPromise = null;
  gate.reset('recorder_unavailable');
  return false;
}
function _updateEvidenceGateWithChunk(durationMs, bytes) {
  if (!state.evidenceGate.isOpen()) {
    return;
  }
  const stats = getShadowStats(state);
  state.evidenceGate.extendBuffer({
    durationMs,
    bytes,
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
  });
  _evaluateEvidenceGate('chunk');
}
function _updateEvidenceGateWithPartial(confidence = null, transcript = '') {
  if (!state.evidenceGate.isOpen()) {
    return;
  }
  const stats = getShadowStats(state);
  const requiredSnr = getEvidenceSnrRequirement(state, _now, EVIDENCE_MIN_SNR_DB);
  const minMsRaw = Number(optsFromGlobal('evidence_min_speech_ms', EVIDENCE_MIN_SPEECH_MS));
  const minBytesRaw = Number(optsFromGlobal('evidence_min_bytes', EVIDENCE_MIN_BYTES));
  const minMs = Number.isFinite(minMsRaw) ? Math.max(0, minMsRaw) : EVIDENCE_MIN_SPEECH_MS;
  const minBytes = Number.isFinite(minBytesRaw) ? Math.max(0, minBytesRaw) : EVIDENCE_MIN_BYTES;
  state.evidenceGate.update({
    vadState: 'hold',
    snr: Number.isFinite(state.evidenceGate.lastDetail?.snrDb)
      ? state.evidenceGate.lastDetail.snrDb
      : null,
    snrBoost: Math.max(0, requiredSnr - EVIDENCE_MIN_SNR_DB),
    bufferedMs: stats.durationMs,
    bufferedBytes: stats.totalBytes,
    minSpeechMs: minMs,
    minBytes,
    asrCue: {
      type: 'partial',
      conf: typeof confidence === 'number' ? confidence : null,
      transcript,
      threshold: PARTIAL_CONF_THRESHOLD,
      delta: PARTIAL_CONF_RISE_DELTA,
    },
  });
  _evaluateEvidenceGate('partial');
}
function _maybeSendAudioStop(detail = {}) {
  if (state.audioStopSent) {
    return false;
  }
  try {
    sendJSON({ type: 'AudioStop' });
    state.audioStopSent = true;
    _voiceLog('info', 'AudioStop sent', detail && typeof detail === 'object' ? detail : { detail });
    return true;
  } catch (err) {
    _voiceLog('warn', 'failed to send AudioStop', { error: err?.message || err, ...(detail && typeof detail === 'object' ? detail : { detail }) });
    return false;
  }
}
function _clearPendingEndTimer() {
  if (state.pendingEndTimer) {
    try { clearTimeout(state.pendingEndTimer); } catch {}
    state.pendingEndTimer = null;
  }
}
function _clearPostTtsHoldTimer() {
  if (state.postTtsHoldTimer) {
    try { clearTimeout(state.postTtsHoldTimer); } catch {}
  }
  state.postTtsHoldTimer = null;
}
function _clearSafetyCloseTimer() {
  if (state.safetyCloseTimer) {
    try { clearTimeout(state.safetyCloseTimer); } catch {}
    state.safetyCloseTimer = null;
  }
}
function _armSafetyCloseTimer() {
  const shouldArm = state.turnOpen || state.recStreaming;
  if (!shouldArm) {
    return;
  }

  const rawDelay = Number(optsFromGlobal('chunk_safety_timeout_ms', SAFETY_CLOSE_DELAY_MS));
  const delayMs = Number.isFinite(rawDelay) ? Math.max(0, rawDelay) : SAFETY_CLOSE_DELAY_MS;

  _clearSafetyCloseTimer();

  state.safetyCloseTimer = setTimeout(() => {
    state.safetyCloseTimer = null;
    const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
      ? performance.now()
      : Date.now();
    const lastChunkAt = state.lastChunkAt || 0;
    const idleMs = lastChunkAt ? Math.max(0, now - lastChunkAt) : delayMs;
    _voiceLog('info', 'safety close', {
      configuredDelayMs: delayMs,
      idleMs,
      bytesSent: state.chunkBytesSent,
    });
    _maybeSendAudioStop({ reason: 'safety_timeout', idleMs, configuredDelayMs: delayMs });
    const pending = _closeTurnIfOpen();
    if (pending) {
      pending.catch(() => {});
    }
  }, delayMs);
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
function _refreshManualConfig() {
  const enabled = !!optsFromGlobal('feature_manual_barge_in', true);
  const manualOnly = !!optsFromGlobal('barge_in_mode_manual', true);
  const autoCommit = !!optsFromGlobal('auto_commit_when_ready', true);
  state.manual.enabled = enabled;
  state.manual.modeManualOnly = manualOnly;
  state.manual.autoCommitWhenReady = autoCommit;
  return enabled;
}
function _clearManualTimers() {
  if (state.manual.noAudioTimer) {
    try { clearTimeout(state.manual.noAudioTimer); } catch {}
    state.manual.noAudioTimer = null;
  }
}
function _manualAutoCancel(reason = 'no_audio') {
  const manual = state.manual;
  if (!manual.buttonDown && !manual.active) {
    return;
  }
  _logLifecycle('manual_barge_in_auto_cancel', { reason });
  try {
    forceBargeInEnd({ autoCancel: true, reason });
  } catch (err) {
    _voiceLog('warn', 'manual auto cancel failed', { error: err?.message || err, reason });
  }
}
function _forceBargeInStart(meta = {}) {
  const manual = state.manual;
  const enabled = _refreshManualConfig();
  if (!enabled) {
    _voiceLog('debug', 'manual barge-in start ignored — feature disabled');
    return false;
  }

  const now = Date.now();
  if (manual.buttonDown) {
    return true;
  }
  if (now < manual.debounceUntil) {
    const remaining = Math.max(0, manual.debounceUntil - now);
    _voiceLog('debug', 'manual barge-in start debounced', { remainingMs: remaining });
    return false;
  }

  manual.buttonDown = true;
  manual.active = false;
  manual.deferCloseStream = false;
  manual.sentStartFrame = false;
  manual.startAt = _now();
  manual.firstChunkAt = 0;
  manual.ignoreVadUntil = 0;
  manual.debounceUntil = now + MANUAL_DEBOUNCE_MS;
  _clearManualTimers();

  _bargeIn();

  state.currentCommitMode = 'manual';
  state.assistantReady = false;

  const source = (typeof meta === 'object' && meta && meta.source)
    ? String(meta.source)
    : 'ui';
  _logLifecycle('manual_barge_in_start', { source });
  try { console.info?.('manual_barge_in_start'); } catch {}

  try {
    const sent = sendJSON({ type: 'Control', action: 'barge_in_start' });
    manual.sentStartFrame = sent === true;
  } catch (err) {
    _voiceLog('warn', 'failed to send manual barge-in start frame', { error: err?.message || err });
    manual.sentStartFrame = false;
  }

  Promise.resolve(_startRecorder()).then((started) => {
    if (!started) {
      manual.buttonDown = false;
      manual.active = false;
      manual.deferCloseStream = false;
      if (manual.sentStartFrame) {
        try { sendJSON({ type: 'Control', action: 'barge_in_end' }); } catch {}
        manual.sentStartFrame = false;
      }
      _voiceLog('warn', 'recorder unavailable — reverting to typing (manual barge-in)');
      emitVoiceEvent('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
      return;
    }

    manual.active = true;
    manual.deferCloseStream = false;
    emitVoiceEvent('state', { state: 'recording' });
    _clearManualTimers();
    try {
      manual.noAudioTimer = setTimeout(() => _manualAutoCancel('no_audio'), MANUAL_NO_AUDIO_CANCEL_MS);
    } catch {}
  }).catch((err) => {
    manual.buttonDown = false;
    manual.active = false;
    manual.deferCloseStream = false;
    _voiceLog('warn', 'manual barge-in start failed', { error: err?.message || err });
    emitVoiceEvent('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
  });

  return true;
}
function _forceBargeInEnd(opts = {}) {
  const manual = state.manual;
  const enabled = _refreshManualConfig();
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
  manual.ignoreVadUntil = _now() + MANUAL_VAD_IGNORE_MS;
  manual.debounceUntil = Date.now() + MANUAL_DEBOUNCE_MS;
  manual.deferCloseStream = true;
  _clearManualTimers();

  _logLifecycle('manual_barge_in_end', { reason, auto_cancel: autoCancel });
  try { console.info?.('manual_barge_in_end'); } catch {}

  try {
    sendJSON({ type: 'Control', action: 'barge_in_end' });
  } catch (err) {
    _voiceLog('warn', 'failed to send manual barge-in end frame', { error: err?.message || err });
  }
  manual.sentStartFrame = false;

  if (state.rec && state.rec.state === 'recording') {
    _stopRecorder({ reason: autoCancel ? 'manual_auto_cancel' : 'manual_release' });
  } else {
    manual.active = false;
    manual.deferCloseStream = false;
  }

  return true;
}
function _ensureWSListener() {
  if (state.wsListener || typeof window === 'undefined') {
    return;
  }
  const handler = async (ev) => {
    const detail = ev?.detail || {};
    const type = detail?.type;
    const typeNorm = typeof type === 'string' ? type.toLowerCase() : '';

    _updateAssistantPhaseFromDetail(detail);

    if (typeNorm === 'utteranceend') {
      _handleGreetGateUtteranceEnd(detail);
    }
    _handleGreetGateStateFrame(detail);

    let isFinal = false;
    if (typeNorm === 'utteranceend') {
      isFinal = true;
    } else if (typeNorm === 'results' || typeNorm === 'result') {
      const channelFinal = detail?.channel?.is_final === true;
      const payloadFinal = detail?.is_final === true;
      isFinal = channelFinal || payloadFinal;
      if (!isFinal) {
        const firstAlt = Array.isArray(detail?.channel?.alternatives)
          ? detail.channel.alternatives[0]
          : null;
        const confidence = Number.isFinite(firstAlt?.confidence)
          ? firstAlt.confidence
          : (Number.isFinite(detail?.confidence) ? detail.confidence : null);
        const transcriptText = typeof firstAlt?.transcript === 'string'
          ? firstAlt.transcript
          : (typeof detail?.transcript === 'string' ? detail.transcript : '');
        _updateEvidenceGateWithPartial(confidence, transcriptText);
      }
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
      _voiceLog('warn', 'failed to stop recorder on server final', { error: err?.message || err });
    }

    try {
      await Promise.resolve(state.chunkSendPromise).catch(() => {});
    } catch (err) {
      _voiceLog('warn', 'chunk send did not settle after server final', { error: err?.message || err });
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
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
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

  // Front-end conditioning chain: high-pass -> light gate -> limiter -> analyser
  const highpass = ctx.createBiquadFilter();
  highpass.type = 'highpass';
  highpass.frequency.value = 80; // Trim HVAC / handling rumble
  highpass.Q.value = Math.SQRT1_2;

  const noiseGate = ctx.createDynamicsCompressor();
  noiseGate.threshold.value = -60;   // close gently on low-level room tone
  noiseGate.knee.value = 15;
  noiseGate.ratio.value = 12;
  noiseGate.attack.value = 0.02;
  noiseGate.release.value = 0.18;

  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -6;      // prevent spikes from re-triggering VAD
  limiter.knee.value = 0;
  limiter.ratio.value = 20;
  limiter.attack.value = 0.003;
  limiter.release.value = 0.08;

  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.06;          // LESS twitchy (was 0.03)

  source.connect(highpass);
  highpass.connect(noiseGate);
  noiseGate.connect(limiter);
  limiter.connect(analyser);

  await _setupPreRollTap(ctx, source);

  state.stream = stream;
  state.ctx = ctx;
  state.source = source;
  state.analyser = analyser;
  state.highpass = highpass;
  state.noiseGate = noiseGate;
  state.limiter = limiter;

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
  _clearSafetyCloseTimer();
  if (!state.turnOpen && !state.turnClosePromise) {
    return null;
  }
  if (state.turnClosePromise) {
    return state.turnClosePromise;
  }
  if (!state.turnOpen) {
    return null;
  }
  let waitMs = 0;
  if (state.manual?.deferCloseStream) {
    const rawDelay = Number(optsFromGlobal('manual_close_delay_ms', 320));
    waitMs = Number.isFinite(rawDelay) ? Math.max(0, rawDelay) : 320;
  }
  const closePromise = (async () => {
    try {
      if (waitMs > 0) {
        _voiceLog('debug', 'delaying CloseStream', { waitMs });
        await new Promise((resolve) => {
          try {
            setTimeout(resolve, waitMs);
          } catch {
            resolve();
          }
        });
      }
      const recorderActive = !!(state.rec && state.rec.state === 'recording');
      if (!recorderActive && state.chunkBytesSent === 0) {
        _logLifecycle('turn_close_skipped', { reason: 'no_audio', bytesSent: 0, waitMs }, 'warn');
        _voiceLog('warn', 'skipping CloseStream — no audio captured');
        return;
      }
      const closeFrame = { type: 'CloseStream' };
      const totalBytes = state.chunkBytesSent;
      _logLifecycle('turn_close_signal', { frame: closeFrame, bytesSent: totalBytes }, 'info');
      _voiceLog('info', 'turn-end signal sent', { bytesSent: totalBytes });
      await sendCloseStream();
    } finally {
      state.turnOpen = false;
      state.turnClosePromise = null;
      state.manual.deferCloseStream = false;
    }
  })();
  state.turnClosePromise = closePromise;
  return closePromise;
}
async function _setupPreRollTap(ctx, source) {
  _teardownPreRollTap();

  if (!ctx || !source) {
    resetShadowBufferState(state);
    return;
  }

  resetShadowBufferState(state);

  const worklet = ctx.audioWorklet;
  if (!worklet || typeof worklet.addModule !== 'function') {
    // AudioWorklet unavailable; gracefully degrade without pre-roll.
    return;
  }

  try {
    const moduleUrl = new URL('./voice/pre_roll_processor.js', import.meta.url);
    await worklet.addModule(moduleUrl);
  } catch (err) {
    _voiceLog('warn', 'failed to load pre-roll worklet', { error: err?.message || err });
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
    _voiceLog('warn', 'pre-roll worklet unavailable', { error: err?.message || err });
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
  resetShadowBufferState(state);
}
function _attemptAudioStartSend(mime) {
  try {
    const result = sendJSON({ type: 'AudioStart', mime });
    return result === true;
  } catch (err) {
    _voiceLog('warn', 'failed to send AudioStart hint', { error: err?.message || err });
    return false;
  }
}
async function _ensureAudioStartSent() {
  _voiceLog('debug', 'ensure AudioStart called', {
    greetGateActive: state.greetGateActive,
    greetGatePhase: state.greetGatePhase,
    turnHintSent: state.turnHintSent,
    turnHintAwaitingWS: state.turnHintAwaitingWS,
  });
  if (state.turnHintSent) {
    return true;
  }

  if (state.turnHintPromise) {
    try {
      return await state.turnHintPromise;
    } catch (err) {
      _voiceLog('warn', 'AudioStart pending promise rejected', { error: err?.message || err });
      return false;
    }
  }

  const recorderMime = (state.rec && state.rec.mimeType) || state.turnHintMime || REC_MIME;
  state.turnHintMime = recorderMime;

  const sendPromise = (async () => {
    const gateWait = _waitForGreetGate();
    if (gateWait) {
      _voiceLog('debug', 'AudioStart waiting for greet gate', {
        phase: state.greetGatePhase,
        active: state.greetGateActive,
      });
      const allowed = await gateWait;
      if (!allowed) {
        _voiceLog('warn', 'AudioStart aborted before greet gate release');
        return false;
      }
    }

    const sentImmediately = _attemptAudioStartSend(recorderMime);
    if (sentImmediately) {
      state.turnHintSent = true;
      _voiceLog('info', 'AudioStart sent', { mime: recorderMime, attempt: 'immediate' });
      return true;
    }

    state.turnHintAwaitingWS = true;
    _voiceLog('info', 'AudioStart deferred until WS ready', { mime: recorderMime });

    try {
      await waitWSOpen();
    } catch (err) {
      _voiceLog('warn', 'waitWSOpen failed while sending AudioStart', { error: err?.message || err });
      return false;
    }

    const sentAfterWait = _attemptAudioStartSend(recorderMime);
    if (sentAfterWait) {
      state.turnHintSent = true;
      _voiceLog('info', 'AudioStart sent', { mime: recorderMime, attempt: 'post-wait' });
      return true;
    }

    _voiceLog('warn', 'AudioStart send still failing after WS wait', { mime: recorderMime });
    return false;
  })();

  state.turnHintPromise = sendPromise
    .catch((err) => {
      _voiceLog('warn', 'AudioStart send promise failed', { error: err?.message || err });
      return false;
    })
    .finally(() => {
      state.turnHintPromise = null;
      state.turnHintAwaitingWS = false;
      if (!state.turnHintSent) {
        state.turnHintMime = null;
      }
    });

  return state.turnHintPromise;
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
      const handshakeOk = await _ensureAudioStartSent();
      if (!handshakeOk) {
        const detail = { mime: blob.type, preRoll, durationMs, timecode };
        _voiceLog('warn', 'skipping audio chunk; AudioStart not confirmed', detail);
        if (!state.chunkSendError) {
          state.chunkSendError = new Error('AudioStart not confirmed');
        }
        return;
      }
      const opening = !state.turnOpen;
      if (opening) {
        state.turnOpen = true;
      }
      try {
        await sendAudioChunk(blob);
        state.chunkBytesSent += blob.size;
        const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
          ? performance.now()
          : Date.now();
        state.lastChunkAt = now;
        state.audioStopSent = false;
        _armSafetyCloseTimer();
        const totalBytes = state.chunkBytesSent;
        const totalKb = Math.round((totalBytes / 1024) * 10) / 10;
        _voiceLog('info', logLabel, {
          bytes: blob.size,
          durationMs,
          timecode,
          mime: blob.type,
          totalBytes,
          totalKb,
        });
        if (opening) {
          const startedAt = state.recStartedAt || 0;
          if (startedAt) {
            const firstChunkMs = Math.max(0, Math.round(now - startedAt));
            const level = firstChunkMs > 400 ? 'warn' : 'debug';
            _logLifecycle('recorder_first_chunk', { ms: firstChunkMs }, level);
            if (firstChunkMs > 400) {
              _voiceLog('warn', 'first audio chunk delayed', { firstChunkMs });
            } else {
              _voiceLog('debug', 'first audio chunk latency', { firstChunkMs });
            }
          }
          _voiceLog('debug', 'turn marked open (first audio chunk queued)');
        }
      } catch (err) {
        state.chunkSendError = err;
        _voiceLog('warn', 'failed to stream audio chunk', { error: err?.message || err });
      }
    });
}
async function _primeRecorderForPreRoll(options = {}) {
  const { resetBuffer = true } = options || {};
  if (!state.stream) {
    return false;
  }
  if (typeof MediaRecorder === 'undefined') {
    _voiceLog('warn', 'MediaRecorder not supported in this browser');
    state.rec = null;
    return false;
  }
  if (state.rec && state.rec.state === 'recording') {
    if (resetBuffer) {
      resetShadowBufferState(state);
      _resetEvidenceGate();
    }
    return true;
  }

  let recorder;
  try {
    recorder = new MediaRecorder(state.stream, { mimeType: REC_MIME, audioBitsPerSecond: 32000 });
  } catch (primaryErr) {
    try {
      recorder = new MediaRecorder(state.stream); // fallback, browser picks best
    } catch (fallbackErr) {
      _voiceLog('warn', 'MediaRecorder init failed', { error: (fallbackErr || primaryErr)?.message || fallbackErr || primaryErr });
      state.rec = null;
      return false;
    }
  }

  state.rec = recorder;
  state.recStreaming = false;
  state.recStopping = false;
  state.recStopShouldSend = false;
  state.turnHintSent = false;
  state.turnHintMime = null;
  state.turnHintPromise = null;
  state.turnHintAwaitingWS = false;
  if (resetBuffer) {
    resetShadowBufferState(state);
    _resetEvidenceGate();
  }

  const timeslice = state.preRollTimeslice || 150;
  recorder.ondataavailable = _handleRecorderData;
  recorder.onstop = async () => {
    _clearSafetyCloseTimer();
    state.turnHintSent = false;
    state.turnHintMime = null;
    state.turnHintPromise = null;
    state.turnHintAwaitingWS = false;
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
        _voiceLog('warn', 'recorded chunks too small', { bytesSent: state.chunkBytesSent });
        finalDetail = { statusText: 'Listening… (heard silence — please try again)' };
      }
    } catch (e) {
      _voiceLog('warn', 'send audio failed', { error: e?.message || e });
      state.chunkSendError = state.chunkSendError || e;
    } finally {
      if (state.chunkSendError && !finalDetail) {
        finalDetail = { statusText: 'Listening… (audio send failed — please try again)' };
      }
      if (state.chunkSendError || state.chunkBytesSent < MIN_VALID_BLOB_BYTES) {
        _voiceLog('warn', 'recorder stopped with issues', {
          bytesSent: state.chunkBytesSent,
          error: state.chunkSendError?.message || state.chunkSendError || null,
        });
      } else {
        _voiceLog('info', 'recorder stopped', {
          bytesSent: state.chunkBytesSent,
          mime: (recorder && recorder.mimeType) || REC_MIME,
        });
      }
      const pendingClose = _closeTurnIfOpen();
      if (pendingClose) {
        try {
          await pendingClose;
        } catch {}
      }
      emitVoiceEvent('state', {
        state: 'armed',
        ...(finalDetail && typeof finalDetail === 'object' ? finalDetail : {}),
      });
      if (state.vad && state.stream && state.stream.active) {
        try { await _primeRecorderForPreRoll(); } catch (err) { _voiceLog('warn', 'failed to re-prime recorder', { error: err?.message || err }); }
      }
    }
    state.manual.active = false;
    if (!state.manual.buttonDown) {
      state.manual.deferCloseStream = false;
    }
    _clearManualTimers();
    state.currentCommitMode = 'idle';
  };

  const manualPriming = !!(state.manual && state.manual.buttonDown);
  const greetGateBlockingHandshake = state.greetGateActive
    && (state.greetGatePhase === 'pending' || state.greetGatePhase === 'calibrating');

  if (!manualPriming) {
    if (greetGateBlockingHandshake && !state.turnHintSent) {
      _voiceLog('debug', 'AudioStart handshake deferred until greet gate release', {
        mime: recorder.mimeType,
        greetGatePhase: state.greetGatePhase,
      });
    } else {
      const audioStartReady = await _ensureAudioStartSent();
      if (!audioStartReady) {
        _voiceLog('warn', 'AudioStart not confirmed — recorder start deferred', { mime: recorder.mimeType });
        state.rec = null;
        return false;
      }
    }
  } else if (!state.turnHintSent) {
    _voiceLog('debug', 'AudioStart handshake deferred during manual barge-in prime', {
      mime: recorder.mimeType,
    });
  }

  try {
    recorder.start(timeslice);
    state.preRollTimeslice = timeslice;
    _voiceLog('debug', 'recorder primed', { mime: recorder.mimeType, timeslice });
  } catch (err) {
    _voiceLog('warn', 'recorder start failed', { error: err?.message || err });
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

  if (state.manual.active) {
    if (!state.manual.firstChunkAt) {
      state.manual.firstChunkAt = _now();
    }
    if (state.manual.noAudioTimer) {
      _clearManualTimers();
    }
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

  state.preRollLastTimecode = bufferPreRollFrame({
    shadowBuffer: state.shadowBuffer,
    blob,
    timecode,
    timeslice: state.preRollTimeslice || 0,
    fallbackMs: PRE_ROLL_MS,
    lastTimecode: state.preRollLastTimecode,
    onBuffered: ({ durationMs, byteLength }) => _updateEvidenceGateWithChunk(durationMs, byteLength),
  }).nextTimecode;
}
function _stopRecorder(detail = null) {
  _clearSafetyCloseTimer();
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
      state.turnHintMime = null;
      state.turnHintPromise = null;
      state.turnHintAwaitingWS = false;
      return;
    }
  }

  if (!recorder) {
    state.rec = null;
    state.turnHintSent = false;
    state.turnHintMime = null;
    state.turnHintPromise = null;
    state.turnHintAwaitingWS = false;
    return;
  }

  if (recorder.state === 'inactive') {
    state.rec = null;
    state.turnHintSent = false;
    state.turnHintMime = null;
    state.turnHintPromise = null;
    state.turnHintAwaitingWS = false;
    return;
  }

  const shouldSendFinal = !!state.recStreaming;
  state.recStopShouldSend = shouldSendFinal;
  state.recStopping = true;
  state.recStreaming = false;
  if (!shouldSendFinal) {
    resetShadowBufferState(state);
    _resetEvidenceGate('recorder_stop');
  }

  try {
    _logLifecycle('recorder_stop_invoked', {
      reason: detail?.reason || null,
    }, 'info');
    _voiceLog('debug', 'recorder.stop() invoked');
    recorder.stop();
  } catch {}
  // intentionally keep state.rec reference nullable here; onstop handler handles final close
  state.rec = null;
  state.turnHintSent = false;
  state.turnHintMime = null;
  state.turnHintPromise = null;
  state.turnHintAwaitingWS = false;
}
function _teardownVADOnly() {
  try { state.vad && state.vad.stop(); } catch {}
  state.vad = null;
}
function _teardownAudioGraph() {
  _teardownPreRollTap();
  try { state.source && state.source.disconnect(); } catch {}
  try { state.highpass && state.highpass.disconnect(); } catch {}
  try { state.noiseGate && state.noiseGate.disconnect(); } catch {}
  try { state.limiter && state.limiter.disconnect(); } catch {}
  try { state.analyser && state.analyser.disconnect(); } catch {}
  try { state.ctx && state.ctx.close && state.ctx.close(); } catch {}
  state.source = null;
  state.highpass = null;
  state.noiseGate = null;
  state.limiter = null;
  state.analyser = null;
  state.ctx = null;
  state.deviceLogged = false;
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.vadMetrics = null;
  _removeWSListener();
}
function _disarm() {
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _clearSafetyCloseTimer();
  _clearBargeConfirm(false);
  _stopRecorder({ reason: 'manual_disarm' });
  _teardownVADOnly();
  state.turnOpen = false; // ensure local state is clean
  state.turnClosePromise = null;
  state.recStartedAt = 0;
  state.lastChunkAt = 0;
  state.ttsPlaying = false;
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.postTtsHoldUntil = 0;
  _clearPostTtsHoldTimer();
  state.eligibility = 'blocked_pregreet';
  state.refractoryUntil = Date.now();
  state.vadMetrics = null;
  _cancelGreetGate('disarm');
  _resetEvidenceGate('disarm');
  _removeWSListener();
  _clearTurnTrace();
  state.manual.buttonDown = false;
  state.manual.active = false;
  state.manual.deferCloseStream = false;
  state.manual.sentStartFrame = false;
  state.manual.ignoreVadUntil = 0;
  state.manual.debounceUntil = 0;
  state.manual.startAt = 0;
  state.manual.firstChunkAt = 0;
  _clearManualTimers();
  state.currentCommitMode = 'idle';
  state.assistantReady = false;
  state.assistantPhase = 'init';
  state.lastAssistantReadyAt = 0;
  emitVoiceEvent('state', { state: 'idle' });
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

async function _arm(stream = null, opts = {}) {
  const mic = stream || await _ensureMic();

  // Build / rebuild VAD
  _teardownVADOnly();

  // Merge runtime globals so admins can tune without rebuilds:
  let globalVad = {};
  try { globalVad = (window.__askchip_config && window.__askchip_config.vad) || {}; } catch {}
  const cfg = { ...globalVad, ...opts };

  const pollMs = cfg.pollMs ?? 33;
  const vadLoop = startVadLoop(state, () => ({
    analyser: state.analyser,
    cfg,
    pollMs,
    onSpeechStart: _onSpeechStartCommitted,
    onSpeechEnd: _onSpeechEndCommitted,
    ttsIsPlaying,
  }));

  if (!vadLoop || !vadLoop.vad) {
    return mic;
  }

  state.vad = vadLoop.vad;
  const effectivePollMs = Number.isFinite(vadLoop.pollMs) ? vadLoop.pollMs : pollMs;

  _logLifecycle('mic_start', {
    sampleRate: state.ctx?.sampleRate,
    pollMs: effectivePollMs,
  });
  emitVoiceEvent('state', { state: 'armed' });

  await _primeRecorderForPreRoll();

  return mic;
}

async function _startRecorder() {
  if (!state.stream) return false;

  const primed = await _primeRecorderForPreRoll({ resetBuffer: false });
  const recorder = state.rec;
  if (!primed || !recorder) {
    return false;
  }

  if (recorder.state !== 'recording') {
    const ready = await new Promise((resolve) => {
      const deadline = Date.now() + 500;
      let settleTimer = null;
      let onStart = null;
      let onError = null;
      const supportsAddEventListener = typeof recorder.addEventListener === 'function';
      const originalOnStart = supportsAddEventListener ? null : (typeof recorder.onstart === 'function' ? recorder.onstart : null);
      const originalOnError = supportsAddEventListener ? null : (typeof recorder.onerror === 'function' ? recorder.onerror : null);
      const cleanup = () => {
        if (settleTimer) {
          try { clearTimeout(settleTimer); } catch {}
          settleTimer = null;
        }
        if (onStart) {
          try { recorder.removeEventListener?.('start', onStart); } catch {}
        }
        if (onError) {
          try { recorder.removeEventListener?.('error', onError); } catch {}
        }
        if (!supportsAddEventListener) {
          try { recorder.onstart = originalOnStart; } catch {}
          try { recorder.onerror = originalOnError; } catch {}
        }
      };
      const checkState = () => {
        if (recorder.state === 'recording') {
          cleanup();
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          cleanup();
          resolve(recorder.state === 'recording');
          return;
        }
        settleTimer = setTimeout(checkState, 40);
      };
      onStart = () => {
        cleanup();
        resolve(true);
      };
      onError = () => {
        cleanup();
        resolve(false);
      };
      try { recorder.addEventListener?.('start', onStart, { once: true }); } catch {}
      try { recorder.addEventListener?.('error', onError, { once: true }); } catch {}
      if (!supportsAddEventListener) {
        recorder.onstart = (...args) => {
          cleanup();
          resolve(true);
          if (typeof originalOnStart === 'function') {
            try { originalOnStart.apply(recorder, args); } catch {}
          }
        };
        recorder.onerror = (...args) => {
          cleanup();
          resolve(false);
          if (typeof originalOnError === 'function') {
            try { originalOnError.apply(recorder, args); } catch {}
          }
        };
      }
      checkState();
    });

    if (!ready || recorder.state !== 'recording' || state.rec !== recorder) {
      return false;
    }
  }

  if (state.recStreaming) {
    return true;
  }

  state.chunkSendPromise = Promise.resolve();
  state.chunkBytesSent = 0;
  state.chunkSendError = null;
  state.turnClosePromise = null;
  state.lastChunkAt = 0;
  state.audioStopSent = false;
  _clearPendingEndTimer();
  state.recStartedAt = performance.now ? performance.now() : Date.now();
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.recStreaming = true;
  state.recStopping = false;
  state.recStopShouldSend = false;
  _ensureWSListener();

  const manualActive = !!(state.manual && state.manual.buttonDown);
  if (manualActive) {
    resetShadowBufferState(state);
  } else {
    flushShadowBuffer(
      state.shadowBuffer,
      (buffer, { durationMs, timecode }) => _sendRecorderChunk(buffer, { preRoll: true, durationMs, timecode }),
      (stats) => stats?.count && _voiceLog('debug', 'flushed pre-roll buffer', {
        chunks: stats.count,
        durationMs: stats.durationMs,
        bytes: stats.totalBytes,
      }),
    );
  }

  _voiceLog('info', 'recorder streaming', {
    mime: (state.rec && state.rec.mimeType) || REC_MIME,
  });

  const limitMs = Number(optsFromGlobal('max_turn_seconds', 90)) * 1000 || DEFAULT_MAX_TURN_MS;
  _safeClearTurnTimer();
  state.turnTimer = setTimeout(() => {
    try { _onSpeechEndCommitted({ reason: 'turn_timeout' }); } catch {}
  }, limitMs);

  return true;
}
async function _onSpeechStartCommitted(detail = {}) {
  const metrics = (detail && typeof detail === 'object') ? detail : {};
  _refreshManualConfig();
  state.vadMetrics = { ...metrics, phase: 'start' };
  const shadowStats = getShadowStats(state);
  const bufferedMsRaw = Number.isFinite(shadowStats.durationMs) ? shadowStats.durationMs : 0;
  const totalBytes = Number.isFinite(shadowStats.totalBytes) ? shadowStats.totalBytes : 0;
  const preRollCount = Number.isFinite(shadowStats.count) ? shadowStats.count : 0;
  const round = (v) => {
    if (!Number.isFinite(v)) return 0;
    return Math.round(v * 100) / 100;
  };
  const roundTenths = (v) => {
    if (!Number.isFinite(v)) return null;
    return Math.round(v * 10) / 10;
  };

  const now = _now();

  updateSessionNoise(state, metrics);

  const manualOnlyMode = !!state.manual.modeManualOnly;
  const allowAutoCommit = manualOnlyMode && !!state.manual.autoCommitWhenReady;
  if (manualOnlyMode && state.ttsPlaying) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'tts_active_manual_mode' });
    _voiceLog('debug', 'speech start ignored while TTS playing (manual mode)');
    return;
  }
  if (manualOnlyMode && !allowAutoCommit) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'manual_mode_vad_disabled' });
    _voiceLog('debug', 'speech start ignored — manual mode without auto-commit');
    return;
  }
  if (allowAutoCommit && !state.assistantReady) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'assistant_not_ready' });
    _voiceLog('debug', 'speech start ignored — assistant not ready for auto-commit');
    return;
  }

  if (state.manual.ignoreVadUntil && now < state.manual.ignoreVadUntil) {
    const remaining = Math.max(0, Math.round(state.manual.ignoreVadUntil - now));
    _voiceLog('debug', 'speech start suppressed during manual cooldown', { remainingMs: remaining });
    return;
  }

  const holdUntilTts = state.postTtsHoldUntil || 0;
  const waitMs = Math.max(0, holdUntilTts - now);
  if (waitMs > 0) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'post_tts_hold', holdUntil: holdUntilTts, waitMs });
    _clearPostTtsHoldTimer();
    try { _voiceLog('info', 'speech start deferred by post-TTS hold', { holdUntil: holdUntilTts, waitMs }); } catch {}
    state.postTtsHoldTimer = setTimeout(() => {
      state.postTtsHoldTimer = null;
      try { _onSpeechStartCommitted(detail); } catch {}
    }, waitMs);
    return;
  }

  _clearPostTtsHoldTimer();
  state.postTtsHoldUntil = 0;

  if (state.eligibility === 'holdoff') {
    state.eligibility = 'eligible';
  }

  const greetActive = state.greetGateActive && (state.greetGatePhase === 'pending' || state.greetGatePhase === 'calibrating');
  if (state.eligibility === 'blocked_pregreet' && !greetActive) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'pregreet_block' });
    return;
  }

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

  const traceActive = _getActiveTurnTraceId();
  if (!state.recStreaming || !traceActive) {
    _beginTurnTrace('speech_start');
  }

  const commitMode = allowAutoCommit ? 'auto_commit' : 'vad';
  state.currentCommitMode = commitMode;
  if (commitMode === 'auto_commit') {
    state.assistantReady = false;
  }

  _logLifecycle('vad_speech_start', {
    preRollBufferedMs: round(bufferedMsRaw),
    preRollSentMs: round(Math.min(bufferedMsRaw, PRE_ROLL_MS)),
    preRollChunks: preRollCount,
    preRollBytes: totalBytes,
    preRollEnabled: preRollCount > 0,
    preRollMime: (state.rec && state.rec.mimeType) || REC_MIME,
    snrDb: roundTenths(metrics?.snrDb),
    noiseFloorDb: roundTenths(metrics?.noiseFloorDb),
    thresholdStartDb: roundTenths(metrics?.thresholds?.startDb),
    commitMode,
  });
  _voiceLog('info', 'speech started', {
    preRollChunks: preRollCount,
    preRollBytes: totalBytes,
    snrDb: roundTenths(metrics?.snrDb),
    commitMode,
  });

  // FAST PATH: Ready-phase hands-free turn start
  if (commitMode === 'auto_commit') {
    // Start the MediaRecorder now (not just priming)
    let ok = await _startRecorder();         // internally sets state.recStreaming = true and flushes pre-roll
    if (!ok) {
      try { await _primeRecorderForPreRoll({ resetBuffer: false }); } catch {}
      ok = await _startRecorder();
    }
    if (ok) {
      emitVoiceEvent('state', { state: 'recording' });
      return;                                // we're streaming; rest of the function can return
    }
    _voiceLog('warn', 'recorder unavailable — reverting to typing');
    emitVoiceEvent('state', { state: 'armed', statusText: 'Listening… (mic unavailable — please type)' });
    return;
  }

  if (state.greetGateActive) {
    if (state.greetGatePhase === 'calibrating') {
      _voiceLog('info', 'speech start suppressed during greet calibration', {
        calibrateUntil: state.greetGateCalibrateUntil,
        snrDb: roundTenths(metrics?.snrDb),
      });
      return;
    }
    if (state.greetGatePhase === 'pending') {
      const snrDb = Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null;
      if (state.ttsPlaying) {
        _voiceLog('info', 'speech start suppressed by greet gate while TTS playing', {
          snrDb: roundTenths(snrDb),
          ttsPlaying: true,
        });
        return;
      }
      if (Number.isFinite(snrDb) && snrDb >= GREET_BARGE_MIN_SNR_DB) {
        _voiceLog('info', 'greet gate bypassed via barge-in', {
          snrDb: roundTenths(snrDb),
        });
        _completeGreetGate('barge_in');
      } else {
        _voiceLog('info', 'speech start suppressed by greet gate', {
          snrDb: roundTenths(metrics?.snrDb),
        });
        return;
      }
    }
  }

  const manualPressing = !!(state.manual && state.manual.buttonDown);
  if (state.ttsPlaying && !manualPressing) {
    _logLifecycle('vad_speech_start_suppressed', { reason: 'tts_playback_active' });
    _voiceLog('debug', 'speech start ignored while TTS playback active');
    return;
  }

  if (state.ttsMask && state.ttsMask.isMasked(now)) {
    const requiredSnr = getEvidenceSnrRequirement(state, _now, EVIDENCE_MIN_SNR_DB);
    const snrDb = Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null;
    if (!Number.isFinite(snrDb) || snrDb < requiredSnr) {
      _logLifecycle('vad_speech_start_suppressed', {
        reason: 'tts_decay_guard',
        snrDb: roundTenths(snrDb),
        requiredSnrDb: roundTenths(requiredSnr),
        decayUntil: state.ttsMask.decayUntil(),
      });
      return;
    }
  }

  _bargeIn();

  if (!state.evidenceGate.isOpen()) {
    _resetEvidenceGate();
    state.evidenceGate.start({
      startedAt: now,
      detail: metrics || null,
      bufferedMs: bufferedMsRaw,
      bufferedBytes: totalBytes,
    });
    const requiredSnr = getEvidenceSnrRequirement(state, _now, EVIDENCE_MIN_SNR_DB);
    const minSpeechOpt = Number(optsFromGlobal('evidence_min_speech_ms', EVIDENCE_MIN_SPEECH_MS));
    const minSpeechMs = Number.isFinite(minSpeechOpt) ? Math.max(0, minSpeechOpt) : EVIDENCE_MIN_SPEECH_MS;
    const minBytesOpt = Number(optsFromGlobal('evidence_min_bytes', EVIDENCE_MIN_BYTES));
    const minBytes = Number.isFinite(minBytesOpt) ? Math.max(0, minBytesOpt) : EVIDENCE_MIN_BYTES;
    state.evidenceGate.update({
      vadState: 'speech',
      snr: Number.isFinite(metrics?.snrDb) ? metrics.snrDb : null,
      snrBoost: Math.max(0, requiredSnr - EVIDENCE_MIN_SNR_DB),
      bufferedMs: bufferedMsRaw,
      bufferedBytes: totalBytes,
      minSpeechMs,
      minBytes,
    });
    emitVoiceEvent('state', { state: 'recording', gated: true });
  } else {
    state.evidenceGate.setDetail(metrics || state.evidenceGate.lastDetail);
  }

  _evaluateEvidenceGate('vad_start');
}
function _onSpeechEndCommitted(detail = null) {
  const metrics = (detail && typeof detail === 'object') ? detail : {};
  const reason = (detail && typeof detail === 'object' && detail.reason)
    ? detail.reason
    : (typeof detail === 'string' ? detail : 'vad_silence');
  const now = performance.now ? performance.now() : Date.now();
  const minTurnMs = Number(optsFromGlobal('min_turn_ms', 1200)); // NEW: min turn length (default 1.2s)

  if (state.manual.ignoreVadUntil && now < state.manual.ignoreVadUntil) {
    const remaining = Math.max(0, Math.round(state.manual.ignoreVadUntil - now));
    _voiceLog('debug', 'speech end suppressed during manual cooldown', { remainingMs: remaining });
    return;
  }

  state.vadMetrics = { ...metrics, phase: 'end' };

  const roundTenths = (v) => {
    if (!Number.isFinite(v)) return null;
    return Math.round(v * 10) / 10;
  };

  updateSessionNoise(state, metrics);

  _voiceLog('info', 'speech ended', {
    source: 'vad',
    reason,
    snrDb: roundTenths(metrics?.snrDb),
    durationMs: Number.isFinite(metrics?.speechDurationMs) ? Math.round(metrics.speechDurationMs) : null,
  });

  if (!state.recStreaming && state.evidenceGate.isOpen()) {
    _abortEvidenceGate(reason || 'evidence_not_met', metrics);
    return;
  }

  if (state.bargeConfirmActive) {
    _clearBargeConfirm(true);
  }

  // If we haven't recorded at least minTurnMs, delay honoring VAD-end.
  // Only applies while recorder is actually running.
  if (state.rec && typeof state.rec.state === 'string' && state.rec.state === 'recording') {
    const elapsed = Math.max(0, now - (state.recStartedAt || now));
    const wait = Math.max(0, minTurnMs - elapsed);
    if (wait > 0) {
      _voiceLog('debug', 'delaying VAD end', { waitMs: wait, elapsed });
      _clearPendingEndTimer();
      state.pendingEndTimer = setTimeout(() => _onSpeechEndCommitted(detail), wait);
      return; // do not stop yet
    }
  }

  _logLifecycle('vad_speech_end', {
    reason,
    snrDb: roundTenths(metrics?.snrDb),
    noiseFloorDb: roundTenths(metrics?.noiseFloorDb),
    speechDurationMs: Number.isFinite(metrics?.speechDurationMs) ? Math.round(metrics.speechDurationMs) : null,
    thresholdStopDb: roundTenths(metrics?.thresholds?.stopDb),
  }, 'info');
  _maybeSendAudioStop({ reason });
  _safeClearTurnTimer();
  _clearPendingEndTimer();
  _clearSafetyCloseTimer();
  _stopRecorder({ reason });
  // Do NOT send CloseStream here; we send it in rec.onstop AFTER the blob is delivered.
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

registerVoiceLegacyFacade({
  initMic: (stream = null) => _ensureMic(stream),
  armVAD: (stream = null, opts = {}) => _arm(stream, opts),
  disarmVAD: () => { _disarm(); },
  isRecording: () => !!(state.rec && state.rec.state === 'recording'),
  bargeIn: () => { _bargeIn(); },
  setVadBoost: (_value) => {},
  setGreetGateActive: (active = true) => { _setGreetGateActive(!!active); },
  forceBargeInStart: (meta = {}) => _forceBargeInStart(meta),
  forceBargeInEnd: (opts = {}) => _forceBargeInEnd(opts),
});

export const __TEST_ONLY__ = {
  state,
  startRecorder: _startRecorder,
  stopRecorder: _stopRecorder,
  ensureWSListener: _ensureWSListener,
  closeTurnIfOpen: _closeTurnIfOpen,
  sendRecorderChunk: _sendRecorderChunk,
  clearSafetyCloseTimer: _clearSafetyCloseTimer,
  onSpeechStartCommitted: _onSpeechStartCommitted,
  onSpeechEndCommitted: _onSpeechEndCommitted,
};
