// app/static/js/audio/capture_runtime.js
// Encapsulates VAD+AppState bridge, consoleBus publishing, silence timers,
// and recorder start/stop lifecycle for AskChip.

export function createCaptureRuntime({
  AppState,
  policyRuntime,          // createPolicyRuntime(AppState)
  audioRuntime,           // createWsAudioRuntime(...)
  initVAD,                // existing initVAD(...) function
  consoleBus,             // existing console event bus
  hubLog,                 // logging helper
  logStage,               // telemetry logStage from ws/telemetry.js
  recordClientBannerEvent // telemetry banner from ws/telemetry.js
}) {
  // ===== Internal VAD state =====
  let vadController = null;
  let vadSilenceTimerId = null;

  // ===== VAD API =====
  function getVadController() {
    return vadController;
  }

  function setVadAppState(partial) {
    // real implementation will be moved from ws_client.js
  }

  function publishVad(event) {
    // real implementation will be moved from ws_client.js
  }

  function handleVadGateChange(state) {
    // real implementation will be moved from ws_client.js
  }

  function getClientVadPolicyConfig() {
    // real implementation will be moved from ws_client.js
    return {};
  }

  function getVadSilenceTimeoutMs() {
    // real implementation will be moved from ws_client.js
    return 0;
  }

  function scheduleVadSilenceTimer(reason) {
    // real implementation will be moved from ws_client.js
  }

  function clearVadSilenceTimer() {
    // real implementation will be moved from ws_client.js
  }

  function initClientVad() {
    // real implementation will call initVAD({ getPolicy, getTtsActive, onGateChange, setAppState, publish })
  }

  // ===== Recorder lifecycle =====

  function evaluateStopRecorderReason(reason) {
    // real implementation will be moved from ws_client.js
    return "unknown";
  }

  function setAppStateValue(key, value) {
    // real implementation will be moved from ws_client.js
  }

  function setListeningState(listening) {
    // real implementation will be moved from ws_client.js
  }

  function setAsrArmInFlight(inFlight) {
    // real implementation will be moved from ws_client.js
  }

  function setWsConnected(connected) {
    // real implementation will be moved from ws_client.js
  }

  function setWsPhase(phase) {
    // real implementation will be moved from ws_client.js
  }

  function resetRecorderTelemetry() {
    // real implementation will be moved from ws_client.js
  }

  async function performStopRecorder(reason) {
    // real implementation will be moved from ws_client.js
  }

  async function stopRecorder(reason) {
    // real implementation will be moved from ws_client.js
  }

  async function startRecorderStreaming(opts = {}) {
    // real implementation will be moved from ws_client.js
  }

  return {
    // VAD
    getVadController,
    setVadAppState,
    publishVad,
    handleVadGateChange,
    getClientVadPolicyConfig,
    getVadSilenceTimeoutMs,
    scheduleVadSilenceTimer,
    clearVadSilenceTimer,
    initClientVad,
    // Recorder
    evaluateStopRecorderReason,
    setAppStateValue,
    setListeningState,
    setAsrArmInFlight,
    setWsConnected,
    setWsPhase,
    resetRecorderTelemetry,
    performStopRecorder,
    stopRecorder,
    startRecorderStreaming,
  };
}
