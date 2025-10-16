import { emitVoiceEvent } from '../ui/Events.js';
import { TurnState } from '../core/index.js';
import { sendJSON } from '../../ws_module.js';
import { stopPlayback, isPlaying as ttsIsPlaying } from '../../audio.js';
import { registerTtsEventListener } from '../tts/TtsHandlers.js';
import { updateSessionNoise, getEvidenceSnrRequirement, getShadowStats } from '../loops/VadLoop.js';
import {
  armVAD as facadeArmVAD, bargeIn as facadeBargeIn, disarmVAD as facadeDisarmVAD,
  forceBargeInEnd as facadeForceBargeInEnd, forceBargeInStart as facadeForceBargeInStart,
  initMic as facadeInitMic, isRecording as facadeIsRecording, onWsClose as facadeOnWsClose,
  onWsMessage as facadeOnWsMessage, onWsOpen as facadeOnWsOpen, registerVoiceLegacyFacade,
  setGreetGateActive as facadeSetGreetGateActive, setVadBoost as facadeSetVadBoost,
  legacyClearManualTimers, legacyClearSafetyCloseTimer, legacyCloseTurnIfOpen,
  legacySetGreetGateActive, legacyOnWsCloseImpl, legacyOnWsMessageImpl, legacyOnWsOpenImpl,
  legacyOnMicAvailable, legacyOnMicStop, legacyOnRecorderData, legacyOnRecorderError,
  legacyResetEvidenceGate, legacySendRecorderChunk, legacyPrimeRecorderForPreRoll,
  legacyStartRecorder, legacyStopRecorder, legacyAbortEvidenceGate, legacyEvaluateEvidenceGate,
  legacyCommitEvidenceGate, legacyUpdateEvidenceGateWithChunk, legacyUpdateEvidenceGateWithPartial,
  createVadSchedulerLegacy, onFrameSpeech as facadeOnFrameSpeech, startVadLoop as facadeStartVadLoop,
  onTtsStart, onTtsEnd, VadFrameUtils, onSpeechStartCommitted as facadeOnSpeechStartCommitted,
  onSpeechEndCommitted as facadeOnSpeechEndCommitted, bootstrapLegacyFacade, legacyEmitTurnMetrics,
} from './VoiceLegacyFacade.js';
import {
  DEFAULT_MAX_TURN_MS, EVIDENCE_MIN_BYTES, EVIDENCE_MIN_SNR_DB, EVIDENCE_MIN_SPEECH_MS,
  GREET_BARGE_MIN_SNR_DB, MANUAL_DEBOUNCE_MS, MANUAL_NO_AUDIO_CANCEL_MS, MANUAL_VAD_IGNORE_MS,
  PARTIAL_CONF_RISE_DELTA, PARTIAL_CONF_THRESHOLD, PRE_ROLL_MS, REC_MIME, SAFETY_CLOSE_DELAY_MS,
  WEBM_MIME, _beginTurnTrace, _cancelGreetGate, _clearTurnTrace, _completeGreetGate,
  _handleGreetGateStateFrame, _handleGreetGateUtteranceEnd, _logLifecycle, _now,
  _registerAsrReadyListener, _resetAsrReady, _updateAssistantPhaseFromDetail, _voiceLog,
  _waitForGreetGate, _getActiveTurnTraceId, optsFromGlobal, state,
} from './VoiceLegacyTopLevel.js';

let _resetEvidenceGate;
let _clearSafetyCloseTimer;
let _closeTurnIfOpen;
let _sendRecorderChunk;
let _stopRecorder;

let _setGreetGateActive;
let _abortEvidenceGate;
let _evaluateEvidenceGate;
let _commitEvidenceGate;
let _updateEvidenceGateWithChunk;
let _updateEvidenceGateWithPartial;
let _manualAutoCancel;
let _forceBargeInStart;
let _forceBargeInEnd;
let _ensureWSListener;
let _primeRecorderForPreRoll;
let _startRecorder;
let _bargeIn;
let _arm;

