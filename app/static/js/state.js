(() => {
  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function mergePlainObjects(base, patch) {
    // Only shallow merge is strictly necessary for the final state properties
    return { ...base, ...patch };
  }

  function cloneState(source) {
    if (!source || typeof source !== "object") {
      return {};
    }
    const next = { ...source };
    if (source.state && typeof source.state === "object") {
      next.state = { ...source.state };
    }
    return next;
  }

  const legacyAppState = typeof window !== "undefined" && window.AppState && typeof window.AppState === "object"
    ? window.AppState
    : null;
  const legacyNestedState = legacyAppState && legacyAppState.state && typeof legacyAppState.state === "object"
    ? legacyAppState.state
    : null;
  const defaultNestedState = {
    asrReady: false,
    micLive: false,
    tts: false,
    senderPaused: false,
    asrTurnActive: false,
    vadActive: false,
    vadDbfs: null,
    lastSpeechAt: null,
    processing: false,
  };
  const initialNestedState = legacyNestedState
    ? { ...defaultNestedState, ...legacyNestedState }
    : { ...defaultNestedState };

  // Cleaned up initialState while preserving the legacy nested `state` object for callers
  // that still rely on it.
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
    },
    // Consolidating mic/ASR state:
    wsConnected: false,
    wsPhase: "disconnected",
    ttsActive: false,
    asrArmInFlight: false, // KEPT, as it's a critical in-flight flag
    asrReady: false,
    // Unified Mic Control Flag (replaces micLive, recorderActive, and old nested listening)
    listening: false, 
    lastChunkTs: null,
    chunkCount: 0,
    lastErrorCode: null,
    lastErrorDetail: null,
    // VAD state remains flat
    vadActive: false,
    vadSpeech: false,
    vadConfidence: 0,
    vadEnergyDb: null,
    vadNoiseDb: null,
    vadDbfs: null,
    lastSpeechAt: null,
    state: initialNestedState,
  };

  let state = cloneState(initialState);
  const listeners = new Set();
  
  // Only track the necessary flat keys for telemetry
  const TELEMETRY_KEYS = [
    "wsConnected",
    "wsPhase",
    "ttsActive",
    "asrArmInFlight",
    "asrReady",
    "listening", // Consolidated flag
    "lastChunkTs",
    "chunkCount",
    "lastErrorCode",
    "lastErrorDetail",
    "vadActive",
    "vadSpeech",
    "vadConfidence",
    "vadEnergyDb",
    "vadNoiseDb",
  ];
  const DELTA_COALESCE_MS = 12;
  const HEARTBEAT_INTERVAL_MS = 5000;

  let telemetryPrev = null;
  let pendingDelta = null;
  let pendingDeltaTimerId = null;
  let telemetryHeartbeatTimerId = null;
  let lastHeartbeatPayload = null;

  function computeTelemetrySnapshot(sourceState = state) {
    const snapshot = sourceState && typeof sourceState === "object" ? sourceState : {};
    return {
      wsConnected: Boolean(snapshot.wsConnected),
      wsPhase: typeof snapshot.wsPhase === "string" ? snapshot.wsPhase : "disconnected",
      ttsActive: Boolean(snapshot.ttsActive),
      asrArmInFlight: Boolean(snapshot.asrArmInFlight),
      asrReady: Boolean(snapshot.asrReady),
      listening: Boolean(snapshot.listening),
      // Derived property: recorderActive is now equivalent to listening
      recorderActive: Boolean(snapshot.listening), 
      lastChunkTs: Number.isFinite(snapshot.lastChunkTs) ? snapshot.lastChunkTs : null,
      chunkCount: Number.isFinite(snapshot.chunkCount) ? snapshot.chunkCount : 0,
      lastErrorCode: Number.isFinite(snapshot.lastErrorCode) ? snapshot.lastErrorCode : (snapshot.lastErrorCode ?? null),
      lastErrorDetail: snapshot.lastErrorDetail ?? null,
      vadActive: Boolean(snapshot.vadActive),
      vadSpeech: Boolean(snapshot.vadSpeech),
      vadConfidence: Number.isFinite(snapshot.vadConfidence) ? snapshot.vadConfidence : 0,
      vadEnergyDb: Number.isFinite(snapshot.vadEnergyDb) ? snapshot.vadEnergyDb : null,
      vadNoiseDb: Number.isFinite(snapshot.vadNoiseDb) ? snapshot.vadNoiseDb : null,
    };
  }

  function publishTelemetry(label, payload) {
    try {
      const hub = window.AppState && window.AppState.hub;
      if (hub && typeof hub.log === "function") {
        hub.log(label, payload);
      }
    } catch (err) {
      try {
        console.warn("AppState telemetry publish failed", label, err);
      } catch {}
    }
  }

  function normalizeTelemetryValue(value) {
    return value === undefined ? null : value;
  }

  function syncTrackedProperties(snapshot) {
    const target = window.AppState;
    if (!target || typeof target !== "object") {
      return;
    }
    for (const key of TELEMETRY_KEYS) {
      target[key] = snapshot[key];
    }
    // Remove all references to nested state (target.state)
  }

  function scheduleDeltaFlush() {
    if (pendingDeltaTimerId) {
      return;
    }
    pendingDeltaTimerId = setTimeout(() => {
      pendingDeltaTimerId = null;
      if (!pendingDelta) {
        return;
      }
      const snapshot = pendingDelta.snapshot || telemetryPrev || computeTelemetrySnapshot();
        const detail = {
          changed: pendingDelta.changed,
          now_ms: Date.now(),
          wsPhase: snapshot.wsPhase,
          listening: snapshot.listening,
          ttsActive: snapshot.ttsActive,
          asrArmInFlight: snapshot.asrArmInFlight,
          chunkCount: snapshot.chunkCount,
          vadActive: snapshot.vadActive,
          vadSpeech: snapshot.vadSpeech,
          vadConfidence: snapshot.vadConfidence,
          vadEnergyDb: snapshot.vadEnergyDb,
          vadNoiseDb: snapshot.vadNoiseDb,
        };
      publishTelemetry("client.appstate.delta", detail);
      pendingDelta = null;
    }, DELTA_COALESCE_MS);
  }

  function recordTelemetryChange(prevSnapshot, nextSnapshot) {
    if (!prevSnapshot) {
      return;
    }
    const changed = {};
    for (const key of TELEMETRY_KEYS) {
      if (!Object.is(prevSnapshot[key], nextSnapshot[key])) {
        changed[key] = true;
      }
    }
    const changedKeys = Object.keys(changed);
    if (!changedKeys.length) {
      return;
    }
    if (!pendingDelta) {
      pendingDelta = { changed: {}, snapshot: nextSnapshot };
    }
    for (const key of changedKeys) {
      const existing = pendingDelta.changed[key];
      if (existing) {
        existing.new = normalizeTelemetryValue(nextSnapshot[key]);
        continue;
      }
      pendingDelta.changed[key] = {
        old: normalizeTelemetryValue(prevSnapshot[key]),
        new: normalizeTelemetryValue(nextSnapshot[key]),
      };
    }
    pendingDelta.snapshot = nextSnapshot;
    scheduleDeltaFlush();
  }

  function stopTelemetryHeartbeat() {
    if (telemetryHeartbeatTimerId) {
      clearInterval(telemetryHeartbeatTimerId);
      telemetryHeartbeatTimerId = null;
    }
    lastHeartbeatPayload = null;
  }

  function heartbeatPayloadEqual(a, b) {
    if (!a || !b) {
      return false;
    }
    return (
      a.wsPhase === b.wsPhase &&
      a.wsConnected === b.wsConnected &&
      a.ttsActive === b.ttsActive &&
      a.listening === b.listening &&
      // Removed recorderActive comparison
      a.chunkCount === b.chunkCount &&
      a.lastChunkAgeMs === b.lastChunkAgeMs &&
      a.vadActive === b.vadActive &&
      a.vadSpeech === b.vadSpeech &&
      a.vadConfidence === b.vadConfidence &&
      a.vadEnergyDb === b.vadEnergyDb &&
      a.vadNoiseDb === b.vadNoiseDb
    );
  }

  function runTelemetryHeartbeat() {
    const snapshot = telemetryPrev || computeTelemetrySnapshot();
    if (!snapshot.wsConnected) {
      stopTelemetryHeartbeat();
      return;
    }
    const lastChunkAgeMs = snapshot.chunkCount > 0 && Number.isFinite(snapshot.lastChunkTs)
      ? Math.max(0, Date.now() - snapshot.lastChunkTs)
      : null;
    const payload = {
      wsPhase: snapshot.wsPhase,
      wsConnected: snapshot.wsConnected,
      ttsActive: snapshot.ttsActive,
      listening: snapshot.listening,
      // Derived property: recorderActive is now snapshot.listening
      recorderActive: snapshot.listening, 
      chunkCount: snapshot.chunkCount,
      lastChunkAgeMs,
      vadActive: snapshot.vadActive,
      vadSpeech: snapshot.vadSpeech,
      vadConfidence: snapshot.vadConfidence,
      vadEnergyDb: snapshot.vadEnergyDb,
      vadNoiseDb: snapshot.vadNoiseDb,
    };
    if (heartbeatPayloadEqual(payload, lastHeartbeatPayload)) {
      return;
    }
    publishTelemetry("client.appstate.heartbeat", payload);
    lastHeartbeatPayload = payload;
  }

  function ensureTelemetryHeartbeat(snapshot) {
    if (snapshot.wsConnected) {
      if (!telemetryHeartbeatTimerId) {
        telemetryHeartbeatTimerId = setInterval(runTelemetryHeartbeat, HEARTBEAT_INTERVAL_MS);
        runTelemetryHeartbeat();
      }
      return;
    }
    stopTelemetryHeartbeat();
  }

  function processTelemetry(nextState) {
    const nextSnapshot = computeTelemetrySnapshot(nextState);
    if (!telemetryPrev) {
      telemetryPrev = nextSnapshot;
      syncTrackedProperties(nextSnapshot);
      ensureTelemetryHeartbeat(nextSnapshot);
      return;
    }
    const prevSnapshot = telemetryPrev;
    telemetryPrev = nextSnapshot;
    syncTrackedProperties(nextSnapshot);
    recordTelemetryChange(prevSnapshot, nextSnapshot);
    ensureTelemetryHeartbeat(nextSnapshot);
  }

  telemetryPrev = computeTelemetrySnapshot(state);
  syncTrackedProperties(telemetryPrev);

  function snapshot() {
    return cloneState(state);
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

  function syncLegacyStateShape() {
    if (typeof AppState !== "object" || !AppState) {
      return;
    }
    AppState.state = state.state && typeof state.state === "object"
      ? { ...state.state }
      : { ...initialNestedState };
  }

  function setState(patch) {
    if (!patch || typeof patch !== "object") return;

    const sanitized = { ...patch };
    let nestedPatch = null;
    if (Object.prototype.hasOwnProperty.call(sanitized, "state")) {
      const candidate = sanitized.state;
      if (candidate && typeof candidate === "object") {
        nestedPatch = { ...candidate };
      }
      delete sanitized.state;
    }

    if (nestedPatch) {
      for (const key of Object.keys(nestedPatch)) {
        if (key === "listening" && Object.prototype.hasOwnProperty.call(sanitized, "listening")) {
          continue;
        }
        if (!Object.prototype.hasOwnProperty.call(sanitized, key)) {
          sanitized[key] = nestedPatch[key];
        }
      }
    }

    const listeningFromNested =
      nestedPatch && Object.prototype.hasOwnProperty.call(nestedPatch, "listening")
        ? nestedPatch.listening
        : undefined;

    if (Object.prototype.hasOwnProperty.call(sanitized, "listening")) {
      delete sanitized.micLive;
      delete sanitized.recorderActive;
      if (nestedPatch) {
        delete nestedPatch.micLive;
        delete nestedPatch.recorderActive;
      }
    } else if (typeof listeningFromNested !== "undefined") {
      sanitized.listening = listeningFromNested;
      delete nestedPatch.micLive;
      delete nestedPatch.recorderActive;
    }

    let nextState = cloneState(state);
    nextState = { ...nextState, ...sanitized };

    if (nestedPatch) {
      const previousNested = state.state && typeof state.state === "object" ? state.state : {};
      const mergedNested = { ...previousNested, ...nestedPatch };
      nextState.state = mergedNested;
    } else if (state.state && typeof state.state === "object") {
      nextState.state = { ...state.state };
    }

    if (
      Object.prototype.hasOwnProperty.call(sanitized, "listening") ||
      (nestedPatch && Object.prototype.hasOwnProperty.call(nestedPatch, "listening"))
    ) {
      const listeningValue = Boolean(nextState.listening);
      nextState.listening = listeningValue;
      nextState.micLive = listeningValue;
      nextState.recorderActive = listeningValue;
      if (nextState.state && typeof nextState.state === "object") {
        nextState.state = {
          ...nextState.state,
          listening: listeningValue,
          micLive: listeningValue,
          recorderActive: listeningValue,
        };
      }
    }

    state = nextState;
    syncLegacyStateShape();
    processTelemetry(state);
    notify();
  }

  function setResume(token, ttlMs) {
    const candidate = typeof token === "string" ? token.trim() : "";
    const ttl = Number(ttlMs);
    if (!candidate || !Number.isFinite(ttl) || ttl <= 0) {
      clearResume();
      return null;
    }
    const expiresAt = Date.now() + ttl;
    const resumeState = {
      token: candidate,
      ttlMs: ttl,
      expiresAt
    };
    state = {
      ...state,
      resume: resumeState,
      resumeError: null
    };
    syncLegacyStateShape();
    processTelemetry(state);
    notify();
    return resumeState;
  }

  function clearResume() {
    if (state.resume !== null || state.resumeError !== null) {
      state = { ...state, resume: null, resumeError: null };
      syncLegacyStateShape();
      processTelemetry(state);
      notify();
    }
  }

  function reset() {
    state = cloneState(initialState);
    syncLegacyStateShape();
    processTelemetry(state);
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
      return cloneState(initialState);
    }
  };

  syncLegacyStateShape();

  syncTrackedProperties(telemetryPrev);

  // Hub logging/queueing logic remains the same
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
