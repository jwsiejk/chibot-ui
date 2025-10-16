// static/js/askchip_client_hooks.js — control frame handler + minimal mic arming
(function () {
  const ADMIN_POST = "/api/v1/admin/log";
  const ADMIN_LOG_STREAM = "/api/v1/admin/logs?live=1&bridge=ui";

  function currentSessionId() {
    try {
      const key = "chip.sid";
      let sid = localStorage.getItem(key);
      if (!sid) {
        if (typeof crypto?.randomUUID === "function") {
          sid = crypto.randomUUID();
        } else {
          sid = `${Date.now()}-${Math.random()}`;
        }
        localStorage.setItem(key, sid);
      }
      return sid || undefined;
    } catch (err) {
      try { console.warn("[askchip] sid lookup failed", err); } catch {}
      return undefined;
    }
  }

  function normalizePayload(evt) {
    const payload = Object.assign({}, evt || {});
    const event = typeof payload.event === "string" ? payload.event : null;
    if (!payload.kind && event) payload.kind = event;
    if (!payload.event && payload.kind) payload.event = payload.kind;
    if (!payload.label && (event || payload.kind)) {
      payload.label = event || payload.kind;
    }
    const sid = payload.session_id || payload.sid || currentSessionId();
    if (sid) {
      payload.session_id = String(sid);
      payload.sid = String(sid);
    }
    if (!payload.sent_at) payload.sent_at = Date.now();
    if (!payload.ts_ms) payload.ts_ms = payload.sent_at;
    if (typeof payload.v !== "number") payload.v = 1;
    const turn = payload.turn_id ?? payload.turnId ?? null;
    payload.turn_id = turn == null ? null : String(turn);
    if (typeof payload.text_preview === "string" && payload.text_preview.length > 120) {
      payload.text_preview = payload.text_preview.slice(0, 120);
    }
    return payload;
  }
  
  function logAdminBreadcrumb(payload) {
    try {
      const label = payload?.label || payload?.event || payload?.kind;
      if (label) {
        try { console.info(String(label)); } catch {}
      }
      if (payload && typeof payload === "object") {
        if (payload.containerized) {
          try { console.info("containerized=true"); } catch {}
        }
        if (payload.container) {
          try { console.info(`container=${payload.container}`); } catch {}
        }
      }
    } catch {}
  }

  async function postAdmin(evt) {
    try {
      const payload = normalizePayload(evt);
      logAdminBreadcrumb(payload);
      await fetch(ADMIN_POST, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
    } catch {}
  }

  async function openEventSource(url, options = {}) {
    if (typeof window === "undefined" || typeof window.EventSource !== "function") {
      return null;
    }

    try {
      return new EventSource(url, options);
    } catch (err) {
      try {
        console.info("[askchip] admin console bridge unavailable", {
          url,
          error: err?.message || err,
        });
      } catch {}
      return null;
    }
  }

  // Minimal upstream hook; wire this to your WS uplink if not present.
  if (!window.askchip) window.askchip = {};
  if (typeof window.askchip.audioUp !== "function") {
    window.askchip.audioUp = function noop(_) {};
  }

  // Expose a start hook so tests can trigger the same flow your Start button uses.
  if (typeof window.startCall !== "function") {
    window.startCall = function () {
      // no-op placeholder; your UI can replace this to match the real Start.
      // The E2E runner can still click your Start button; this is a fallback.
      console.log("[askchip] startCall() placeholder");
      return true;
    };
    window.startCall.__askchipPlaceholder = true;
  }

  window.askchip.armMic = async function armMic() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });

      const mr = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });

      // Tell admin we are containerized (P4)
      postAdmin({ event: "media", containerized: true, container: "webm/opus" });

      let started = false;
      mr.ondataavailable = (e) => {
        if (!e.data || !e.data.size) return;
        if (!started) {
          started = true;
          postAdmin({ event: "asr:start" }); // P2
        }
        try { window.askchip.audioUp(e.data); } catch {}
      };

      mr.start(150); // 150ms
      console.log("[askchip] mic armed");
    } catch (err) {
      console.error("armMic failed", err);
      postAdmin({ event: "asr:error", msg: String(err && err.message || err) });
    }
  };

  // Handle control frames from the server bus
  window.askchip_onControl = function (msg) {
    if (msg && msg.type === "control" && msg.op === "arm_mic") {
      const delay = Number(msg.delay_ms || 0);
      setTimeout(() => window.askchip.armMic(), delay);
      postAdmin({ event: "mic:auto_arm_suggested", delay_ms: delay });
    }
  };

  async function installAdminConsoleBridge() {
    try {
      if (window.__askchip_admin_console_bridge) {
        return;
      }
      const es = await openEventSource(ADMIN_LOG_STREAM, { withCredentials: true });
      if (!es) {
        return;
      }
      window.__askchip_admin_console_bridge = es;
      let bridgeErrorLogged = false;
      const logBridgeIssue = (detail) => {
        if (bridgeErrorLogged) return;
        bridgeErrorLogged = true;
        try {
          console.info("[askchip] admin console bridge unavailable", detail);
        } catch {}
      };
      es.addEventListener("error", (event) => {
        logBridgeIssue({ url: ADMIN_LOG_STREAM, event: event?.type || "error" });
      }, { once: true });
      es.addEventListener("message", (ev) => {
        try {
          const data = JSON.parse(ev.data || "{}");
          const kind = data?.kind || data?.event || data?.label;
          if (!kind) return;
          const payload = data?.payload && typeof data.payload === "object" ? data.payload : undefined;
          const info = payload || data;
          switch (String(kind)) {
            case "latency_breakdown": {
              try { console.info("latency_breakdown", info); } catch {}
              const metrics = info?.metrics || data?.metrics;
              if (metrics && typeof metrics === "object") {
                Object.entries(metrics).forEach(([name, value]) => {
                  try { console.info(`${name}=${value}`); } catch {}
                });
              }
              break;
            }
            case "policy_decision": {
              const detail = info?.decision || info?.detail || info?.label || data?.decision || data?.detail;
              try { console.info(`policy_decision: ${detail ?? "unknown"}`); } catch {}
              break;
            }
            case "nlu": {
              try { console.info("nlu", info); } catch {}
              break;
            }
            case "session_goal": {
              try { console.info("session_goal", info); } catch {}
              break;
            }
            case "state": {
              try { console.info("state", info); } catch {}
              break;
            }
            case "barge_in":
            case "barge_resume":
            case "barge_commit":
            case "barge_cancel":
            case "tts_pause":
            case "tts_resume":
            case "tts_cancel":
            case "tts_commit":
            case "asr:start":
            case "asr:partial":
            case "asr:first_partial":
            case "asr:final":
            case "CloseStream ack": {
              try { console.info(String(kind), info); } catch {}
              break;
            }
            default: {
              try { console.info(String(kind), info); } catch {}
            }
          }
        } catch {}
      });
    } catch {}
  }

  function installSessionConsoleBridge() {
    try {
      if (window.__askchip_ws_console_bridge) {
        return;
      }

      const partialSeen = new Set();
      const finalSeen = new Set();

      const seenKey = (frame) => {
        try {
          const turnId = frame?.turn_id ?? frame?.channel?.turn_id ?? frame?.payload?.turn_id;
          if (turnId !== undefined && turnId !== null) {
            return `turn:${turnId}`;
          }
          const seq = frame?.sequence_id ?? frame?.seq ?? frame?.channel?.seq;
          if (seq !== undefined && seq !== null) {
            return `seq:${seq}`;
          }
        } catch {}
        return "global";
      };

      const resetSeen = () => {
        try { partialSeen.clear(); } catch {}
        try { finalSeen.clear(); } catch {}
      };

      const logLatency = (frame) => {
        try { console.info("latency_breakdown", frame); } catch {}
        const metrics = frame?.metrics || frame?.payload?.metrics;
        if (metrics && typeof metrics === "object") {
          Object.entries(metrics).forEach(([name, value]) => {
            try { console.info(`${name}=${value}`); } catch {}
          });
        }
      };

      const tag = (label) => {
        try { console.info(String(label)); } catch {}
      };

      const simple = new Set([
        "barge_in",
        "barge_resume",
        "barge_commit",
        "barge_cancel",
        "tts_pause",
        "tts_resume",
        "tts_cancel",
        "tts_commit",
        "asr:partial",
      ]);

      window.addEventListener("askchip-ws", (ev) => {
        try {
          const frame = ev?.detail;
          if (!frame || typeof frame !== "object") {
            return;
          }

          const type = frame.type;
          const event = typeof frame.event === "string" ? frame.event : undefined;
          const eventLabel = event || type;

          if (eventLabel && simple.has(String(eventLabel))) {
            tag(eventLabel);
          }

          if (type === "assistant_end" || event === "assistant_end") {
            tag("assistant_end");
          }
          if (type === "UtteranceEnd" || event === "UtteranceEnd") {
            tag("UtteranceEnd");
          }
          if (type === "latency_breakdown" || event === "latency_breakdown") {
            logLatency(frame);
          }
          if (type === "policy_decision" || (event && event.includes("policy_decision"))) {
            const detail = frame?.decision ?? frame?.detail ?? frame?.label ?? frame?.payload?.decision ?? "unknown";
            try { console.info(`policy_decision: ${String(detail)}`); } catch {}
          }
          if (type === "nlu" || event === "nlu") {
            try { console.info("nlu", frame); } catch {}
          }
          if (type === "session_goal" || event === "session_goal") {
            try { console.info("session_goal", frame); } catch {}
          }
          if (type === "state" || event === "state") {
            try { console.info("state", frame); } catch {}
          }

          if (type === "Result" || type === "Results" || event === "Result") {
            const channel = frame?.channel || frame?.payload?.channel || {};
            const isFinal = Boolean(
              (channel && typeof channel.is_final === "boolean" && channel.is_final) ||
              (typeof frame.final === "boolean" && frame.final)
            );
            const key = seenKey(frame);

            if (!isFinal) {
              if (!partialSeen.has(key)) {
                partialSeen.add(key);
                tag("asr:partial");
                tag("asr:first_partial");
              }
            } else {
              partialSeen.delete(key);
              if (!finalSeen.has(key)) {
                finalSeen.add(key);
                tag("asr:final");
              }
            }
          }

          if (type === "CloseStream" || event === "CloseStream") {
            resetSeen();
          }
        } catch {}
      }, { passive: true });

      window.addEventListener("askchip-ws-close", resetSeen, { passive: true });

      window.__askchip_ws_console_bridge = true;
    } catch {}
  }

  installAdminConsoleBridge();
  installSessionConsoleBridge();
})();
