// Moved from AdaptiveRuntime on 2025-10-20. No behavior change.
import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';
import { openWS, waitWSOpen } from '../../ws_module.js';

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { emit, force, ...rest } = detail;
  return { ...rest };
}

export default class AsrController {
  constructor({ ctx } = {}) {
    this.ctx = ctx;
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
    const transportPromise = Promise.resolve(this.ensureTransport());
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
    try { this.teardownTransport(); } catch {}
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

  async ensureTransport() {
    const ctx = this.ctx;
    if (!ctx) {
      return null;
    }
    const transport = ctx.transport || {};
    if (transport.connected) {
      return transport.wsPromise;
    }
    if (!transport.wsPromise) {
      transport.wsPromise = openWS();
    }
    try {
      const wsHandle = await waitWSOpen();
      transport.connected = true;
      if (ctx.state) {
        ctx.state.wsReady = true;
      }
      return wsHandle;
    } catch (err) {
      transport.wsPromise = null;
      transport.connected = false;
      if (ctx.state) {
        ctx.state.wsReady = false;
      }
      throw err;
    }
  }

  teardownTransport() {
    const ctx = this.ctx;
    if (!ctx) {
      return;
    }
    const transport = ctx.transport || {};
    transport.connected = false;
    if (ctx.state) {
      ctx.state.wsReady = false;
    }
    transport.wsPromise = null;
    if (transport.safetyTimer) {
      try { clearTimeout(transport.safetyTimer); } catch {}
      transport.safetyTimer = null;
    }
  }
}
