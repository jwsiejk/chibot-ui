import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { clearAll, force, ...rest } = detail;
  return { ...rest };
}

export default class TtsMaskController {
  constructor({ ctx, engage, release } = {}) {
    this.ctx = ctx;
    this._engageImpl = typeof engage === 'function' ? engage : null;
    this._releaseImpl = typeof release === 'function' ? release : null;
    this._reasons = new Set();
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
}
