import { emitVoiceEvent } from './voice/ui/Events.js';
import { TurnState } from './voice/core/index.js';
import { sendJSON } from './ws_module.js';
import { stopPlayback, isPlaying as ttsIsPlaying } from './audio.js';
import { registerTtsEventListener } from './voice/tts/TtsHandlers.js';
import {
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
  onWsClose as facadeOnWsClose,
  onWsMessage as facadeOnWsMessage,
  onWsOpen as facadeOnWsOpen,
  registerVoiceLegacyFacade,
  setGreetGateActive as facadeSetGreetGateActive,
  setVadBoost as facadeSetVadBoost,
  legacyClearManualTimers,
  legacyClearSafetyCloseTimer,
  legacyCloseTurnIfOpen,
  legacySetGreetGateActive,
  legacyOnWsCloseImpl,
  legacyOnWsMessageImpl,
  legacyOnWsOpenImpl,
  legacyOnMicAvailable,
  legacyOnMicStop,
  legacyOnRecorderData,
  legacyOnRecorderError,
  legacyResetEvidenceGate,
  legacySendRecorderChunk,
  legacyPrimeRecorderForPreRoll,
  legacyStartRecorder,
  legacyStopRecorder,
  legacyAbortEvidenceGate,
  legacyEvaluateEvidenceGate,
  legacyCommitEvidenceGate,
  legacyUpdateEvidenceGateWithChunk,
  legacyUpdateEvidenceGateWithPartial,
  createVadSchedulerLegacy,
  onFrameSpeech as facadeOnFrameSpeech,
  startVadLoop as facadeStartVadLoop,
  onTtsStart,
  onTtsEnd,
  VadFrameUtils,
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
  PARTIAL_CONF_RISE_DELTA,
  PARTIAL_CONF_THRESHOLD,
  PRE_ROLL_MS,
  REC_MIME,
  SAFETY_CLOSE_DELAY_MS,
  WEBM_MIME,
  _beginTurnTrace,
  _cancelGreetGate,
  _clearTurnTrace,
  _completeGreetGate,
  _handleGreetGateStateFrame,
  _handleGreetGateUtteranceEnd,
  _logLifecycle,
  _now,
  _updateAssistantPhaseFromDetail,
  _voiceLog,
  _waitForGreetGate,
  _getActiveTurnTraceId,
  optsFromGlobal,
  state,
} from './voice/legacy/VoiceLegacyTopLevel.js';

const _resetEvidenceGate = legacyResetEvidenceGate;
const _clearSafetyCloseTimer = legacyClearSafetyCloseTimer;
const _closeTurnIfOpen = legacyCloseTurnIfOpen;
const _sendRecorderChunk = legacySendRecorderChunk;
const _stopRecorder = legacyStopRecorder;

const {
  setGreetGateActive: _setGreetGateActive,
  abortEvidenceGate: _abortEvidenceGate,
  evaluateEvidenceGate: _evaluateEvidenceGate,
  commitEvidenceGate: _commitEvidenceGate,
  updateEvidenceGateWithChunk: _updateEvidenceGateWithChunk,
  updateEvidenceGateWithPartial: _updateEvidenceGateWithPartial,
  manualAutoCancel: _manualAutoCancel,
  forceBargeInStart: _forceBargeInStart,
  forceBargeInEnd: _forceBargeInEnd,
  ensureWSListener: _ensureWSListener,
  primeRecorderForPreRoll: _primeRecorderForPreRoll,
  startRecorder: _startRecorder,
  bargeIn: _bargeIn,
  arm: _arm,
} = createVadSchedulerLegacy({
  state,
  emitVoiceEvent,
  sendJSON,
  stopPlayback,
  ttsIsPlaying,
  startVadLoop: facadeStartVadLoop,
  forceBargeInEnd: facadeForceBargeInEnd,
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
  now: _now,
  logLifecycle: _logLifecycle,
  voiceLog: _voiceLog,
  MANUAL_DEBOUNCE_MS,
  MANUAL_NO_AUDIO_CANCEL_MS,
  MANUAL_VAD_IGNORE_MS,
  onSpeechEndCommitted: _onSpeechEndCommitted,
  onSpeechStartCommitted: _onSpeechStartCommitted,
});

