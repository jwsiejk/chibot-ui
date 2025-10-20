import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { emit, force, ...rest } = detail;
  return { ...rest };
}

export default class AsrController {
  constructor({ ctx, ensureTransport, stopTransport } = {}) {
    this.ctx = ctx;
    this._ensureTransport = typeof ensureTransport === 'function' ? ensureTransport : null;
    this._stopTransport = typeof stopTransport === 'function' ? stopTransport : null;
    this._readyCallbacks = new Set();
    this._stopCallbacks = new Set();
    this._active = false;
    this._ready = false;
    this._startToken = 0;
    this._lastStartAt = 0;
    this._lastReason = 'unknown';
  }

  get active() {
    return this._active;
  }

  get ready() {
    return this._ready;
  }

  async ensureStarted(options = {}) {
    const { reason = 'unknown', emit = true, detail = {} } = options;
    const token = ++this._startToken;
    const reused = this._active && this._ready;
    this._active = true;
    this._ready = false;
    this._lastStartAt = Date.now();
    this._lastReason = reason;
    if (emit) {
      const payload = sanitizeDetail({ reason, reused, ...detail });
      emitFlowBreadcrumb('asr:start', payload);
      emitVoiceEvent('asr_start', payload);
    }
    const transportPromise = this._ensureTransport ? Promise.resolve(this._ensureTransport()) : Promise.resolve();
    transportPromise
      .then(() => {
        if (this._startToken === token) {
          this._handleReady({ reason, reused, emit, detail });
        }
      })
      .catch(() => {});
    return transportPromise;
  }

  _handleReady(meta = {}) {
    const { reason = 'unknown', reused = false, emit = true, detail = {} } = meta;
    this._ready = true;
    const latencyMs = Math.max(0, Date.now() - this._lastStartAt);
    const payload = sanitizeDetail({ reason, reused, latency_ms: latencyMs, ...detail });
    if (emit) {
      emitFlowBreadcrumb('asr:ready', payload);
      emitVoiceEvent('asr_ready', payload);
    }
    for (const cb of this._readyCallbacks) {
      try { cb(payload); } catch {}
    }
  }

  onReady(cb) {
    if (typeof cb !== 'function') {
      return () => {};
    }
    this._readyCallbacks.add(cb);
    if (this._ready) {
      try {
        cb({ reason: this._lastReason, reused: true, latency_ms: Math.max(0, Date.now() - this._lastStartAt) });
      } catch {}
    }
    return () => {
      this._readyCallbacks.delete(cb);
    };
  }

  onStop(cb) {
    if (typeof cb !== 'function') {
      return () => {};
    }
    this._stopCallbacks.add(cb);
    return () => {
      this._stopCallbacks.delete(cb);
    };
  }

  notifyPartial(detail = {}) {
    const payload = sanitizeDetail(detail);
    emitFlowBreadcrumb('asr:partial', payload);
    emitVoiceEvent('asr_partial', payload);
  }

  notifyFinal(detail = {}) {
    const payload = sanitizeDetail(detail);
    emitFlowBreadcrumb('asr:final', payload);
    emitVoiceEvent('asr_final', payload);
  }

  notifyStop(detail = {}) {
    const payload = sanitizeDetail(detail);
    this._active = false;
    this._ready = false;
    emitFlowBreadcrumb('asr:stop', payload);
    emitVoiceEvent('asr_stop', payload);
    for (const cb of this._stopCallbacks) {
      try { cb(payload); } catch {}
    }
  }

  stopIfIdle(options = {}) {
    const { reason = 'idle', emit = true, detail = {} } = options;
    const ctxState = this.ctx?.state || {};
    if (ctxState.turnOpen || ctxState.recording) {
      return false;
    }
    if (this._stopTransport) {
      try { this._stopTransport(); } catch {}
    }
    const alreadyIdle = !this._active && !this._ready;
    if (emit) {
      if (!alreadyIdle) {
        this.notifyStop({ reason, idle: true, ...detail });
      }
    } else if (!alreadyIdle) {
      this._active = false;
      this._ready = false;
      const payload = sanitizeDetail({ reason, idle: true, ...detail });
      for (const cb of this._stopCallbacks) {
        try { cb(payload); } catch {}
      }
    }
    return true;
  }
}
