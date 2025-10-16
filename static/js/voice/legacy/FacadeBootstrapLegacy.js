export function bootstrapLegacyFacade(deps = {}) {
  const {
    registerVoiceLegacyFacade,
    VadFrameUtils,
    state,
    arm,
    bargeIn,
    setGreetGateActive,
    forceBargeInStart,
    forceBargeInEnd,
    legacyOnWsOpenImpl,
    legacyOnWsMessageImpl,
    legacyOnWsCloseImpl,
    legacyOnMicAvailable,
    legacyOnMicStop,
    legacyOnRecorderData,
    legacyOnRecorderError,
    legacyResetEvidenceGate,
    legacyClearSafetyCloseTimer,
    legacyCloseTurnIfOpen,
    legacySendRecorderChunk,
    legacyStopRecorder,
  } = deps;

  if (!registerVoiceLegacyFacade || !VadFrameUtils) {
    throw new Error('bootstrapLegacyFacade missing required dependencies');
  }

  registerVoiceLegacyFacade({
    initMic: (stream = null) => VadFrameUtils.ensureMic(stream),
    armVAD: (stream = null, opts = {}) => arm?.(stream, opts),
    disarmVAD: () => { VadFrameUtils.disarm(); },
    isRecording: () => !!(state?.rec && state.rec.state === 'recording'),
    bargeIn: () => { bargeIn?.(); },
    setVadBoost: (_value) => {},
    setGreetGateActive: (active = true) => { setGreetGateActive?.(!!active); },
    forceBargeInStart: (meta = {}) => forceBargeInStart?.(meta),
    forceBargeInEnd: (opts = {}) => forceBargeInEnd?.(opts),
    onWsOpen: (detail = null) => { legacyOnWsOpenImpl?.(detail); },
    onWsMessage: (detail = {}, helpers = {}) => legacyOnWsMessageImpl?.(detail, helpers),
    onWsClose: (detail = null) => { legacyOnWsCloseImpl?.(detail); },
    onMicAvailable: (detail = {}) => { legacyOnMicAvailable?.(detail); },
    onMicStop: (detail = {}) => legacyOnMicStop?.(detail),
    onRecorderData: (event, helpers = {}) => legacyOnRecorderData?.(event, helpers),
    onRecorderError: (event = null, helpers = {}) => legacyOnRecorderError?.(event, helpers),
  });

  return {
    resetEvidenceGate: legacyResetEvidenceGate,
    clearSafetyCloseTimer: legacyClearSafetyCloseTimer,
    closeTurnIfOpen: legacyCloseTurnIfOpen,
    sendRecorderChunk: legacySendRecorderChunk,
    stopRecorder: legacyStopRecorder,
  };
}

