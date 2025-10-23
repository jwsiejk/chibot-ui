(() => {
  const initialState = {
    connectionState: "disconnected",
    accessToken: null,
    sid: null,
    resume: null,
    resumeError: null,
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

  function setResume(token, ttlMs) {
    const resumeToken = typeof token === "string" && token.trim() ? token.trim() : null;
    const ttl = Number(ttlMs);
    if (!resumeToken || !Number.isFinite(ttl) || ttl <= 0) {
      clearResume();
      return null;
    }
    const expiresAt = Date.now() + ttl;
    const resume = { token: resumeToken, ttlMs: ttl, expiresAt };
    setState({ resume, resumeError: null });
    return resume;
  }

  function clearResume() {
    setState({ resume: null, resumeError: null });
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
