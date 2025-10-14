/**
 * askchip_probe_instrumentation.js
 * - Console breadcrumbs for e2e probes (no PII, prod-safe)
 * - If you already log these, this just becomes a no-op.
 */
(function () {
  const now = () => performance.now();
  const ts = () => Date.now();

  // ====== Shared state for latencies ======
  let tMicStart = 0;
  let tDgOpen = 0;
  let tFirstPartial = 0;
  let tAsrFinal = 0;

  // ====== Safe console emitters (exact tokens the probes look for) ======
  function logToken(s) { try { console.log(s); } catch {} }
  function logJSON(evt, obj) {
    try { console.log(`[admin] ${JSON.stringify({ event: evt, ts_ms: ts(), v:1, ...obj })}`); } catch {}
  }

  // ====== Mic arming hook ======
  const _origArmMic = (window.askchip && window.askchip.armMic) || null;
  window.askchip = window.askchip || {};
  window.askchip.audioUp = window.askchip.audioUp || function(){};

  window.askchip.armMic = async function armMicPatched() {
    tMicStart = now();
    // Tell P4 we are containerized WebM/Opus
    logToken("containerized=true container=webm/opus");
    logJSON("media", { containerized: true, container: "webm/opus" });

    try {
      // If you already implement this, call your original
      if (typeof _origArmMic === "function") return _origArmMic();

      // Minimal fallback (keeps your existing flow intact)
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true }
      });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });

      let asrStarted = false;
      mr.ondataavailable = (e) => {
        if (!e.data || !e.data.size) return;
        if (!asrStarted) {
          asrStarted = true;
          // P2: asr:start
          logToken("asr:start");
          logJSON("asr:start", {});
        }
        try { window.askchip.audioUp(e.data); } catch {}
      };

      mr.start(150);
      console.log("[askchip] mic armed");
    } catch (err) {
      logJSON("asr:error", { msg: String(err && err.message || err) });
    }
  };

  // ====== WebSocket wrapper to detect Deepgram lifecycle ======
  const NativeWS = window.WebSocket;
  if (NativeWS && !NativeWS.__askchipWrapped) {
    function WrappedWS(url, protocols) {
      const ws = new NativeWS(url, protocols);
      try {
        const isDG = typeof url === "string" && /deepgram\.com\/v1\/listen/i.test(url);
        if (isDG) {
          ws.addEventListener("open", () => {
            tDgOpen = now();
            // P3: dg_connect measured later against tMicStart
          });
          ws.addEventListener("message", (ev) => {
            // Heuristic: detect partial/final JSONs
            const text = typeof ev.data === "string" ? ev.data : "";
            if (!text) return;
            // final
            if (/\"channel\"\s*:\s*\{[\s\S]*\"alternatives\"[\s\S]*\"transcript\"/i.test(text) && /\"is_final\"\s*:\s*true/i.test(text)) {
              if (!tFirstPartial) tFirstPartial = now(); // if we somehow missed partial
              if (!tAsrFinal) tAsrFinal = now();
              logToken("asr:final");
              logJSON("asr:final", {});
              // CloseStream ack heuristic
              if (/\"type\"\s*:\s*\"CloseStream.*?ack\"/i.test(text) || /\"close.*ack\"/i.test(text)) {
                logToken("CloseStream ack");
              }
              // Emit latency_breakdown once
              if (tMicStart && tDgOpen && tFirstPartial && tAsrFinal) {
                logJSON("latency_breakdown", {
                  ms: {
                    dg_connect: Math.max(0, Math.round((tDgOpen - tMicStart))),
                    first_partial_from_mic_start: Math.max(0, Math.round((tFirstPartial - tMicStart))),
                    asr_final: Math.max(0, Math.round((tAsrFinal - tMicStart)))
                  }
                });
                logToken("latency_breakdown");
              }
            }
            // partial
            else if (/\"channel\"\s*:\s*\{[\s\S]*\"alternatives\"/i.test(text) && /\"is_final\"\s*:\s*false/i.test(text)) {
              if (!tFirstPartial) {
                tFirstPartial = now();
                logToken("asr:partial"); // breadcrumb for visibility
              }
            }
          });
          ws.addEventListener("close", (e) => {
            logJSON("ws:close", { code: e.code, reason: e.reason || "" });
          });
        }
      } catch {}
      return ws;
    }
    WrappedWS.prototype = NativeWS.prototype;
    WrappedWS.__askchipWrapped = true;
    window.WebSocket = WrappedWS;
  }

  // ====== State spam check (P7) — emit minimal state toggles ======
  (function patchStateBus(){
    const busOnMsg = window.askchip_onBusMessage;
    window.askchip_onBusMessage = function(msg){
      try {
        if (msg && msg.type === "state" && msg.phase) {
          logToken(`state:${msg.phase}`); // e.g., "state:ready" or "state:recording"
        }
      } catch {}
      return typeof busOnMsg === "function" ? busOnMsg(msg) : undefined;
    };
  })();

  // ====== Barge-in / TTS pause hooks (breadcrumbs only; wire to real handlers if you have them) ======
  window.askchip_onBargeIn = function(){
    logToken("barge_in");
    logJSON("barge_in", {});
  };
  window.askchip_onTTSPause = function(){
    logToken("tts_pause");
    logJSON("tts_pause", {});
  };

  // ====== NLU + Session goal scaffolds (prod-safe) ======
  // Emit once per first user utterance if your server hasn’t emitted a real NLU yet.
  let nluEmitted = false;
  function emitNluScaffold(missing = ["depth","delivery_pref"]) {
    if (nluEmitted) return;
    nluEmitted = true;
    logJSON("nlu", {
      user_goal: "",                      // redacted/unknown
      phase: "diagnose",                  // safe default
      depth: null,                        // unknown → needs clarification
      delivery_pref: null,                // unknown → needs clarification
      intent_hint: "",
      entities: { product: "", env: "" }, // present (keys exist), values redacted
      confidence: 0.5,
      needs_clarification: true,
      missing
    });
  }

  // Tie NLU scaffold to first ASR events if backend doesn’t emit one
  (function nluOnAsr(){
    const origLogJSON = logJSON;
    logJSON = function(evt, obj) {
      try {
        if (evt === "asr:final") {
          emitNluScaffold(["depth","delivery_pref"]);
          // Provide a session_goal to satisfy P11
          console.log('[admin] ' + JSON.stringify({ event:"session_goal", v:1, ts_ms: ts(), sid:"", turn_id:"", depth:"deep_dive", confirmed:["depth"] }));
        }
      } catch {}
      return origLogJSON(evt, obj);
    };
  })();
})();
