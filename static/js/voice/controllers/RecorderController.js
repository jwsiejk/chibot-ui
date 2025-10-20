import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { force, ...rest } = detail;
  return { ...rest };
}

export default class RecorderController {
  constructor({ ctx, start, stop } = {}) {
    this.ctx = ctx;
    this._startImpl = typeof start === 'function' ? start : null;
    this._stopImpl = typeof stop === 'function' ? stop : null;
    this._active = false;
    this._startToken = 0;
    this._buffering = false;
    this._ready = false;
    this._lastStartAt = 0;
  }

  get active() {
    return this._active;
  }

  get buffering() {
    return this._buffering;
  }

  start(options = {}) {
    const { reason = 'unknown', emit = true, detail = {} } = options;
    const wasActive = this._active;
    const token = ++this._startToken;
    this._lastStartAt = Date.now();
    if (!this._active && this._startImpl) {
      try {
        this._startImpl();
        this._active = true;
      } catch (err) {
        this._active = false;
        throw err;
      }
    }
    this._buffering = true;
    this._ready = false;
    if (emit) {
      const payload = sanitizeDetail({ reason, reused: wasActive, ...detail });
      emitFlowBreadcrumb('recorder:start', payload);
      emitVoiceEvent('recorder_start', payload);
    }
    return { token, reused: wasActive };
  }

  stop(options = {}) {
    const { reason = 'unknown', emit = true, detail = {} } = options;
    const wasActive = this._active;
    if (this._stopImpl) {
      try {
        this._stopImpl();
      } catch {}
    }
    this._active = false;
    this._buffering = false;
    this._ready = false;
    if (emit && wasActive) {
      const payload = sanitizeDetail({ reason, ...detail });
      emitFlowBreadcrumb('recorder:stop', payload);
      emitVoiceEvent('recorder_stop', payload);
    }
    return wasActive;
  }

  notifyAsrReady(detail = {}) {
    if (this._ready) {
      return;
    }
    this._ready = true;
    this._buffering = false;
    const latencyMs = Math.max(0, Date.now() - this._lastStartAt);
    const payload = sanitizeDetail({ ...detail, latency_ms: latencyMs });
    emitFlowBreadcrumb('recorder:buffer_ready', payload);
    emitVoiceEvent('recorder_buffer_ready', payload);
  }

  notifyAsrIdle(detail = {}) {
    this._ready = false;
    this._buffering = false;
    const payload = sanitizeDetail({ ...detail });
    emitFlowBreadcrumb('recorder:idle', payload);
    emitVoiceEvent('recorder_idle', payload);
  }
}
