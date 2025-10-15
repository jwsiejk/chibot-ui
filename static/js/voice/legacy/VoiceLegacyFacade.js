import { emitVoiceEvent } from '../ui/Events.js';
import { bufferPreRollFrame, flushShadowBuffer, resetShadowBufferState } from '../core/index.js';
import { sendAudioChunk, sendCloseStream, sendJSON, waitWSOpen } from '../../ws_module.js';
import {
  DEFAULT_MAX_TURN_MS,
  EVIDENCE_MIN_BYTES,
  EVIDENCE_MIN_SNR_DB,
  EVIDENCE_MIN_SPEECH_MS,
  MIN_VALID_BLOB_BYTES,
  PARTIAL_CONF_RISE_DELTA,
  PARTIAL_CONF_THRESHOLD,
  REC_MIME,
  SAFETY_CLOSE_DELAY_MS,
  PRE_ROLL_MS,
  optsFromGlobal,
  _clearGreetGateWaiters,
  _completeGreetGate,
  _handleGreetGateStateFrame,
  _handleGreetGateUtteranceEnd,
  _logLifecycle,
  _resetGreetGateState,
  _updateAssistantPhaseFromDetail,
  _voiceLog,
  _now,
  _waitForGreetGate,
  state,
} from './VoiceLegacyTopLevel.js';
import { getEvidenceSnrRequirement, getShadowStats } from '../loops/VadLoop.js';

const IMPLEMENTATION = Object.create(null);

const KNOWN_METHODS = [
  'initMic',
  'armVAD',
  'disarmVAD',
  'isRecording',
  'bargeIn',
  'setVadBoost',
  'setGreetGateActive',
  'forceBargeInStart',
  'forceBargeInEnd',
  'initVoice',
  'startVoice',
  'stopVoice',
  'onWsOpen',
  'onWsMessage',
  'onWsClose',
  'onMicAvailable',
  'onMicStop',
  'onRecorderData',
  'onRecorderError',
];

function resolveImplementation(name) {
  const fn = IMPLEMENTATION[name];
  if (typeof fn !== 'function') {
    throw new Error(`VoiceLegacyFacade.${name} not wired`);
  }
  return fn;
}

function delegate(name) {
  return function legacyDelegate(...args) {
    return resolveImplementation(name)(...args);
  };
}

export const initMic = delegate('initMic');
export const armVAD = delegate('armVAD');
export const disarmVAD = delegate('disarmVAD');
export const isRecording = delegate('isRecording');
export const bargeIn = delegate('bargeIn');
export const setVadBoost = delegate('setVadBoost');
export const setGreetGateActive = delegate('setGreetGateActive');
export const forceBargeInStart = delegate('forceBargeInStart');
export const forceBargeInEnd = delegate('forceBargeInEnd');
export const initVoice = delegate('initVoice');
export const startVoice = delegate('startVoice');
export const stopVoice = delegate('stopVoice');
export const onWsOpen = delegate('onWsOpen');
export const onWsMessage = delegate('onWsMessage');
export const onWsClose = delegate('onWsClose');
export const onMicAvailable = delegate('onMicAvailable');
export const onMicStop = delegate('onMicStop');
export const onRecorderData = delegate('onRecorderData');
export const onRecorderError = delegate('onRecorderError');

export { startVadLoop, stopVadLoop, createVadSchedulerLegacy } from './VadSchedulerLegacy.js';

export function registerVoiceLegacyFacade(overrides = {}) {
  if (!overrides || typeof overrides !== 'object') {
    return { ...IMPLEMENTATION };
  }
  for (const name of KNOWN_METHODS) {
    if (Object.prototype.hasOwnProperty.call(overrides, name)) {
      const candidate = overrides[name];
      if (typeof candidate === 'function') {
        IMPLEMENTATION[name] = candidate;
      }
    }
  }
  return { ...IMPLEMENTATION };
}

const POST_TTS_HOLDOFF_MS = 600;
const TTS_DECAY_MS = 750;
const ENDED_STATES = new Set(['ended', 'stopped', 'idle', 'paused', '']);

