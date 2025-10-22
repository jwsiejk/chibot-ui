(() => {
  const initialState = {
    connectionState: "disconnected",
    accessToken: null,
    sid: null,
    resumeToken: null,
    resumeTtlMs: null,
    resumeExpiresAt: null,
    latencyMs: null,
    lastPingAt: null,
    heartbeatTimerId: null,
    websocket: null,
    infoFrame: null
  };

  let state = { ...initialState };
  const listeners = new Set();

  function snapshot() {
    return { ...state };
  }

  function notify() {
    const current = snapshot();
    listeners.forEach((listener) => {
      try {
        listener(current);
      } catch (err) {
        console.error("AppState subscriber error", err);
      }
    });
  }

  function setState(patch) {
    if (!patch || typeof patch !== "object") return;
    state = { ...state, ...patch };
    notify();
  }

  function reset() {
    state = { ...initialState };
    notify();
  }

  function subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("listener must be a function");
    }
    listeners.add(listener);
    listener(snapshot());
    return () => {
      listeners.delete(listener);
    };
  }

  window.AppState = {
    getState: snapshot,
    setState,
    reset,
    subscribe,
    get initialState() {
      return { ...initialState };
    }
  };
})();
