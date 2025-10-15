import { sendJSON } from '../../ws_module.js';
import { resumePlayback } from '../../audio.js';
import { emitVoiceEvent } from '../ui/Events.js';
import {
  optsFromGlobal,
  _voiceLog,
  _cancelGreetGate,
  _resetEvidenceGate,
  _clearTurnTrace,
  state,
} from './VoiceLegacyTopLevel.js';
import {
  legacyClearManualTimers,
  legacyClearPostTtsHoldTimer,
  legacyEnsureMic,
  legacyTeardownPreRollTap,
  legacyStopRecorder,
  legacyClearSafetyCloseTimer,
} from './VoiceLegacyFacade.js';

export function maybeSendAudioStop(detail = {}) {
  if (state.audioStopSent) {
    return false;
  }
  try {
    sendJSON({ type: 'AudioStop' });
    state.audioStopSent = true;
    _voiceLog('info', 'AudioStop sent', detail && typeof detail === 'object' ? detail : { detail });
    return true;
  } catch (err) {
    _voiceLog('warn', 'failed to send AudioStop', {
      error: err?.message || err,
      ...(detail && typeof detail === 'object' ? detail : { detail }),
    });
    return false;
  }
}

export function clearPendingEndTimer() {
  if (state.pendingEndTimer) {
    try { clearTimeout(state.pendingEndTimer); } catch {}
    state.pendingEndTimer = null;
  }
}

export function clearPostTtsHoldTimer() {
  legacyClearPostTtsHoldTimer();
}

export function clearBargeConfirm(resume = false) {
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

export function refreshManualConfig() {
  const enabled = !!optsFromGlobal('feature_manual_barge_in', true);
  const manualOnly = !!optsFromGlobal('barge_in_mode_manual', true);
  const autoCommit = !!optsFromGlobal('auto_commit_when_ready', true);
  state.manual.enabled = enabled;
  state.manual.modeManualOnly = manualOnly;
  state.manual.autoCommitWhenReady = autoCommit;
  return enabled;
}

export async function ensureMic(externalStream = null) {
  return legacyEnsureMic(externalStream, {
    teardownAudioGraph: () => teardownAudioGraph(),
  });
}

export function safeClearTurnTimer() {
  if (state.turnTimer) {
    clearTimeout(state.turnTimer);
    state.turnTimer = null;
  }
}

export function teardownVadOnly() {
  try { state.vad && state.vad.stop(); } catch {}
  state.vad = null;
}

export function removeWsListener() {
  if (!state.wsListener || typeof window === 'undefined') {
    return;
  }
  try { window.removeEventListener('askchip-ws', state.wsListener); } catch {}
  state.wsListener = null;
}

export function teardownAudioGraph() {
  legacyTeardownPreRollTap();
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
  removeWsListener();
}

export function disarm() {
  safeClearTurnTimer();
  clearPendingEndTimer();
  legacyClearSafetyCloseTimer();
  clearBargeConfirm(false);
  legacyStopRecorder({ reason: 'manual_disarm' });
  teardownVadOnly();
  state.turnOpen = false;
  state.turnClosePromise = null;
  state.recStartedAt = 0;
  state.lastChunkAt = 0;
  state.ttsPlaying = false;
  state.finalized = false;
  state.postFinalHoldUntil = 0;
  state.postTtsHoldUntil = 0;
  clearPostTtsHoldTimer();
  state.eligibility = 'blocked_pregreet';
  state.refractoryUntil = Date.now();
  state.vadMetrics = null;
  _cancelGreetGate('disarm');
  _resetEvidenceGate('disarm');
  removeWsListener();
  _clearTurnTrace();
  state.manual.buttonDown = false;
  state.manual.active = false;
  state.manual.deferCloseStream = false;
  state.manual.sentStartFrame = false;
  state.manual.ignoreVadUntil = 0;
  state.manual.debounceUntil = 0;
  state.manual.startAt = 0;
  state.manual.firstChunkAt = 0;
  legacyClearManualTimers();
  state.currentCommitMode = 'idle';
  state.assistantReady = false;
  state.assistantPhase = 'init';
  state.lastAssistantReadyAt = 0;
  emitVoiceEvent('state', { state: 'idle' });
}
