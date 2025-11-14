// app/static/js/ws/policy_runtime.js
// Encapsulates client-side policy merging and access helpers for ws_client.js.

export function createPolicyRuntime(AppState) {
  // Private policy state will live here (normalized policy, defaults, etc.).
  // For now, just stub helpers with safe behavior.

  function getCurrentPolicy() {
    // Return the raw AppState.policy or a minimal safe object for now.
    return AppState?.policy || {};
  }

  function applyPolicySnapshotFromSource(source, reason) {
    // stub – real implementation will be moved from ws_client.js
    return getCurrentPolicy();
  }

  function installClientVadPolicySnapshot(snapshot) {
    // stub – real implementation will be moved from ws_client.js
  }

  function shouldAutoRearmAfterClosed(reason) {
    // stub – safe default: do not auto-rearm unless policy says so
    return false;
  }

  function getClientVadPolicyRoot() {
    // stub – VAD policy root; will come from DEFAULT_POLICY + AppState
    return {};
  }

  return {
    getCurrentPolicy,
    applyPolicySnapshotFromSource,
    installClientVadPolicySnapshot,
    shouldAutoRearmAfterClosed,
    getClientVadPolicyRoot,
  };
}
