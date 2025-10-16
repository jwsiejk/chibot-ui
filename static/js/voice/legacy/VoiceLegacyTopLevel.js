import { emitVoiceEvent } from '../ui/Events.js';
import {
  EvidenceGate,
  HysteresisVAD,
  NoiseModel,
  ShadowBuffer,
  TtsMask,
  TurnState,
} from '../core/index.js';

const WEBM_MIME = 'audio/webm; codecs=opus';
const REC_MIME = (typeof MediaRecorder !== 'undefined'
  && typeof MediaRecorder.isTypeSupported === 'function'
  && MediaRecorder.isTypeSupported(WEBM_MIME))
  ? WEBM_MIME
  : 'audio/webm; codecs=opus';
const DEFAULT_MAX_TURN_MS = 90_000;
const MIN_VALID_BLOB_BYTES = 1;
const PRE_ROLL_MS = 550;
const SAFETY_CLOSE_DELAY_MS = 2200;
const MANUAL_DEBOUNCE_MS = 300;
const MANUAL_NO_AUDIO_CANCEL_MS = 500;
const MANUAL_VAD_IGNORE_MS = 600;
const EVIDENCE_MIN_SPEECH_MS = 480;
const EVIDENCE_MIN_BYTES = 8 * 1024;
const EVIDENCE_MIN_SNR_DB = 3.5;
const PARTIAL_CONF_THRESHOLD = 0.55;
const PARTIAL_CONF_RISE_DELTA = 0.05;
const GREET_BARGE_MIN_SNR_DB = 8;
const GREET_CALIBRATE_DEFAULT_MS = 500;
const GREET_CALIBRATE_MIN_MS = 400;
const GREET_CALIBRATE_MAX_MS = 600;

