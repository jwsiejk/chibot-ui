(() => {
  const initialState = {
    connectionState: "disconnected",
    sid: null,
    resume: null,
    resumeError: null,
    latencyMs: null,
    lastPingAt: null,
    heartbeatTimerId: null,
    websocket: null,
    infoFrame: null,
    serverBanner: null,
    clientBanner: {
      info: null,
      events: []
    }
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
    const sanitized = { ...patch };
    if ("resume" in sanitized) {
      delete sanitized.resume;
    }
    if ("resumeError" in sanitized) {
      delete sanitized.resumeError;
    }
    state = { ...state, ...sanitized, resume: null, resumeError: null };
    notify();
  }

  function setResume() {
    clearResume();
    return null;
  }

  function clearResume() {
    if (state.resume !== null || state.resumeError !== null) {
      state = { ...state, resume: null, resumeError: null };
      notify();
    }
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
    setResume,
    clearResume,
    get initialState() {
      return { ...initialState };
    }
  };
})();
