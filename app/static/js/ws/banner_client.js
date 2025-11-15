// app/static/js/ws/banner_client.js
// Encapsulates client banner state, toast UI, and environment/sanitize helpers.

export function createBannerClient({ AppState, updateState, getSocket, sendJson }) {
  // getSocket: () => underlying WebSocket (from ws_client.js)
  // sendJson: (frame) => boolean, same semantics as ws_client sendJson

  const CLIENT_BANNER_TYPE = "client.banner";
  const CLIENT_BANNER_MAX_HISTORY = 24;
  const CLIENT_BANNER_MAX_QUEUE = 24;
  const CLIENT_BANNER_EVENT_LABEL_MAX = 64;
  const CLIENT_BANNER_STRING_MAX = 240;

  let clientBannerQueue = [];
  let toastRoot = null;

  function ensureClientBannerState() {
    return AppState?.clientBanner || { info: {}, events: [] };
  }

  function updateClientBannerState(info, events) {
    const state = { info, events };
    updateState({ clientBanner: state });
    return state;
  }

  function queueClientBannerPayload(payload) {
    // real implementation will be moved from ws_client.js
  }

  function flushClientBannerQueue() {
    // real implementation will be moved from ws_client.js
  }

  function ensureToastRoot() {
    // real implementation will be moved from ws_client.js
    return null;
  }

  function showConnectionToast(message) {
    // real implementation will be moved from ws_client.js
  }

  function truncateBannerString(value, max) {
    // real implementation will be moved from ws_client.js
    return typeof value === "string" ? value : "";
  }

  function sanitizeBannerValue(value, depth = 0) {
    // real implementation will be moved from ws_client.js
    return value;
  }

  function sanitizeUrlForBanner(url) {
    // real implementation will be moved from ws_client.js
    return null;
  }

  function collectClientBannerInfo() {
    // real implementation will be moved from ws_client.js
    return {};
  }

  return {
    CLIENT_BANNER_TYPE,
    CLIENT_BANNER_MAX_HISTORY,
    CLIENT_BANNER_MAX_QUEUE,
    CLIENT_BANNER_EVENT_LABEL_MAX,
    CLIENT_BANNER_STRING_MAX,
    ensureClientBannerState,
    updateClientBannerState,
    queueClientBannerPayload,
    flushClientBannerQueue,
    ensureToastRoot,
    showConnectionToast,
    truncateBannerString,
    sanitizeBannerValue,
    sanitizeUrlForBanner,
    collectClientBannerInfo,
  };
}
