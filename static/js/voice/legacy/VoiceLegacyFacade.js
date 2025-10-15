import { emitVoiceEvent } from '../ui/Events.js';
import { resetShadowBufferState } from '../core/index.js';
import { sendAudioChunk, sendCloseStream, sendJSON, waitWSOpen } from '../../ws_module.js';
import {
  MIN_VALID_BLOB_BYTES,
  REC_MIME,
  SAFETY_CLOSE_DELAY_MS,
  optsFromGlobal,
  _handleGreetGateStateFrame,
  _handleGreetGateUtteranceEnd,
  _logLifecycle,
  _updateAssistantPhaseFromDetail,
  _voiceLog,
  _waitForGreetGate,
  state,
} from './VoiceLegacyTopLevel.js';

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