const state = {
  stream: null, ctx: null, source: null, analyser: null,
  highpass: null, noiseGate: null, limiter: null, vad: null, rec: null,
  finalized: false, postFinalHoldUntil: 0, wsListener: null,
  chunkSendPromise: Promise.resolve(), chunkBytesSent: 0, chunkSendError: null,
  turnTimer: null, turnOpen: false, // track whether a turn is currently open server-side
  turnClosePromise: null, turnHintSent: false, turnHintMime: null, turnHintPromise: null, turnHintAwaitingWS: false,
  deviceLogged: false, recStartedAt: 0, pendingEndTimer: null,
  ttsPlaying: false, bargeConfirmTimer: null, bargeConfirmActive: false,
  preRollNode: null, preRollGain: null,
  shadowBuffer: new ShadowBuffer({ maxMs: PRE_ROLL_MS }),
  preRollLastTimecode: null, preRollTimeslice: 150,
  recStreaming: false, recStopping: false, recStopShouldSend: false,
  lastChunkAt: 0, safetyCloseTimer: null,
  turnTraceBase: null, turnTraceSeq: 0, turnTraceId: null,
  audioStopSent: false, vadMetrics: null,
  greetGateActive: false, greetGatePhase: 'idle', greetGateWaiters: [],
  greetGateCalibrateTimer: null, greetGateCalibrateUntil: 0, greetGateCalibrateLastMs: null,
  greetGateCalibrateMs: GREET_CALIBRATE_DEFAULT_MS,
  greetGateCalibrateMinMs: GREET_CALIBRATE_MIN_MS,
  greetGateCalibrateMaxMs: GREET_CALIBRATE_MAX_MS,
  greetGateLastSignal: null, greetGateLastReason: null,
  postTtsHoldUntil: 0, postTtsHoldTimer: null,
  ttsMask: new TtsMask(),
  eligibility: 'blocked_pregreet', refractoryUntil: 0,
  noiseModel: new NoiseModel({ alpha: 0.2 }), vadHysteresis: new HysteresisVAD(),
  sessionNoiseFloorDb: null,
  sessionSnrMean: 0, sessionSnrM2: 0, sessionSnrStd: 0, sessionSnrSamples: 0,
  evidenceGate: new EvidenceGate(), evidenceGateCommitPromise: null,
  turnMetricsStartAt: null,
  turnMetricsTimeToFirstPartialMs: null,
  turnMetricsHadPartial: false,
  turnMetricsFinalConfidence: null,
  turnMetricsTotalSpeechMs: null,
  turnMetricsFalseStart: false,
  turnMetricsGateReason: null,
  turnMetricsBytesBufferedAtCommit: null,
  turnMetricsEmitted: false,
  turnManualBargeInUsed: false,
  manual: {
    enabled: true, modeManualOnly: false, autoCommitWhenReady: true,
    buttonDown: false, active: false, debounceUntil: 0, ignoreVadUntil: 0,
    deferCloseStream: false, sentStartFrame: false, noAudioTimer: null,
    startAt: 0, firstChunkAt: 0,
  },
  assistantPhase: 'init', assistantReady: false, lastAssistantReadyAt: 0,
  currentCommitMode: 'idle',
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
function _now() {
  try {
    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
      return performance.now();
    }
  } catch {}
  return Date.now();
}
function optsFromGlobal(key, fallback) {
  try {
    const cfg = window.__askchip_config || {};
    if (key in cfg) return cfg[key];
  } catch {}
  return fallback;
}
function _normalizePhase(value) { return typeof value === 'string' ? value.trim().toLowerCase() : ''; }
function _valueIsReadyFlag(value) {
  if (value === true || value === 1) return true;
  if (typeof value === 'string') {
    const lowered = value.trim().toLowerCase();
    if (lowered === 'true' || lowered === '1' || lowered === 'yes') return true;
  }
  return false;
}
function _updateAssistantPhaseFromDetail(detail = {}) {
  const phase = _normalizePhase(detail?.phase)
    || _normalizePhase(detail?.channel?.phase)
    || _normalizePhase(detail?.state);
  const readyFlag = _valueIsReadyFlag(detail?.ready_for_user)
    || _valueIsReadyFlag(detail?.channel?.ready_for_user);

  if (phase) {
    if (phase === 'ready' || phase === 'ready_for_user') {
      state.assistantPhase = TurnState.Ready.toLowerCase();
      state.assistantReady = true;
      state.lastAssistantReadyAt = _now();
    } else if (phase === 'speaking' || phase === 'tts') {
      state.assistantPhase = TurnState.Speaking.toLowerCase();
      state.assistantReady = false;
    } else if (phase === 'thinking') {
      state.assistantPhase = TurnState.Thinking.toLowerCase();
      state.assistantReady = false;
    } else if (phase === 'listening') {
      state.assistantPhase = TurnState.Listening.toLowerCase();
    } else {
      state.assistantPhase = phase;
    }
  }

  if (readyFlag && !state.assistantReady) {
    state.assistantReady = true;
    state.lastAssistantReadyAt = _now();
  }
  if (readyFlag === false) {
    state.assistantReady = false;
  }
}
function _clearGreetGateWaiters(result = false) {
  const waiters = Array.isArray(state.greetGateWaiters) ? state.greetGateWaiters : [];
  state.greetGateWaiters = [];
  for (const waiter of waiters) {
    try { waiter(result); } catch {}
  }
}
function _clearGreetGateCalibrateTimer() {
  if (state.greetGateCalibrateTimer) {
    try { clearTimeout(state.greetGateCalibrateTimer); } catch {}
  }
  state.greetGateCalibrateTimer = null;
  state.greetGateCalibrateUntil = 0;
  state.greetGateCalibrateLastMs = null;
}
function _resetGreetGateState() {
  _clearGreetGateCalibrateTimer();
  state.greetGatePhase = 'idle';
  state.greetGateLastSignal = null;
}
function _resolveGreetGateCalibrateMs() {
  const fallback = Number.isFinite(state.greetGateCalibrateMs)
    ? state.greetGateCalibrateMs
    : GREET_CALIBRATE_DEFAULT_MS;
  const minBase = Number.isFinite(state.greetGateCalibrateMinMs)
    ? state.greetGateCalibrateMinMs
    : GREET_CALIBRATE_MIN_MS;
  const maxBase = Number.isFinite(state.greetGateCalibrateMaxMs)
    ? state.greetGateCalibrateMaxMs
    : GREET_CALIBRATE_MAX_MS;
  const raw = Number(optsFromGlobal('greet_gate_calibrate_ms', fallback));
  const min = Math.min(minBase, maxBase);
  const max = Math.max(minBase, maxBase);
  const target = Number.isFinite(raw) ? raw : fallback;
  return Math.max(min, Math.min(max, target));
}
function _completeGreetGate(reason = 'open') {
  if (!state.greetGateActive) {
    _resetGreetGateState();
    return;
  }
  _voiceLog('info', 'greet gate released', { reason });
  state.greetGateActive = false;
  state.greetGatePhase = 'open';
  state.greetGateLastReason = reason;
  if (state.eligibility === 'blocked_pregreet') {
    state.eligibility = 'eligible';
  }
  _clearGreetGateCalibrateTimer();
  _clearGreetGateWaiters(true);
  _resetGreetGateState();
}
function _cancelGreetGate(reason = 'cancelled') {
  if (!state.greetGateActive) {
    _resetGreetGateState();
    return;
  }
  _voiceLog('info', 'greet gate cancelled', { reason });
  state.greetGateActive = false;
  state.greetGatePhase = 'cancelled';
  state.greetGateLastReason = reason;
  if (state.eligibility === 'blocked_pregreet') {
    state.eligibility = 'eligible';
  }
  _clearGreetGateCalibrateTimer();
  _clearGreetGateWaiters(false);
  _resetGreetGateState();
}
function _resetEvidenceGate(reason = null) {
  try {
    if (state?.evidenceGate && typeof state.evidenceGate.reset === 'function') {
      state.evidenceGate.reset(reason);
    }
  } catch {}
  try {
    state.evidenceGateCommitPromise = null;
  } catch {}
}
function _startGreetGateCalibrate(source = 'unknown') {
  if (!state.greetGateActive) return;
  if (state.greetGatePhase === 'calibrating' && state.greetGateCalibrateTimer) {
    state.greetGateLastSignal = source;
    return;
  }
  const durationMs = _resolveGreetGateCalibrateMs();
  const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
    ? performance.now()
    : Date.now();
  _clearGreetGateCalibrateTimer();
  state.greetGatePhase = 'calibrating';
  state.greetGateLastSignal = source;
  state.greetGateCalibrateLastMs = durationMs;
  state.greetGateCalibrateUntil = now + durationMs;
  _voiceLog('info', 'greet gate calibrating', {
    source,
    calibrateMs: durationMs,
    ttsPlaying: !!state.ttsPlaying,
  });
  try {
    state.greetGateCalibrateTimer = setTimeout(() => {
      state.greetGateCalibrateTimer = null;
      state.greetGateCalibrateUntil = 0;
      _completeGreetGate('calibrated');
    }, durationMs);
  } catch (err) {
    _voiceLog('warn', 'failed to start greet calibrate timer', { error: err?.message || err });
    _completeGreetGate('calibrate_timer_failed');
  }
}
function _waitForGreetGate() {
  if (!state.greetGateActive) return null;
  if (state.greetGatePhase !== 'pending' && state.greetGatePhase !== 'calibrating') return null;
  return new Promise((resolve) => { state.greetGateWaiters.push((result) => resolve(result !== false)); });
}
function _handleGreetGateUtteranceEnd(detail = {}) {
  if (!state.greetGateActive) return;
  state.ttsPlaying = false;
  _voiceLog('debug', 'greet gate observed UtteranceEnd', {
    phase: state.greetGatePhase,
    reason: state.greetGateLastReason,
    ttsPlaying: !!state.ttsPlaying,
  });
  _startGreetGateCalibrate('UtteranceEnd');
}
function _handleGreetGateStateFrame(detail = {}) {
  if (!state.greetGateActive) return;
  _updateAssistantPhaseFromDetail(detail);
  const channelReady = _valueIsReadyFlag(detail?.channel?.ready_for_user);
  const ready = _valueIsReadyFlag(detail?.ready_for_user) || channelReady;
  const stateField = _normalizePhase(detail?.state);
  const phaseField = _normalizePhase(detail?.phase);
  const channelPhaseField = _normalizePhase(detail?.channel?.phase);
  const explicitReady = ['ready_for_user', 'ready'].some((target) =>
    stateField === target || phaseField === target || channelPhaseField === target
  );
  if (ready || explicitReady) {
    _voiceLog('debug', 'greet gate observed ready state', {
      phase: state.greetGatePhase,
      channelReady,
      ready,
      explicitReady,
      stateField,
      phaseField,
      channelPhaseField,
      ttsPlaying: !!state.ttsPlaying,
    });
    _startGreetGateCalibrate('ready_for_user');
  }
}
function _setActiveTurnTraceId(traceId) { state.turnTraceId = traceId || null; try { window.__askchip_turn_trace_id = state.turnTraceId; } catch {} }
function _getActiveTurnTraceId() { return state.turnTraceId || null; }
function _ensureTurnTraceBase() {
  if (!state.turnTraceBase) { state.turnTraceBase = Math.floor(Math.random() * 46656).toString(36).padStart(3, '0'); }
}
function _beginTurnTrace(reason = 'turn_start') {
  _ensureTurnTraceBase();
  state.turnTraceSeq = (state.turnTraceSeq || 0) + 1;
  const traceId = `${state.turnTraceBase}_${state.turnTraceSeq}`;
  _setActiveTurnTraceId(traceId);
  _voiceLog('info', 'turn trace started', { reason });
  return traceId;
}
function _clearTurnTrace() { if (_getActiveTurnTraceId()) { _voiceLog('info', 'turn trace cleared'); _setActiveTurnTraceId(null); } }
function _withTrace(detail = {}) {
  const traceId = _getActiveTurnTraceId();
  if (!traceId) return detail;
  if (detail && typeof detail === 'object') { return detail.traceId === traceId ? detail : { ...detail, traceId }; }
  return { value: detail, traceId };
}
function _formatVoiceMessage(message) {
  const traceId = _getActiveTurnTraceId();
  return traceId ? `[voice][trace:${traceId}] ${message}` : `[voice] ${message}`;
}
function _voiceLog(level, message, detail = undefined) {
  try {
    const method = typeof console?.[level] === 'function' ? console[level] : console.log;
    if (!method) return;
    const formatted = _formatVoiceMessage(message);
    if (detail === undefined) {
      method.call(console, formatted);
      return;
    }
    if (detail && typeof detail === 'object') {
      method.call(console, formatted, _withTrace(detail));
      return;
    }
    const traceId = _getActiveTurnTraceId();
    if (traceId) {
      method.call(console, `${formatted} trace:${traceId}`, detail);
      return;
    }
    method.call(console, formatted, detail);
  } catch {}
}
function _logLifecycle(event, detail = {}, level = 'debug') {
  const payload = { event, ...(detail && typeof detail === 'object' ? detail : { detail }) };
  _voiceLog(level, event, payload);
  try {
    window.dispatchEvent(new CustomEvent('askchip-voice-lifecycle', { detail: payload }));
  } catch {}
}

