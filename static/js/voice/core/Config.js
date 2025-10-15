const DEFAULT_CONFIG = {
  barge_in: {
    mode: 'manual_only',
  },
  vad: {
    enabled: true,
    hysteresis: {
      enter: 3,
      exit: 4,
    },
  },
  tts: {
    mask_auto_vad: true,
    decay_ms: 700,
  },
  evidence: {
    snr_sigma: 2.5,
    asr_conf: 0.65,
  },
  shadow: {
    ms: 450,
  },
  commit: {
    min_ms: 500,
    drop_if_no_partial: true,
  },
  transport: {
    close_on_turn_end: false,
  },
};

export function getConfig() {
  const source = (typeof window !== 'undefined' && window.__askchip_config) || {};

  return {
    barge_in: {
      mode: source.barge_in?.mode ?? DEFAULT_CONFIG.barge_in.mode,
    },
    vad: {
      enabled: source.vad?.enabled ?? DEFAULT_CONFIG.vad.enabled,
      hysteresis: {
        enter: source.vad?.hysteresis?.enter ?? DEFAULT_CONFIG.vad.hysteresis.enter,
        exit: source.vad?.hysteresis?.exit ?? DEFAULT_CONFIG.vad.hysteresis.exit,
      },
    },
    tts: {
      mask_auto_vad: source.tts?.mask_auto_vad ?? DEFAULT_CONFIG.tts.mask_auto_vad,
      decay_ms: source.tts?.decay_ms ?? DEFAULT_CONFIG.tts.decay_ms,
    },
    evidence: {
      snr_sigma: source.evidence?.snr_sigma ?? DEFAULT_CONFIG.evidence.snr_sigma,
      asr_conf: source.evidence?.asr_conf ?? DEFAULT_CONFIG.evidence.asr_conf,
    },
    shadow: {
      ms: source.shadow?.ms ?? DEFAULT_CONFIG.shadow.ms,
    },
    commit: {
      min_ms: source.commit?.min_ms ?? DEFAULT_CONFIG.commit.min_ms,
      drop_if_no_partial:
        source.commit?.drop_if_no_partial ?? DEFAULT_CONFIG.commit.drop_if_no_partial,
    },
    transport: {
      close_on_turn_end:
        source.transport?.close_on_turn_end ?? DEFAULT_CONFIG.transport.close_on_turn_end,
    },
  };
}

export { DEFAULT_CONFIG };
