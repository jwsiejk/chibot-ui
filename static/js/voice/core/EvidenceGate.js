import { getConfig } from './Config.js';

const clampPositive = (value, fallback) => {
  if (!Number.isFinite(value) || value < 0) return fallback;
  return value;
};

const DEFAULT_BASE_SNR_DB = 3.5;
const DEFAULT_SPEECH_WINDOW_FRAMES = 24;
const DEFAULT_EVIDENCE_PARAMS = {
  w1: 0.5,
  w2: 0.3,
  w3: 0.2,
  threshold: 1.0,
  asrInstantOpen: 0.7,
};

const clampFinite = (value, fallback) => (Number.isFinite(value) ? value : fallback);

const resolveEvidenceConfig = (overrides = {}) => {
  const configEvidence = getConfig()?.evidence ?? {};
  return {
    w1: clampFinite(overrides.w1, clampFinite(configEvidence.w1, DEFAULT_EVIDENCE_PARAMS.w1)),
    w2: clampFinite(overrides.w2, clampFinite(configEvidence.w2, DEFAULT_EVIDENCE_PARAMS.w2)),
    w3: clampFinite(overrides.w3, clampFinite(configEvidence.w3, DEFAULT_EVIDENCE_PARAMS.w3)),
    threshold: clampFinite(
      overrides.threshold,
      clampFinite(configEvidence.threshold, DEFAULT_EVIDENCE_PARAMS.threshold),
    ),
    asrInstantOpen: clampFinite(
      overrides.asrInstantOpen,
      clampFinite(configEvidence.asrInstantOpen, DEFAULT_EVIDENCE_PARAMS.asrInstantOpen),
    ),
    windowFrames: clampFinite(
      overrides.windowFrames,
      clampFinite(configEvidence.windowFrames, DEFAULT_SPEECH_WINDOW_FRAMES),
    ),
  };
};

const normalizeSnr = (snr) => {
  const value = Number.isFinite(snr) ? Math.max(0, snr) : 0;
  if (value === 0) {
    return 0;
  }
  return value / (value + 1);
};

export class EvidenceGate {
  constructor({
    snrSigma = 2.5,
    asrConf = 0.65,
    baseSnrDb = DEFAULT_BASE_SNR_DB,
    evidence: evidenceOverrides = {},
  } = {}) {
    this.config = {
      snrSigma: Number.isFinite(snrSigma) ? snrSigma : 2.5,
      asrConf: Number.isFinite(asrConf) ? asrConf : 0.65,
      baseSnrDb: Number.isFinite(baseSnrDb) ? baseSnrDb : DEFAULT_BASE_SNR_DB,
    };
    const resolvedEvidence = resolveEvidenceConfig(evidenceOverrides);
    const windowFrames = clampPositive(resolvedEvidence.windowFrames, DEFAULT_SPEECH_WINDOW_FRAMES);
    this.evidence = {
      w1: resolvedEvidence.w1,
      w2: resolvedEvidence.w2,
      w3: resolvedEvidence.w3,
      threshold: resolvedEvidence.threshold,
      asrInstantOpen: resolvedEvidence.asrInstantOpen,
    };
    this.speechWindowMax = Math.max(5, Math.min(30, Math.round(windowFrames))) || DEFAULT_SPEECH_WINDOW_FRAMES;
    this.speechWindowMin = Math.max(5, Math.round(this.speechWindowMax * 0.5));
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
    this.evidenceScore = 0;
    this.instantGateOpen = false;
    this.voicedFrameRatio = 0;
    this.voicedFrameRatioForScore = 0;
    this._resetSpeechWindow();
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
    this.evidenceScore = 0;
    this.instantGateOpen = false;
    this.voicedFrameRatio = 0;
    this.voicedFrameRatioForScore = 0;
    this._resetSpeechWindow();
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

  _resetSpeechWindow() {
    this.speechWindow = [];
    this.speechWindowVoiced = 0;
  }

  _ingestVadState(vadState) {
    if (!this.speechWindow || !Array.isArray(this.speechWindow)) {
      this._resetSpeechWindow();
    }
    if (vadState !== 'speech' && vadState !== 'silence') {
      return;
    }
    const isSpeech = vadState === 'speech';
    this.speechWindow.push(isSpeech);
    if (isSpeech) {
      this.speechWindowVoiced += 1;
    }
    while (this.speechWindow.length > this.speechWindowMax) {
      const removed = this.speechWindow.shift();
      if (removed) {
        this.speechWindowVoiced = Math.max(0, this.speechWindowVoiced - 1);
      }
    }
    const total = this.speechWindow.length;
    if (total > 0) {
      this.voicedFrameRatio = this.speechWindowVoiced / total;
      const progress = Math.min(1, total / this.speechWindowMin);
      this.voicedFrameRatioForScore = this.voicedFrameRatio * progress;
    } else {
      this.voicedFrameRatio = 0;
      this.voicedFrameRatioForScore = 0;
    }
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
    this._ingestVadState(vadState);

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

    if (asrCue && Number.isFinite(asrCue.conf) && asrCue.type !== 'partial') {
      this.partialConfidence = asrCue.conf;
    }

    if (asrCue && asrCue.type === 'vad_end' && !this.minMassOk) {
      this.reasonValue = 'vad_end';
    }

    const snrComponent = normalizeSnr(snr);
    const asrConfidence = Number.isFinite(this.partialConfidence) ? this.partialConfidence : 0;
    this.instantGateOpen = asrConfidence >= this.evidence.asrInstantOpen;
    const ratioComponent = this.voicedFrameRatioForScore;
    this.evidenceScore = (this.evidence.w1 * snrComponent)
      + (this.evidence.w2 * ratioComponent)
      + (this.evidence.w3 * asrConfidence);
    this.partialGateOk = this.instantGateOpen || this.evidenceScore >= this.evidence.threshold;

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
    if (this.instantGateOpen) {
      return true;
    }
    const hasMass = this.minMassOk || this.voicedFrameRatio >= 0.5;
    return hasMass && this.evidenceScore >= this.evidence.threshold;
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