function normalizeStateValue(value) {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function extractDetailAndState(ctx = {}) {
  const detail = ctx.detail ?? ctx.event?.detail ?? {};
  const stateValue = normalizeStateValue(detail?.state);
  ctx.detail = detail;
  ctx.stateValue = stateValue;
  return { detail, stateValue };
}

function resolveNowFn(now) {
  if (typeof now === 'function') {
    return now;
  }
  return () => Date.now();
}

export function onTtsStart(ctx = {}) {
  const { state: ctxState, abortEvidenceGate, ttsIsPlaying, TurnState } = ctx;
  if (!ctxState) {
    return false;
  }

  const { detail, stateValue } = extractDetailAndState(ctx);
  if (stateValue !== 'playing') {
    return false;
  }

  const nowFn = resolveNowFn(ctx.now);
  ctxState.ttsPlaying = true;
  ctxState.assistantReady = false;
  ctxState.assistantPhase = typeof TurnState?.Speaking === 'string'
    ? TurnState.Speaking.toLowerCase()
    : 'speaking';
  ctxState.postTtsHoldUntil = nowFn() + POST_TTS_HOLDOFF_MS;
  if (ctxState.ttsMask && typeof ctxState.ttsMask.start === 'function') {
    ctxState.ttsMask.start();
  }
  if (ctxState.evidenceGate?.isOpen?.() && typeof abortEvidenceGate === 'function') {
    abortEvidenceGate('tts_playback_start');
  }

  const isPrime = detail?.prime === true;
  const playbackConfirmed = detail?.confirmed === true || detail?.playbackConfirmed === true;
  let playbackActive = !!playbackConfirmed;
  if (!playbackActive && typeof ttsIsPlaying === 'function') {
    try {
      playbackActive = !!ttsIsPlaying();
    } catch {
      playbackActive = false;
    }
  }

  if (!isPrime && playbackActive && ctxState.eligibility === 'blocked_pregreet') {
    ctxState.eligibility = 'holdoff';
  }

  emitVoiceEvent('tts', {
    state: 'playing',
    prime: isPrime,
    playbackConfirmed: playbackConfirmed === true,
  });

  return true;
}

export function onTtsEnd(ctx = {}) {
  const { state: ctxState, clearPostTtsHoldTimer, TurnState } = ctx;
  if (!ctxState) {
    return false;
  }

  const { stateValue } = extractDetailAndState(ctx);
  ctxState.ttsPlaying = stateValue === 'playing';
  if (!ENDED_STATES.has(stateValue)) {
    return false;
  }

  const nowFn = resolveNowFn(ctx.now);
  ctxState.ttsPlaying = false;
  ctxState.postTtsHoldUntil = 0;
  if (typeof clearPostTtsHoldTimer === 'function') {
    clearPostTtsHoldTimer();
  }

  const sigma = Number.isFinite(ctxState.sessionSnrStd) ? ctxState.sessionSnrStd : 0;
  if (ctxState.ttsMask && typeof ctxState.ttsMask.end === 'function') {
    ctxState.ttsMask.end({
      decayMs: TTS_DECAY_MS,
      snrBoost: Math.max(3, sigma * 1.5),
    });
  }

  ctxState.assistantReady = true;
  ctxState.assistantPhase = typeof TurnState?.Ready === 'string'
    ? TurnState.Ready.toLowerCase()
    : 'ready';
  ctxState.lastAssistantReadyAt = nowFn();
  if (ctxState.eligibility === 'holdoff') {
    ctxState.eligibility = 'eligible';
  }

  emitVoiceEvent('tts', { state: stateValue || 'ended' });

  return true;
}

export function legacyClearPostTtsHoldTimer() {
  if (state.postTtsHoldTimer) {
    try { clearTimeout(state.postTtsHoldTimer); } catch {}
  }
  state.postTtsHoldTimer = null;
}

export function legacySetGreetGateActive(active, helpers = {}) {
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
    legacyClearPostTtsHoldTimer();
    _voiceLog('info', 'greet gate armed', {
      greetGateActive: state.greetGateActive,
      turnHintSent: state.turnHintSent,
      turnHintAwaitingWS: state.turnHintAwaitingWS,
    });
    const ensureWsListener = helpers && typeof helpers.ensureWsListener === 'function'
      ? helpers.ensureWsListener
      : null;
    if (ensureWsListener) {
      ensureWsListener();
    }
    return;
  }
  if (state.greetGateActive) {
    _completeGreetGate('manual_release');
  } else {
    _resetGreetGateState();
  }
}

