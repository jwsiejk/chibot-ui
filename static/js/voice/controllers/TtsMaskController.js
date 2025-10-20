// Moved from AdaptiveRuntime on 2025-10-20. No behavior change.
import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';
import { getEvidenceSnrRequirement } from '../loops/VadLoop.js';

const MASK_LOG_INTERVAL_MS = 180;
const MASK_DECAY_MIN_MS = 0;
const MASK_DECAY_MAX_MS = 600;

export function clampMaskDecayMs(value, { min = MASK_DECAY_MIN_MS, max = MASK_DECAY_MAX_MS } = {}) {
  if (!Number.isFinite(value) || value <= 0) {
    return 0;
  }
  const lowerBound = Number.isFinite(min) ? Math.max(MASK_DECAY_MIN_MS, min) : MASK_DECAY_MIN_MS;
  const maxCandidate = Number.isFinite(max) && max > 0 ? max : MASK_DECAY_MAX_MS;
  const upperBound = Math.min(MASK_DECAY_MAX_MS, Math.max(lowerBound || 0, maxCandidate));
  return Math.min(upperBound, Math.max(lowerBound, value));
}

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { clearAll, force, ...rest } = detail;
  return { ...rest };
}

export default class TtsMaskController {
  constructor({ ctx, engage, release, voiceLog, nowMs, getEvidenceSnrRequirement: snrRequirement } = {}) {
    this.ctx = ctx;
    this._engageImpl = typeof engage === 'function' ? engage : null;
    this._releaseImpl = typeof release === 'function' ? release : null;
    this._reasons = new Set();
    this._voiceLog = typeof voiceLog === 'function' ? voiceLog : null;
    this._nowMs = typeof nowMs === 'function' ? nowMs : (() => Date.now());
    this._getEvidenceSnrRequirement = typeof snrRequirement === 'function'
      ? snrRequirement
      : getEvidenceSnrRequirement;
  }

  clampDecayMs(value) {
    const overrides = {};
    const floor = this.ctx?.config?.tts?.mask_decay_floor_ms;
    if (Number.isFinite(floor)) {
      overrides.min = Math.max(MASK_DECAY_MIN_MS, floor);
    }
    const ceiling = this.ctx?.config?.tts?.mask_decay_ceiling_ms;
    if (Number.isFinite(ceiling)) {
      const min = overrides.min ?? MASK_DECAY_MIN_MS;
      overrides.max = Math.max(min || 0, ceiling);
    }
    return clampMaskDecayMs(value, overrides);
  }

  engage(reason = 'tts', detail = {}) {
    const tag = reason || 'tts';
    const wasActive = this._reasons.size > 0;
    this._reasons.add(tag);
    if (this._engageImpl) {
      try { this._engageImpl(tag, detail); } catch {}
    }
    if (!wasActive) {
      const payload = sanitizeDetail({ reason: tag, ...detail });
      emitFlowBreadcrumb('tts_mask:on', payload);
      emitVoiceEvent('tts_mask_on', payload);
      emitFlowBreadcrumb('mic_gate:engaged', payload);
      emitVoiceEvent('mic_gate_engaged', payload);
    }
  }

  release(reason = 'tts', detail = {}) {
    const tag = reason || 'tts';
    const clearAll = detail && detail.clearAll;
    const hadReasons = this._reasons.size > 0;
    if (clearAll) {
      this._reasons.clear();
    } else {
      this._reasons.delete(tag);
    }
    if (this._releaseImpl) {
      try { this._releaseImpl(tag, detail); } catch {}
    }
    if (hadReasons && this._reasons.size === 0) {
      const payload = sanitizeDetail({ reason: tag, ...detail });
      emitFlowBreadcrumb('tts_mask:off', payload);
      emitVoiceEvent('tts_mask_off', payload);
      emitFlowBreadcrumb('mic_gate:released', payload);
      emitVoiceEvent('mic_gate_released', payload);
    }
  }

  startLogging() {
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    if (!ctx.maskLogTimer) {
      ctx.maskLogTimer = setInterval(() => {
        this._logMaskTick();
      }, MASK_LOG_INTERVAL_MS);
    }
    this._logMaskTick();
  }

  stopLogging() {
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    if (ctx.maskLogTimer) {
      try { clearInterval(ctx.maskLogTimer); } catch {}
    }
    ctx.maskLogTimer = null;
  }

  resolveSnrBoost(baseSnrDb = 3.5) {
    try {
      const requirement = this._getEvidenceSnrRequirement(this.ctx?.state, this._nowMs, baseSnrDb);
      return Math.max(0, requirement - baseSnrDb);
    } catch {
      return 0;
    }
  }

  _logMaskTick() {
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    const tsLocal = this._nowMs();
    if (!ctx.ttsMask?.isMasked(tsLocal)) {
      this.stopLogging();
      return;
    }
    const boost = ctx.ttsMask.snrBoost(tsLocal);
    const decayUntil = ctx.ttsMask.decayUntil();
    const remaining = Number.isFinite(decayUntil)
      ? Math.max(0, Math.round(decayUntil - tsLocal))
      : null;
    this._log('info', '[mask] active', {
      ts_ms: Date.now(),
      session_id: ctx.sessionId || null,
      turn_id: ctx.state?.activeTurnId || null,
      boost_db: Number.isFinite(boost) ? Number.parseFloat(boost.toFixed(2)) : null,
      decay_remaining_ms: remaining,
    });
  }

  _log(level, message, payload) {
    if (!this._voiceLog) {
      return;
    }
    try {
      this._voiceLog(level, message, payload);
    } catch {}
  }
}