export {
  WEBM_MIME,
  REC_MIME,
  DEFAULT_MAX_TURN_MS,
  MIN_VALID_BLOB_BYTES,
  PRE_ROLL_MS,
  SAFETY_CLOSE_DELAY_MS,
  MANUAL_DEBOUNCE_MS,
  MANUAL_NO_AUDIO_CANCEL_MS,
  MANUAL_VAD_IGNORE_MS,
  EVIDENCE_MIN_SPEECH_MS,
  EVIDENCE_MIN_BYTES,
  EVIDENCE_MIN_SNR_DB,
  PARTIAL_CONF_THRESHOLD,
  PARTIAL_CONF_RISE_DELTA,
  GREET_BARGE_MIN_SNR_DB,
  GREET_CALIBRATE_DEFAULT_MS,
  GREET_CALIBRATE_MIN_MS,
  GREET_CALIBRATE_MAX_MS,
  BARGE_CONFIRM_DEFAULT_MS,
  bargeConfirmMs,
  state,
  _now,
  optsFromGlobal,
  _normalizePhase,
  _valueIsReadyFlag,
  _updateAssistantPhaseFromDetail,
  _clearGreetGateWaiters,
  _clearGreetGateCalibrateTimer,
  _resetGreetGateState,
  _resolveGreetGateCalibrateMs,
  _completeGreetGate,
  _cancelGreetGate,
  _resetEvidenceGate,
  _startGreetGateCalibrate,
  _waitForGreetGate,
  _handleGreetGateUtteranceEnd,
  _handleGreetGateStateFrame,
  _setActiveTurnTraceId,
  _getActiveTurnTraceId,
  _ensureTurnTraceBase,
  _beginTurnTrace,
  _clearTurnTrace,
  _withTrace,
  _formatVoiceMessage,
  _voiceLog,
  _logLifecycle,
};
