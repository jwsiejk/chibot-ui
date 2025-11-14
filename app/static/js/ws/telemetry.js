// Thin telemetry helpers used by ws_client.js

export const MIC_OUTCOME = {
  UNKNOWN: "UNKNOWN",
  SUCCESS: "SUCCESS",
  FAILURE: "FAILURE",
  CANCELLED: "CANCELLED",
};

export function logMic(detail) {
  // stub – real implementation will be moved from ws_client.js
}

export function emitMicBreadcrumb(detail) {
  // stub
}

export function normalizeErrorDetail(detail) {
  // stub – should still return something safe
  return detail || {};
}

export function recordLastError(code, reason) {
  // stub
}

export function recordClientBannerEvent(event, detail) {
  // stub
}

export function logStage(label, detail) {
  // stub
}
