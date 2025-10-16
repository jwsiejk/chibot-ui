const DEFAULT_CONFIG = {
  barge_in: {
    mode: 'manual_only',
  },
  admin: {
    voice_metrics_panel: true,
  },
  metrics: {
    client_enabled: true,
    server_enabled: true,
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
    w1: 0.5,
    w2: 0.3,
    w3: 0.2,
    threshold: 1.0,
    asrInstantOpen: 0.7,
  },
  shadow: {
    ms: 450,
  },
  commit: {
    min_ms: 500,
    drop_if_no_partial: true,
    no_partial_timeout_ms: 1200,
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
    admin: {
      voice_metrics_panel:
        source.admin?.voice_metrics_panel ?? DEFAULT_CONFIG.admin.voice_metrics_panel,
    },
    metrics: {
      client_enabled:
        source.metrics?.client_enabled ?? DEFAULT_CONFIG.metrics.client_enabled,
      server_enabled:
        source.metrics?.server_enabled ?? DEFAULT_CONFIG.metrics.server_enabled,
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
      w1: source.evidence?.w1 ?? DEFAULT_CONFIG.evidence.w1,
      w2: source.evidence?.w2 ?? DEFAULT_CONFIG.evidence.w2,
      w3: source.evidence?.w3 ?? DEFAULT_CONFIG.evidence.w3,
      threshold: source.evidence?.threshold ?? DEFAULT_CONFIG.evidence.threshold,
      asrInstantOpen:
        source.evidence?.asrInstantOpen ?? DEFAULT_CONFIG.evidence.asrInstantOpen,
    },
    shadow: {
      ms: source.shadow?.ms ?? DEFAULT_CONFIG.shadow.ms,
    },
    commit: {
      min_ms: source.commit?.min_ms ?? DEFAULT_CONFIG.commit.min_ms,
      drop_if_no_partial:
        source.commit?.drop_if_no_partial ?? DEFAULT_CONFIG.commit.drop_if_no_partial,
      no_partial_timeout_ms:
        source.commit?.no_partial_timeout_ms ?? DEFAULT_CONFIG.commit.no_partial_timeout_ms,
    },
    transport: {
      close_on_turn_end:
        source.transport?.close_on_turn_end ?? DEFAULT_CONFIG.transport.close_on_turn_end,
    },
  };
}

export { DEFAULT_CONFIG };
