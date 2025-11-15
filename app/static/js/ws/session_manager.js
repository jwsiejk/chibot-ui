// app/static/js/ws/session_manager.js
// Encapsulates WS session lifecycle, resume, and rate-limit retry logic.

export function createSessionManager({
  AppState,
  connection,                // createWsConnection(...)
  captureRuntime,            // createCaptureRuntime(...) for stopRecorder/evaluateStopRecorderReason
  bannerClient,              // createBannerClient(...)
  logStage,                  // telemetry logStage
  recordClientBannerEvent,   // telemetry banner helper
  hubLog,                    // hub logging helper
}) {
  function makeWsUrl(options = {}) {
    // real implementation will be moved from ws_client.js
    return "";
  }

  function computeUrl(options = {}) {
    // real implementation will be moved from ws_client.js
    return makeWsUrl(options);
  }

  function getResumeState() {
    // real implementation will be moved from ws_client.js
    return null;
  }

  function assignResume(state) {
    // real implementation will be moved from ws_client.js
  }

  function clearResumeState() {
    // real implementation will be moved from ws_client.js
  }

  function attemptAutoResume() {
    // real implementation will be moved from ws_client.js
  }

  function trackTokenFromUrl(url) {
    // real implementation will be moved from ws_client.js
  }

  function maybeShowHandshakeToast(infoFrame) {
    // real implementation will be moved from ws_client.js
  }

  function resetRateLimitRecovery() {
    // real implementation will be moved from ws_client.js
  }

  function scheduleRateLimitRetry(infoFrame) {
    // real implementation will be moved from ws_client.js
  }

  async function open(options = {}, protocols) {
    // real implementation will be moved from ws_client.js
    return null;
  }

  async function close(reason = "client_closed") {
    // real implementation will be moved from ws_client.js
  }

  return {
    makeWsUrl,
    computeUrl,
    getResumeState,
    assignResume,
    clearResumeState,
    attemptAutoResume,
    trackTokenFromUrl,
    maybeShowHandshakeToast,
    resetRateLimitRecovery,
    scheduleRateLimitRetry,
    open,
    close,
  };
}
