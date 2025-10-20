// Moved from AdaptiveRuntime on 2025-10-20. No behavior change.
import { emitFlowBreadcrumb } from '../../flow_breadcrumbs.js';
import { emitVoiceEvent } from '../ui/Events.js';
import { bufferPreRollFrame } from '../core/index.js';
import { sendAudioChunk } from '../../ws_module.js';

const DEFAULT_PRE_ROLL_MS = 0;
const DEFAULT_MIN_VALID_BLOB_BYTES = 1;

function sanitizeDetail(detail = {}) {
  if (!detail || typeof detail !== 'object') {
    return {};
  }
  const { force, ...rest } = detail;
  return { ...rest };
}

export default class RecorderController {
  constructor({
    ctx,
    sendChunk,
    logPreCommitMode,
    voiceLog,
    constants = {},
  } = {}) {
    this.ctx = ctx;
    this._sendChunk = typeof sendChunk === 'function' ? sendChunk : null;
    this._logPreCommitMode = typeof logPreCommitMode === 'function' ? logPreCommitMode : null;
    this._voiceLog = typeof voiceLog === 'function' ? voiceLog : null;
    this._constants = {
      PRE_ROLL_MS: Number.isFinite(constants.PRE_ROLL_MS) ? constants.PRE_ROLL_MS : DEFAULT_PRE_ROLL_MS,
      MIN_VALID_BLOB_BYTES: Number.isFinite(constants.MIN_VALID_BLOB_BYTES)
        ? constants.MIN_VALID_BLOB_BYTES
        : DEFAULT_MIN_VALID_BLOB_BYTES,
    };
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
    if (!this._active) {
      try {
        this._startMediaRecorder();
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
    this._stopMediaRecorder();
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

  _startMediaRecorder() {
    const ctx = this.ctx;
    if (!ctx || !ctx.audio) {
      return;
    }
    const { audio } = ctx;
    if (audio.recorder) {
      return;
    }
    const recorderStream = audio.encoderStream || audio.stream;
    if (!recorderStream) {
      return;
    }
    let mimeType = 'audio/webm; codecs=opus';
    try {
      if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported
        && MediaRecorder.isTypeSupported('audio/ogg; codecs=opus')) {
        mimeType = 'audio/ogg; codecs=opus';
      }
    } catch {}
    const recorder = new MediaRecorder(recorderStream, { mimeType });
    recorder.addEventListener('dataavailable', (event) => {
      this._handleRecorderData(event);
    });
    recorder.addEventListener('error', (event) => {
      const detail = { message: event?.error?.message || 'unknown' };
      emitVoiceEvent('recorder_error', detail);
      this.stop({ reason: 'recorder_error', detail });
    });
    recorder.start(audio.recTimeslice);
    this._log('info', '[recorder] started', {
      ts_ms: Date.now(),
      session_id: ctx.sessionId || null,
      mime_type: mimeType,
      timeslice_ms: audio.recTimeslice,
      source: audio.encoderDestination ? 'processor' : 'raw_stream',
    });
    audio.recorder = recorder;
    if (ctx.state) {
      ctx.state.recording = true;
    }
  }

  _stopMediaRecorder() {
    const ctx = this.ctx;
    if (!ctx || !ctx.audio) {
      return;
    }
    const { audio } = ctx;
    const { recorder } = audio;
    if (!recorder) {
      return;
    }
    try {
      if (recorder.state !== 'inactive') {
        recorder.stop();
      }
    } catch {}
    audio.recorder = null;
    if (ctx.state) {
      ctx.state.recording = false;
    }
  }

  _handleRecorderData(event) {
    const ctx = this.ctx;
    if (!ctx || !ctx.audio || !ctx.state) {
      return;
    }
    const blob = event?.data;
    if (!blob || typeof blob.size !== 'number'
      || blob.size < this._constants.MIN_VALID_BLOB_BYTES) {
      return;
    }
    const timecode = Number.isFinite(event?.timecode) ? event.timecode : null;
    const { durationMs, nextTimecode } = bufferPreRollFrame({
      shadowBuffer: ctx.shadowBuffer,
      blob,
      timecode,
      timeslice: ctx.audio.recTimeslice,
      fallbackMs: this._constants.PRE_ROLL_MS,
      lastTimecode: ctx.audio.lastTimecode,
      onBuffered: ({ durationMs: dur, byteLength }) => {
        try {
          ctx.evidenceGate.extendBuffer({ durationMs: dur, bytes: byteLength });
        } catch {}
      },
    });
    ctx.audio.lastTimecode = nextTimecode;
    const feedMode = ctx.state.turnOpen
      ? 'streaming'
      : (ctx.state.preCommitASRFeed ? 'asr_priming' : 'shadow_only');
    if (this._logPreCommitMode) {
      this._logPreCommitMode(feedMode, {
        source: ctx.state.turnOpen ? 'turn_stream' : 'precommit_buffer',
        chunk_bytes: blob.size,
        duration_ms: durationMs,
        timecode,
      });
    }
    if (!ctx.state.turnOpen) {
      if (ctx.state.preCommitASRFeed) {
        try {
          const maybePromise = sendAudioChunk(blob);
          if (maybePromise && typeof maybePromise.catch === 'function') {
            maybePromise.catch(() => {});
          }
        } catch {}
      }
      return;
    }
    if (this._sendChunk) {
      this._sendChunk(blob, { durationMs });
    }
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
