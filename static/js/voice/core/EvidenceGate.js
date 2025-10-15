const clampPositive = (value, fallback) => {
  if (!Number.isFinite(value) || value < 0) return fallback;
  return value;
};

const DEFAULT_BASE_SNR_DB = 3.5;

export class EvidenceGate {
  constructor({ snrSigma = 2.5, asrConf = 0.65, baseSnrDb = DEFAULT_BASE_SNR_DB } = {}) {
    this.config = {
      snrSigma: Number.isFinite(snrSigma) ? snrSigma : 2.5,
      asrConf: Number.isFinite(asrConf) ? asrConf : 0.65,
      baseSnrDb: Number.isFinite(baseSnrDb) ? baseSnrDb : DEFAULT_BASE_SNR_DB,
    };
    this.reset();
  }

  reset(reason = null) {
    this.active = false;
    this.satisfied = false;
    this.aborted = false;
    this.startedAt = 0;
    this.lastDetail = null;
    this.bufferedMs = 0;
    this.bufferedBytes = 0;
    this.minMassOk = false;
    this.snrOk = false;
    this.partialConfidence = null;
    this.partialRising = false;
    this.partialGateOk = false;
    this.reasonValue = reason || null;
  }

  reason() {
    return this.reasonValue;
  }

  start({ startedAt = 0, detail = null, bufferedMs = 0, bufferedBytes = 0 } = {}) {
    this.active = true;
    this.satisfied = false;
    this.aborted = false;
    this.startedAt = Number.isFinite(startedAt) ? startedAt : 0;
    this.lastDetail = detail || null;
    this.bufferedMs = clampPositive(bufferedMs, 0);
    this.bufferedBytes = clampPositive(bufferedBytes, 0);
    this.minMassOk = false;
    this.snrOk = false;
    this.partialConfidence = null;
    this.partialRising = false;
    this.partialGateOk = false;
    this.reasonValue = null;
  }

  setDetail(detail) {
    this.lastDetail = detail || this.lastDetail;
  }

  setBufferStats({ bufferedMs, bufferedBytes } = {}) {
    if (Number.isFinite(bufferedMs)) {
      this.bufferedMs = Math.max(0, bufferedMs);
    }
    if (Number.isFinite(bufferedBytes)) {
      this.bufferedBytes = Math.max(0, bufferedBytes);
    }
  }

  extendBuffer({ durationMs, bytes, bufferedMs, bufferedBytes } = {}) {
    if (!this.active || this.satisfied || this.aborted) return;
    if (Number.isFinite(durationMs)) {
      this.bufferedMs = Math.max(this.bufferedMs, durationMs);
    }
    if (Number.isFinite(bytes)) {
      this.bufferedBytes = Math.max(this.bufferedBytes, bytes);
    }
    this.setBufferStats({ bufferedMs, bufferedBytes });
  }

  update({
    vadState,
    snr,
    asrCue,
    snrBoost = 0,
    bufferedMs,
    bufferedBytes,
    minSpeechMs,
    minBytes,
  } = {}) {
    if (!this.active || this.satisfied || this.aborted) {
      return { shouldCommit: false, state: 'idle' };
    }

    this.setBufferStats({ bufferedMs, bufferedBytes });

    const minMs = Number.isFinite(minSpeechMs) ? Math.max(0, minSpeechMs) : 0;
    const minB = Number.isFinite(minBytes) ? Math.max(0, minBytes) : 0;
    this.minMassOk = this.bufferedMs >= minMs && this.bufferedBytes >= minB;

    const requiredSnr = (this.config.baseSnrDb || DEFAULT_BASE_SNR_DB) + Math.max(0, snrBoost);
    this.snrOk = Number.isFinite(snr) ? snr >= requiredSnr : false;

    if (asrCue && asrCue.type === 'partial') {
      const confidence = Number.isFinite(asrCue.conf) ? asrCue.conf : null;
      const threshold = Number.isFinite(asrCue.threshold)
        ? asrCue.threshold
        : (this.config.asrConf ?? 0.65);
      const delta = Number.isFinite(asrCue.delta) ? asrCue.delta : 0.05;
      if (confidence !== null) {
        if (this.partialConfidence === null || !Number.isFinite(this.partialConfidence)) {
          this.partialConfidence = confidence;
        } else if (confidence > this.partialConfidence + delta) {
          this.partialRising = true;
          this.partialConfidence = confidence;
        } else {
          this.partialConfidence = Math.max(this.partialConfidence, confidence);
        }
        if (confidence >= threshold) {
          this.partialGateOk = true;
        } else if (this.partialRising && confidence >= threshold - delta) {
          this.partialGateOk = true;
        }
      }
      if (typeof asrCue.transcript === 'string' && asrCue.transcript.trim().length > 2) {
        this.partialGateOk = this.partialGateOk || this.partialRising;
      }
    }

    if (asrCue && asrCue.type === 'vad_end' && !this.minMassOk) {
      this.reasonValue = 'vad_end';
    }

    const shouldCommit = this.shouldCommit();
    if (shouldCommit && !this.reasonValue) {
      this.reasonValue = vadState || 'commit';
    }
    return {
      shouldCommit,
      state: shouldCommit ? 'commit' : 'hold',
    };
  }

  shouldCommit() {
    return this.snrOk && (this.minMassOk || this.partialGateOk);
  }

  satisfy(reason = 'committed') {
    if (!this.active) return;
    this.satisfied = true;
    this.active = false;
    this.reasonValue = reason || this.reasonValue || 'committed';
  }

  abort(reason = 'aborted', detail = null, stats = {}) {
    if (!this.active || this.satisfied) return;
    this.aborted = true;
    this.active = false;
    this.reasonValue = reason || 'aborted';
    this.lastDetail = detail || this.lastDetail;
    if (Number.isFinite(stats.durationMs)) {
      this.bufferedMs = Math.max(this.bufferedMs, stats.durationMs);
    }
    if (Number.isFinite(stats.totalBytes)) {
      this.bufferedBytes = Math.max(this.bufferedBytes, stats.totalBytes);
    }
  }

  isOpen() {
    return this.active && !this.satisfied && !this.aborted;
  }
}