export function legacyAbortEvidenceGate(reason, detail = null) {
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
  legacyResetEvidenceGate(state.evidenceGate.reason());
  emitVoiceEvent('state', { state: 'armed' });
}

export function legacyEvaluateEvidenceGate(trigger = 'poll', helpers = {}) {
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
    const commitPromise = legacyCommitEvidenceGate(trigger, helpers).catch((err) => {
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

export async function legacyCommitEvidenceGate(trigger = 'unknown', helpers = {}) {
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

  const startRecorder = typeof helpers.startRecorder === 'function'
    ? helpers.startRecorder
    : (() => legacyStartRecorder({
      primeRecorderForPreRoll: helpers.primeRecorderForPreRoll,
      clearPendingEndTimer: helpers.clearPendingEndTimer,
      ensureWSListener: helpers.ensureWsListener,
      onSpeechEndCommitted: helpers.onSpeechEndCommitted,
      clearTurnTimer: helpers.clearTurnTimer,
    }));
  const primeRecorder = typeof helpers.primeRecorderForPreRoll === 'function'
    ? helpers.primeRecorderForPreRoll
    : ((opts = {}) => legacyPrimeRecorderForPreRoll(opts));

  let ok = await startRecorder();
  if (!ok) {
    try {
      await primeRecorder({ resetBuffer: false });
    } catch {}
    ok = await startRecorder();
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

export function legacyUpdateEvidenceGateWithChunk(durationMs, bytes, helpers = {}) {
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
  legacyEvaluateEvidenceGate('chunk', helpers);
}

export function legacyUpdateEvidenceGateWithPartial(confidence = null, transcript = '', helpers = {}) {
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
  legacyEvaluateEvidenceGate('partial', helpers);
}

export function legacyClearManualTimers() {
  if (state.manual.noAudioTimer) {
    try { clearTimeout(state.manual.noAudioTimer); } catch {}
    state.manual.noAudioTimer = null;
  }
}

export function legacyResetEvidenceGate(reason = null) {
  state.evidenceGate.reset(reason);
  state.evidenceGateCommitPromise = null;
}

export function legacyClearSafetyCloseTimer() {
  if (state.safetyCloseTimer) {
    try { clearTimeout(state.safetyCloseTimer); } catch {}
    state.safetyCloseTimer = null;
  }
}

export function legacyArmSafetyCloseTimer() {
  const shouldArm = state.turnOpen || state.recStreaming;
  if (!shouldArm) {
    return;
  }

  const rawDelay = Number(optsFromGlobal('chunk_safety_timeout_ms', SAFETY_CLOSE_DELAY_MS));
  const delayMs = Number.isFinite(rawDelay) ? Math.max(0, rawDelay) : SAFETY_CLOSE_DELAY_MS;

  legacyClearSafetyCloseTimer();

  try {
    state.safetyCloseTimer = setTimeout(() => {
      state.safetyCloseTimer = null;
      _voiceLog('warn', 'chunk send timed out — forcing turn close', { delayMs });
      _logLifecycle('turn_close_timeout', { delayMs, turnOpen: state.turnOpen, recStreaming: state.recStreaming }, 'warn');
      const pending = legacyCloseTurnIfOpen();
      if (pending) {
        pending.catch(() => {});
      }
    }, delayMs);
  } catch (err) {
    state.safetyCloseTimer = null;
    _voiceLog('warn', 'failed to arm safety close timer', { error: err?.message || err });
  }
}

export function legacyCloseTurnIfOpen() {
  legacyClearSafetyCloseTimer();
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

function legacyAttemptAudioStartSend(mime) {
  try {
    const result = sendJSON({ type: 'AudioStart', mime });
    return result === true;
  } catch (err) {
    _voiceLog('warn', 'failed to send AudioStart hint', { error: err?.message || err });
    return false;
  }
}

export async function legacyEnsureAudioStartSent() {
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

    const sentImmediately = legacyAttemptAudioStartSend(recorderMime);
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

    const sentAfterWait = legacyAttemptAudioStartSend(recorderMime);
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

export async function legacySetupPreRollTap(ctx, source) {
  legacyTeardownPreRollTap();

  if (!ctx || !source) {
    resetShadowBufferState(state);
    return;
  }

  resetShadowBufferState(state);

  const worklet = ctx.audioWorklet;
  if (!worklet || typeof worklet.addModule !== 'function') {
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
    legacyTeardownPreRollTap();
  }
}

export function legacyTeardownPreRollTap() {
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

export async function legacyEnsureMic(externalStream = null, helpers = {}) {
  if (state.stream && state.stream.active) return state.stream;

  if (state.stream && !state.stream.active) {
    try { helpers.teardownAudioGraph?.(); } catch {}
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

  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC({ sampleRate: 48000 });
  if (ctx.state === 'suspended') { try { await ctx.resume(); } catch {} }

  const source = ctx.createMediaStreamSource(stream);

  const highpass = ctx.createBiquadFilter();
  highpass.type = 'highpass';
  highpass.frequency.value = 80;
  highpass.Q.value = Math.SQRT1_2;

  const noiseGate = ctx.createDynamicsCompressor();
  noiseGate.threshold.value = -60;
  noiseGate.knee.value = 15;
  noiseGate.ratio.value = 12;
  noiseGate.attack.value = 0.02;
  noiseGate.release.value = 0.18;

  const limiter = ctx.createDynamicsCompressor();
  limiter.threshold.value = -6;
  limiter.knee.value = 0;
  limiter.ratio.value = 20;
  limiter.attack.value = 0.003;
  limiter.release.value = 0.08;

  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.06;

  source.connect(highpass);
  highpass.connect(noiseGate);
  noiseGate.connect(limiter);
  limiter.connect(analyser);

  await legacySetupPreRollTap(ctx, source);

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

export function legacyOnMicAvailable({ recorder, resetBuffer = true } = {}) {
  state.rec = recorder || null;
  state.recStreaming = false;
  state.recStopping = false;
  state.recStopShouldSend = false;
  state.turnHintSent = false;
  state.turnHintMime = null;
  state.turnHintPromise = null;
  state.turnHintAwaitingWS = false;

  if (resetBuffer) {
    resetShadowBufferState(state);
    legacyResetEvidenceGate();
  }
}

export async function legacyOnMicStop({ recorder, helpers = {} } = {}) {
  legacyClearSafetyCloseTimer();
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
    await state.chunkSendPromise?.catch((err) => {
      state.chunkSendError = state.chunkSendError || err;
    });
    if (state.chunkBytesSent < MIN_VALID_BLOB_BYTES && !state.chunkSendError) {
      _voiceLog('warn', 'recorded chunks too small', { bytesSent: state.chunkBytesSent });
      finalDetail = { statusText: 'Listening… (heard silence — please try again)' };
    }
  } catch (err) {
    _voiceLog('warn', 'send audio failed', { error: err?.message || err });
    state.chunkSendError = state.chunkSendError || err;
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
    const pendingClose = legacyCloseTurnIfOpen();
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
      const prime = helpers.primeRecorderForPreRoll
        || ((opts = {}) => legacyPrimeRecorderForPreRoll(opts, helpers));
      try { await prime(); } catch (err) {
        _voiceLog('warn', 'failed to re-prime recorder', { error: err?.message || err });
      }
    }
  }
  state.manual.active = false;
  if (!state.manual.buttonDown) {
    state.manual.deferCloseStream = false;
  }
  legacyClearManualTimers();
  state.currentCommitMode = 'idle';
}

export function legacyOnRecorderData(event, helpers = {}) {
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
      legacyClearManualTimers();
    }
  }

  const timecode = Number.isFinite(event.timecode) ? event.timecode : null;

  if (state.recStopping && !state.recStopShouldSend) {
    return;
  }

  if (state.recStopping && state.recStopShouldSend) {
    state.recStopShouldSend = false;
    state.recStopping = false;
    legacySendRecorderChunk(blob, { preRoll: false, durationMs: null, timecode });
    return;
  }

  if (state.recStreaming) {
    legacySendRecorderChunk(blob, { preRoll: false, durationMs: null, timecode });
    return;
  }

  const updateEvidenceGateWithChunk = helpers.updateEvidenceGateWithChunk;
  state.preRollLastTimecode = bufferPreRollFrame({
    shadowBuffer: state.shadowBuffer,
    blob,
    timecode,
    timeslice: state.preRollTimeslice || 0,
    fallbackMs: PRE_ROLL_MS,
    lastTimecode: state.preRollLastTimecode,
    onBuffered: ({ durationMs, byteLength }) => {
      if (typeof updateEvidenceGateWithChunk === 'function') {
        updateEvidenceGateWithChunk(durationMs, byteLength);
      }
    },
  }).nextTimecode;
}

export function legacyOnRecorderError(event = null) {
  const detail = event && typeof event === 'object' ? (event.error || event) : event;
  if (!detail) {
    return;
  }
  _voiceLog('warn', 'recorder error event', { error: detail?.message || detail });
}

export async function legacyPrimeRecorderForPreRoll(options = {}, helpers = {}) {
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
      legacyResetEvidenceGate();
    }
    return true;
  }

  let recorder;
  try {
    recorder = new MediaRecorder(state.stream, { mimeType: REC_MIME, audioBitsPerSecond: 32000 });
  } catch (primaryErr) {
    try {
      recorder = new MediaRecorder(state.stream);
    } catch (fallbackErr) {
      _voiceLog('warn', 'MediaRecorder init failed', { error: (fallbackErr || primaryErr)?.message || fallbackErr || primaryErr });
      state.rec = null;
      return false;
    }
  }

  legacyOnMicAvailable({ recorder, resetBuffer });

  const nextHelpers = { ...helpers };
  if (!nextHelpers.primeRecorderForPreRoll) {
    nextHelpers.primeRecorderForPreRoll = (opts = {}) => legacyPrimeRecorderForPreRoll(opts, helpers);
  }

  const timeslice = state.preRollTimeslice || 150;
  recorder.ondataavailable = (event) => legacyOnRecorderData(event, nextHelpers);
  recorder.onstop = async () => {
    await legacyOnMicStop({ recorder, helpers: nextHelpers });
  };
  recorder.onerror = (event) => {
    legacyOnRecorderError(event, nextHelpers);
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
      const audioStartReady = await legacyEnsureAudioStartSent();
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

export async function legacyStartRecorder(helpers = {}) {
  if (!state.stream) return false;

  const prime = helpers.primeRecorderForPreRoll
    || ((opts = {}) => legacyPrimeRecorderForPreRoll(opts, helpers));
  const primed = await prime({ resetBuffer: false });
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
  helpers.clearPendingEndTimer?.();
  state.recStartedAt = (typeof performance !== 'undefined' && typeof performance.now === 'function')
    ? performance.now()
    : Date.now();
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.recStreaming = true;
  state.recStopping = false;
  state.recStopShouldSend = false;
  helpers.ensureWSListener?.();

  const manualActive = !!(state.manual && state.manual.buttonDown);
  if (manualActive) {
    resetShadowBufferState(state);
  } else {
    flushShadowBuffer(
      state.shadowBuffer,
      (buffer, { durationMs, timecode }) => legacySendRecorderChunk(buffer, { preRoll: true, durationMs, timecode }),
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
  helpers.clearTurnTimer?.();
  try {
    state.turnTimer = setTimeout(() => {
      try { helpers.onSpeechEndCommitted?.({ reason: 'turn_timeout' }); } catch {}
    }, limitMs);
  } catch {}

  return true;
}

export function legacySendRecorderChunk(blob, meta = {}) {
  if (!blob || blob.size < MIN_VALID_BLOB_BYTES) {
    return;
  }

  const { preRoll = false, durationMs = null, timecode = null } = meta || {};
  const logLabel = preRoll ? 'streamed pre-roll chunk' : 'streamed audio chunk';
  state.chunkSendPromise = state.chunkSendPromise
    .catch(() => {})
    .then(async () => {
      const handshakeOk = await legacyEnsureAudioStartSent();
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
        legacyArmSafetyCloseTimer();
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

export function legacyStopRecorder(detail = null) {
  legacyClearSafetyCloseTimer();
  const recorder = state.rec;
  const wasActive = !!recorder && recorder.state !== 'inactive';
  const payload = Object.assign({
    active: wasActive,
    hasRecorder: !!recorder,
  }, detail || {});
  _logLifecycle('mic_stop', payload, wasActive ? 'debug' : 'info');

  if (detail?.reason === 'server_final') {
    legacyApplyPostFinalHold('stop_recorder');
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
    legacyResetEvidenceGate('recorder_stop');
  }

  try {
    _logLifecycle('recorder_stop_invoked', {
      reason: detail?.reason || null,
    }, 'info');
    _voiceLog('debug', 'recorder.stop() invoked');
    recorder.stop();
  } catch {}
  state.rec = null;
  state.turnHintSent = false;
  state.turnHintMime = null;
  state.turnHintPromise = null;
  state.turnHintAwaitingWS = false;
}

export function legacyApplyPostFinalHold(source = 'unknown') {
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

export async function legacyOnWsMessageImpl(detail = {}, helpers = {}) {
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
    if (!isFinal && typeof helpers.updateEvidenceGateWithPartial === 'function') {
      const firstAlt = Array.isArray(detail?.channel?.alternatives)
        ? detail.channel.alternatives[0]
        : null;
      const confidence = Number.isFinite(firstAlt?.confidence)
        ? firstAlt.confidence
        : (Number.isFinite(detail?.confidence) ? detail.confidence : null);
      const transcriptText = typeof firstAlt?.transcript === 'string'
        ? firstAlt.transcript
        : (typeof detail?.transcript === 'string' ? detail.transcript : '');
      helpers.updateEvidenceGateWithPartial(confidence, transcriptText);
    }
  }

  if (!isFinal || state.finalized) {
    return;
  }

  legacyApplyPostFinalHold('ws_final');

  const recorder = state.rec;
  const isRecording = !!(recorder && typeof recorder.state === 'string' && recorder.state !== 'inactive');
  if (!isRecording) {
    return;
  }

  try {
    legacyStopRecorder({ reason: 'server_final' });
  } catch (err) {
    _voiceLog('warn', 'failed to stop recorder on server final', { error: err?.message || err });
  }

  try {
    await Promise.resolve(state.chunkSendPromise).catch(() => {});
  } catch (err) {
    _voiceLog('warn', 'chunk send did not settle after server final', { error: err?.message || err });
  }
}

export function legacyOnWsOpenImpl(detail = null) {
  const payload = (detail && typeof detail === 'object') ? detail : {};
  _voiceLog('debug', 'ws open observed', payload);
  state.assistantReady = false;
  state.turnClosePromise = null;
}

export function legacyOnWsCloseImpl(detail = null) {
  const payload = (detail && typeof detail === 'object') ? detail : {};
  _voiceLog('warn', 'ws close observed', payload);

  legacyClearSafetyCloseTimer();

  const recorder = state.rec;
  const isRecording = !!(recorder && typeof recorder.state === 'string' && recorder.state !== 'inactive');
  if (isRecording) {
    try {
      legacyStopRecorder({ reason: 'ws_close' });
    } catch (err) {
      _voiceLog('warn', 'failed to stop recorder on ws close', { error: err?.message || err });
    }
  }

  resetShadowBufferState(state);
  legacyResetEvidenceGate('ws_close');

  state.turnOpen = false;
  state.turnClosePromise = null;
  state.turnHintSent = false;
  state.turnHintMime = null;
  state.turnHintPromise = null;
  state.turnHintAwaitingWS = false;
  state.chunkSendPromise = Promise.resolve();
  state.chunkSendError = null;
  state.chunkBytesSent = 0;
  state.audioStopSent = false;
  state.finalized = false;

  emitVoiceEvent('state', { state: 'armed', statusText: 'Listening…' });
}

export * as VadFrameUtils from './VadFrameUtilsLegacy.js';