export async function initMic(stream = null) { return await facadeInitMic(stream); }
export async function armVAD(stream = null, opts = {}) { return await facadeArmVAD(stream, opts); }
export function disarmVAD() { facadeDisarmVAD(); }
export function isRecording() { return !!facadeIsRecording(); }
export function bargeIn() { facadeBargeIn(); }
export function setVadBoost(value) { facadeSetVadBoost(value); }
export function setGreetGateActive(active = true) { facadeSetGreetGateActive(!!active); }
export function forceBargeInStart(meta = {}) { return facadeForceBargeInStart(meta); }
export function forceBargeInEnd(opts = {}) { return facadeForceBargeInEnd(opts); }

VadFrameUtils.refreshManualConfig();
registerTtsEventListener({ createContext: () => ({ state, now: _now, abortEvidenceGate: _abortEvidenceGate, ttsIsPlaying, clearPostTtsHoldTimer: VadFrameUtils.clearPostTtsHoldTimer, TurnState }), onTtsStart, onTtsEnd });

async function _onSpeechStartCommitted(detail = {}) {
  return await facadeOnFrameSpeech({
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
    startRecorder: _startRecorder,
    primeRecorderForPreRoll: _primeRecorderForPreRoll,
    bargeIn: _bargeIn,
    resetEvidenceGate: _resetEvidenceGate,
    evaluateEvidenceGate: _evaluateEvidenceGate,
    onSpeechStartCommitted: _onSpeechStartCommitted,
    now: _now,
    logLifecycle: _logLifecycle,
    voiceLog: _voiceLog,
    beginTurnTrace: _beginTurnTrace,
    completeGreetGate: _completeGreetGate,
    getActiveTurnTraceId: _getActiveTurnTraceId,
  }, detail);
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
    VadFrameUtils.clearBargeConfirm(true);
  }

  // If we haven't recorded at least minTurnMs, delay honoring VAD-end.
  // Only applies while recorder is actually running.
  if (state.rec && typeof state.rec.state === 'string' && state.rec.state === 'recording') {
    const elapsed = Math.max(0, now - (state.recStartedAt || now));
    const wait = Math.max(0, minTurnMs - elapsed);
    if (wait > 0) {
      _voiceLog('debug', 'delaying VAD end', { waitMs: wait, elapsed });
      VadFrameUtils.clearPendingEndTimer();
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
  VadFrameUtils.maybeSendAudioStop({ reason });
  VadFrameUtils.safeClearTurnTimer();
  VadFrameUtils.clearPendingEndTimer();
  _clearSafetyCloseTimer();
  _stopRecorder({ reason });
  // Do NOT send CloseStream here; we send it in rec.onstop AFTER the blob is delivered.
}

registerVoiceLegacyFacade({
  initMic: (stream = null) => VadFrameUtils.ensureMic(stream),
  armVAD: (stream = null, opts = {}) => _arm(stream, opts),
  disarmVAD: () => { VadFrameUtils.disarm(); },
  isRecording: () => !!(state.rec && state.rec.state === 'recording'),
  bargeIn: () => { _bargeIn(); },
  setVadBoost: (_value) => {},
  setGreetGateActive: (active = true) => { _setGreetGateActive(!!active); },
  forceBargeInStart: (meta = {}) => _forceBargeInStart(meta),
  forceBargeInEnd: (opts = {}) => _forceBargeInEnd(opts),
  onWsOpen: (detail = null) => { legacyOnWsOpenImpl(detail); },
  onWsMessage: (detail = {}, helpers = {}) => legacyOnWsMessageImpl(detail, helpers),
  onWsClose: (detail = null) => { legacyOnWsCloseImpl(detail); },
  onMicAvailable: (detail = {}) => { legacyOnMicAvailable(detail); },
  onMicStop: (detail = {}) => legacyOnMicStop(detail),
  onRecorderData: (event, helpers = {}) => legacyOnRecorderData(event, helpers),
  onRecorderError: (event = null, helpers = {}) => legacyOnRecorderError(event, helpers),
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
