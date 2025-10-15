import { TurnState } from '../core/TurnState.js';
import { emitVoiceEvent } from '../ui/Events.js';

export function setState(next, ctx) {
  if (ctx && typeof ctx === 'object') {
    ctx.state = next;
  }
  return next;
}

export function emitVoiceState(stateName, detail = undefined) {
  const payload = detail && typeof detail === 'object'
    ? { state: stateName, ...detail }
    : { state: stateName };
  emitVoiceEvent('state', payload);
  return stateName;
}

export function normalizePhase(value) {
  if (typeof value !== 'string') return '';
  return value.trim().toLowerCase();
}

export function valueIsReadyFlag(value) {
  if (value === true || value === 1) return true;
  if (typeof value === 'string') {
    const lowered = value.trim().toLowerCase();
    if (lowered === 'true' || lowered === '1' || lowered === 'yes') return true;
  }
  return false;
}

export function updateAssistantPhaseFromDetail(ctx, detail = {}, nowProvider = Date.now) {
  if (!ctx || typeof ctx !== 'object') {
    return;
  }

  const resolveNow = () => {
    try {
      return typeof nowProvider === 'function' ? nowProvider() : nowProvider;
    } catch {
      return Date.now();
    }
  };

  const phase = normalizePhase(detail?.phase)
    || normalizePhase(detail?.channel?.phase)
    || normalizePhase(detail?.state);

  const readyFlag = valueIsReadyFlag(detail?.ready_for_user)
    || valueIsReadyFlag(detail?.channel?.ready_for_user);

  if (phase) {
    if (phase === 'ready' || phase === 'ready_for_user') {
      ctx.assistantPhase = TurnState.Ready.toLowerCase();
      ctx.assistantReady = true;
      ctx.lastAssistantReadyAt = resolveNow();
    } else if (phase === 'speaking' || phase === 'tts') {
      ctx.assistantPhase = TurnState.Speaking.toLowerCase();
      ctx.assistantReady = false;
    } else if (phase === 'thinking') {
      ctx.assistantPhase = TurnState.Thinking.toLowerCase();
      ctx.assistantReady = false;
    } else if (phase === 'listening') {
      ctx.assistantPhase = TurnState.Listening.toLowerCase();
    } else {
      ctx.assistantPhase = phase;
    }
  }

  if (readyFlag && !ctx.assistantReady) {
    ctx.assistantReady = true;
    ctx.lastAssistantReadyAt = resolveNow();
  }
  if (readyFlag === false) {
    ctx.assistantReady = false;
  }
}

export function clearGreetGateWaiters(ctx, result = false) {
  if (!ctx || typeof ctx !== 'object') {
    return;
  }
  const waiters = Array.isArray(ctx.greetGateWaiters) ? ctx.greetGateWaiters : [];
  ctx.greetGateWaiters = [];
  for (const waiter of waiters) {
    try { waiter(result); } catch {}
  }
}

export function clearGreetGateCalibrateTimer(ctx) {
  if (!ctx || typeof ctx !== 'object') {
    return;
  }
  if (ctx.greetGateCalibrateTimer) {
    try { clearTimeout(ctx.greetGateCalibrateTimer); } catch {}
  }
  ctx.greetGateCalibrateTimer = null;
  ctx.greetGateCalibrateUntil = 0;
  ctx.greetGateCalibrateLastMs = null;
}

export function resetGreetGateState(ctx) {
  if (!ctx || typeof ctx !== 'object') {
    return;
  }
  clearGreetGateCalibrateTimer(ctx);
  ctx.greetGatePhase = 'idle';
  ctx.greetGateLastSignal = null;
}
