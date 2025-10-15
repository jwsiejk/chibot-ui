import { emitVoiceEvent } from '../ui/Events.js';

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
  const { state, abortEvidenceGate, ttsIsPlaying, TurnState } = ctx;
  if (!state) {
    return false;
  }

  const { detail, stateValue } = extractDetailAndState(ctx);
  if (stateValue !== 'playing') {
    return false;
  }

  const nowFn = resolveNowFn(ctx.now);
  state.ttsPlaying = true;
  state.assistantReady = false;
  state.assistantPhase = typeof TurnState?.Speaking === 'string'
    ? TurnState.Speaking.toLowerCase()
    : 'speaking';
  state.postTtsHoldUntil = nowFn() + POST_TTS_HOLDOFF_MS;
  if (state.ttsMask && typeof state.ttsMask.start === 'function') {
    state.ttsMask.start();
  }
  if (state.evidenceGate?.isOpen?.() && typeof abortEvidenceGate === 'function') {
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

  if (!isPrime && playbackActive && state.eligibility === 'blocked_pregreet') {
    state.eligibility = 'holdoff';
  }

  emitVoiceEvent('tts', {
    state: 'playing',
    prime: isPrime,
    playbackConfirmed: playbackConfirmed === true,
  });

  return true;
}

export function onTtsEnd(ctx = {}) {
  const { state, clearPostTtsHoldTimer, TurnState } = ctx;
  if (!state) {
    return false;
  }

  const { stateValue } = extractDetailAndState(ctx);
  state.ttsPlaying = stateValue === 'playing';
  if (!ENDED_STATES.has(stateValue)) {
    return false;
  }

  const nowFn = resolveNowFn(ctx.now);
  state.ttsPlaying = false;
  state.postTtsHoldUntil = 0;
  if (typeof clearPostTtsHoldTimer === 'function') {
    clearPostTtsHoldTimer();
  }

  const sigma = Number.isFinite(state.sessionSnrStd) ? state.sessionSnrStd : 0;
  if (state.ttsMask && typeof state.ttsMask.end === 'function') {
    state.ttsMask.end({
      decayMs: TTS_DECAY_MS,
      snrBoost: Math.max(3, sigma * 1.5),
    });
  }

  state.assistantReady = true;
  state.assistantPhase = typeof TurnState?.Ready === 'string'
    ? TurnState.Ready.toLowerCase()
    : 'ready';
  state.lastAssistantReadyAt = nowFn();
  if (state.eligibility === 'holdoff') {
    state.eligibility = 'eligible';
  }

  emitVoiceEvent('tts', { state: stateValue || 'ended' });

  return true;
}

export function registerTtsEventListener({ createContext, onTtsStart: startFn, onTtsEnd: endFn, windowRef } = {}) {
  const win = windowRef || (typeof window !== 'undefined' ? window : null);
  const addEventListener = win?.addEventListener?.bind(win);
  if (!addEventListener) {
    return;
  }

  try {
    addEventListener('chip-tts', (event) => {
      const contextBase = typeof createContext === 'function' ? createContext(event) || {} : {};
      contextBase.event = event;
      const handledStart = typeof startFn === 'function' ? startFn(contextBase) : false;
      if (handledStart) {
        return;
      }
      const handledEnd = typeof endFn === 'function' ? endFn(contextBase) : false;
      if (handledEnd) {
        return;
      }
    });
  } catch {}
}
