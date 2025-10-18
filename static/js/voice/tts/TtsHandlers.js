import { TtsMask } from '../core/TtsMask.js';
import { getConfig } from '../core/index.js';
import { emitVoiceEvent } from '../ui/Events.js';

const POST_TTS_HOLDOFF_MS = 600;
const ENDED_STATES = new Set(['ended', 'stopped', 'idle', 'paused', '']);

const normalizeStateValue = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

const extractDetailAndState = (ctx = {}) => {
  const detail = ctx.detail ?? ctx.event?.detail ?? {};
  const stateValue = normalizeStateValue(detail?.state);
  ctx.detail = detail;
  ctx.stateValue = stateValue;
  return { detail, stateValue };
};

const resolveNowFn = (now) => {
  if (typeof now === 'function') {
    return now;
  }
  return () => Date.now();
};

const isMaskLike = (value) => {
  if (!value || typeof value !== 'object') {
    return false;
  }
  if (value instanceof TtsMask) {
    return true;
  }
  return typeof value.start === 'function' && typeof value.end === 'function';
};

const ensureMask = (ctx, state) => {
  let mask = isMaskLike(ctx?.ttsMask) ? ctx.ttsMask : null;
  if (!mask && isMaskLike(state?.ttsMask)) {
    mask = state.ttsMask;
    ctx.ttsMask = mask;
  }
  if (!mask) {
    mask = new TtsMask();
    if (ctx) {
      ctx.ttsMask = mask;
    }
    if (state) {
      state.ttsMask = mask;
    }
  }
  return mask;
};

export function onTtsStart(ctx = {}) {
  const { state: ctxState, abortEvidenceGate, ttsIsPlaying, TurnState } = ctx;
  if (!ctxState) {
    return false;
  }

  const { detail, stateValue } = extractDetailAndState(ctx);
  if (stateValue !== 'playing') {
    return false;
  }

  const mask = ensureMask(ctx, ctxState);
  const nowFn = resolveNowFn(ctx.now);

  ctxState.ttsPlaying = true;
  ctxState.assistantReady = false;
  ctxState.assistantPhase = typeof TurnState?.AssistantSpeaking === 'string'
    ? 'assistant_speaking'
    : 'speaking';
  ctxState.postTtsHoldUntil = nowFn() + POST_TTS_HOLDOFF_MS;
  if (typeof mask?.start === 'function') {
    mask.start();
  }
  ctx.autoVadMasked = true;
  ctxState.autoVadMasked = true;

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

  const mask = ensureMask(ctx, ctxState);
  const cfg = typeof getConfig === 'function' ? getConfig() : null;
  const decayMsRaw = cfg?.tts?.decay_ms;
  const sigma = Number.isFinite(ctxState.sessionSnrStd) ? ctxState.sessionSnrStd : 0;
  const maskOptions = {
    decayMs: Number.isFinite(decayMsRaw) ? decayMsRaw : undefined,
  };
  if (Number.isFinite(sigma) && sigma > 0) {
    maskOptions.snrBoost = Math.max(3, sigma * 1.5);
  }
  if (typeof mask?.end === 'function') {
    mask.end(maskOptions);
  }
  ctx.autoVadMasked = false;
  ctxState.autoVadMasked = false;

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

export function registerTtsEventListener({ createContext, onTtsStart: startFn, onTtsEnd: endFn, windowRef } = {}) {
  const win = windowRef || (typeof window !== 'undefined' ? window : null);
  const addEventListener = win?.addEventListener?.bind(win);
  if (!addEventListener) {
    return;
  }

  try {
    let ctx;
    addEventListener('chip-tts', (event) => {
      if (!ctx) {
        ctx = typeof createContext === 'function' ? createContext(event) || {} : {};
      }
      ctx.event = event;
      ctx.detail = event?.detail;
      const handledStart = typeof startFn === 'function' ? startFn(ctx) : false;
      if (handledStart) {
        return;
      }
      const handledEnd = typeof endFn === 'function' ? endFn(ctx) : false;
      if (handledEnd) {
        return;
      }
    });
  } catch {}
}