const commitCtx = { onFrameSpeech: facadeOnFrameSpeech };
const handleSpeechStartCommitted = (detail = {}) => facadeOnSpeechStartCommitted(commitCtx, detail);
const handleSpeechEndCommitted = (detail = null) => facadeOnSpeechEndCommitted(commitCtx, detail);
commitCtx.onSpeechStartCommitted = handleSpeechStartCommitted;
commitCtx.onSpeechEndCommitted = handleSpeechEndCommitted;

({
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
  onSpeechEndCommitted: handleSpeechEndCommitted,
  onSpeechStartCommitted: handleSpeechStartCommitted,
}));

({
  resetEvidenceGate: _resetEvidenceGate,
  clearSafetyCloseTimer: _clearSafetyCloseTimer,
  closeTurnIfOpen: _closeTurnIfOpen,
  sendRecorderChunk: _sendRecorderChunk,
  stopRecorder: _stopRecorder,
} = bootstrapLegacyFacade({
  registerVoiceLegacyFacade,
  VadFrameUtils,
  state,
  arm: _arm,
  bargeIn: _bargeIn,
  setGreetGateActive: _setGreetGateActive,
  forceBargeInStart: _forceBargeInStart,
  forceBargeInEnd: _forceBargeInEnd,
  legacyOnWsOpenImpl,
  legacyOnWsMessageImpl,
  legacyOnWsCloseImpl,
  legacyOnMicAvailable,
  legacyOnMicStop,
  legacyOnRecorderData,
  legacyOnRecorderError,
  legacyResetEvidenceGate,
  legacyClearSafetyCloseTimer,
  legacyCloseTurnIfOpen,
  legacySendRecorderChunk,
  legacyStopRecorder,
}));

Object.assign(commitCtx, {
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
  startRecorder: (...args) => _startRecorder?.(...args),
  primeRecorderForPreRoll: (...args) => _primeRecorderForPreRoll?.(...args),
  bargeIn: (...args) => _bargeIn?.(...args),
  resetEvidenceGate: _resetEvidenceGate,
  evaluateEvidenceGate: (...args) => _evaluateEvidenceGate?.(...args),
  now: _now,
  logLifecycle: _logLifecycle,
  voiceLog: _voiceLog,
  beginTurnTrace: _beginTurnTrace,
  completeGreetGate: _completeGreetGate,
  getActiveTurnTraceId: _getActiveTurnTraceId,
  abortEvidenceGate: (...args) => _abortEvidenceGate?.(...args),
  clearSafetyCloseTimer: _clearSafetyCloseTimer,
  stopRecorder: _stopRecorder,
  performance: typeof performance !== 'undefined' ? performance : undefined,
  emitTurnMetrics: (...args) => legacyEmitTurnMetrics?.(...args),
  registerAsrReadyListener: (...args) => _registerAsrReadyListener?.(...args),
  resetAsrReady: (...args) => _resetAsrReady?.(...args),
});

Object.assign(commitCtx, {
  startRecorder: _startRecorder,
  primeRecorderForPreRoll: _primeRecorderForPreRoll,
  bargeIn: _bargeIn,
  evaluateEvidenceGate: _evaluateEvidenceGate,
  abortEvidenceGate: _abortEvidenceGate,
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

export const __TEST_ONLY__ = {
  state,
  startRecorder: _startRecorder,
  stopRecorder: _stopRecorder,
  ensureWSListener: _ensureWSListener,
  closeTurnIfOpen: _closeTurnIfOpen,
  sendRecorderChunk: _sendRecorderChunk,
  clearSafetyCloseTimer: _clearSafetyCloseTimer,
  onSpeechStartCommitted: handleSpeechStartCommitted,
  onSpeechEndCommitted: handleSpeechEndCommitted,
};
