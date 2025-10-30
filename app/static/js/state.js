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

  const AppState = window.AppState = {
    get: snapshot,
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

  const hubLogs = [];
  const hubStartQueue = [];
  const hubStopQueue = [];
  let hubPendingSocket = { hasValue: false, value: null };
  let hubImpl = null;

  function enqueueLog(label, detail) {
    if (hubLogs.length >= 40) {
      hubLogs.shift();
    }
    hubLogs.push({ label, detail });
  }

  function enqueueStart(policy) {
    if (hubStartQueue.length >= 10) {
      hubStartQueue.shift();
    }
    hubStartQueue.push(policy);
  }

  function enqueueStop(reason) {
    if (hubStopQueue.length >= 10) {
      hubStopQueue.shift();
    }
    hubStopQueue.push(reason);
  }

  function invoke(method, args) {
    if (!hubImpl || typeof hubImpl[method] !== "function") {
      return undefined;
    }
    try {
      return hubImpl[method](...args);
    } catch (err) {
      try {
        console.warn("AppState.hub handler failed", method, err);
      } catch {}
      return undefined;
    }
  }

  function flushQueues() {
    if (!hubImpl) {
      return;
    }
    if (hubPendingSocket.hasValue) {
      invoke("bindSocket", [hubPendingSocket.value]);
      hubPendingSocket = { hasValue: false, value: null };
    }
    if (hubLogs.length) {
      const pending = hubLogs.splice(0, hubLogs.length);
      for (const entry of pending) {
        invoke("log", [entry.label, entry.detail]);
      }
    }
    if (hubStartQueue.length) {
      const pending = hubStartQueue.splice(0, hubStartQueue.length);
      for (const policy of pending) {
        invoke("startListening", [policy]);
      }
    }
    if (hubStopQueue.length) {
      const pending = hubStopQueue.splice(0, hubStopQueue.length);
      for (const reason of pending) {
        invoke("stopListening", [reason]);
      }
    }
  }

  const hubApi = {
    log(label, detail) {
      if (!hubImpl || typeof hubImpl.log !== "function") {
        enqueueLog(label, detail);
        return undefined;
      }
      return invoke("log", [label, detail]);
    },
    bindSocket(ws) {
      if (!hubImpl || typeof hubImpl.bindSocket !== "function") {
        hubPendingSocket = { hasValue: true, value: ws || null };
        return undefined;
      }
      return invoke("bindSocket", [ws || null]);
    },
    startListening(policy) {
      if (!hubImpl || typeof hubImpl.startListening !== "function") {
        enqueueStart(policy);
        return undefined;
      }
      return invoke("startListening", [policy]);
    },
    stopListening(reason) {
      if (!hubImpl || typeof hubImpl.stopListening !== "function") {
        enqueueStop(reason);
        return undefined;
      }
      return invoke("stopListening", [reason]);
    },
  };

  Object.defineProperty(hubApi, "_install", {
    value(impl) {
      if (!impl || typeof impl !== "object") {
        return;
      }
      hubImpl = impl;
      flushQueues();
    },
    enumerable: false,
  });

  AppState.hub = hubApi;
})();
