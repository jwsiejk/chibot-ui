// UMD-style module: attaches to window.StatusBar
(function (global) {
  const StatusBar = {
    mountId: "statusbar-root",
    render(ui = {}) {
      const host = document.getElementById(this.mountId);
      if (!host) return;

      // Optional high-level phase from the voicePhaseController, if available.
      // Expected values (if present): "boot", "greet", "conversation_ready",
      // "user_turn", "closing", "closed".
      const phase = ui.phase || null;

      const wsPhase = typeof ui.wsPhase === "string" ? ui.wsPhase : null;
      const connectionState = typeof ui.connectionState === "string" ? ui.connectionState : null;

      const f = {
        connecting:   !!(ui.wsConning || ui.wsConnecting || wsPhase === "connecting" || wsPhase === "resuming" || connectionState === "connecting" || connectionState === "resuming"),
        connected:    !!(ui.wsConn || ui.wsConnected || wsPhase === "connected" || wsPhase === "ready" || connectionState === "connected"),
        asrReady:     !!ui.asrReady,
        // Unified mic/listening flag from the new audio runtime
        micOpen:      !!(ui.listening || ui.micLive),
        ttsActive:    !!(ui.tts || ui.ttsActive),
        senderPaused: !!ui.senderPaused,
        processing:   !!ui.processing,
        hearingUser:  !!ui.asrTurnActive,
      };

      const canCapture = f.asrReady && f.micOpen && !f.ttsActive;

      let st;

      // --- Highest-level: session lifecycle / connection ---

      if (!f.connected && f.connecting) {
        st = {
          label: "Connecting…",
          sub: "Establishing a voice link.",
          tone: "gray",
        };

      } else if (!f.connected) {
        // No live WS connection yet
        st = {
          label: "Disconnected",
          sub: "Press Start to connect and begin.",
          tone: "gray",
        };

      } else if (phase === "closing" || phase === "closed" || wsPhase === "closing" || wsPhase === "closed" || connectionState === "closing" || connectionState === "closed") {
        st = {
          label: "Session ended",
          sub: "Press Start to begin a new session.",
          tone: "gray",
        };

      // --- Greet & TTS states ---

      } else if (phase === "greet" && f.ttsActive) {
        st = {
          label: "Greeting…",
          sub: "Chip is introducing himself. Say “Hold on” to interrupt.",
          tone: "blue",
        };

      } else if (f.ttsActive) {
        st = {
          label: "Speaking…",
          sub: "Say “Hold on” to interrupt.",
          tone: "blue",
        };

      } else if (!f.asrReady && f.micOpen) {
        st = {
          label: "Preparing mic…",
          sub: "Calibrating your input.",
          tone: "amber",
        };

      // --- LLM processing (no TTS yet) ---

      } else if (f.processing) {
        const thinkingSub =
          phase === "user_turn"
            ? "Chip is working on your answer."
            : "Waiting for Chip’s response.";
        st = {
          label: "Thinking…",
          sub: thinkingSub,
          tone: "purple",
        };

      } else if (canCapture && f.hearingUser) {
        st = {
          label: "Hearing you…",
          sub: "Capturing your speech.",
          tone: "green",
        };

      // --- Conversation-ready & listening states ---

      } else if (phase === "conversation_ready" && canCapture) {
        // This is the ideal “full-duplex ready” state after greet
        const hint = f.senderPaused
          ? "Listening (auto-paused on silence)."
          : "You can speak now.";
        st = {
          label: "Listening",
          sub: hint,
          tone: "green",
        };

      } else if (phase === "conversation_ready" && !f.micOpen) {
        // Connected, greet done, but mic has been explicitly paused
        st = {
          label: "Mic paused",
          sub: "Press Start to resume listening.",
          tone: "amber",
        };

      // --- Mic / ASR bootstrapping (no explicit phase info) ---

      } else if (!f.micOpen) {
        // Connected but not listening yet
        st = {
          label: "Stand by",
          sub: "Press Start to open the mic.",
          tone: "amber",
        };

      } else if (!f.asrReady) {
        st = {
          label: "Preparing mic…",
          sub: "Getting ready to listen.",
          tone: "amber",
        };

      } else if (canCapture) {
        // Fallback listening state when phase is unknown
        const hint = f.senderPaused
          ? "Listening (auto-pause on silence)."
          : "You can speak now.";
        st = {
          label: "Listening",
          sub: hint,
          tone: "green",
        };

      } else {
        // Generic safe fallback
        st = {
          label: "Ready",
          sub: "",
          tone: "gray",
        };
      }

      host.innerHTML = `
        <div class="statusbar status-${st.tone}">
          <div class="status-dot"></div>
          <div class="status-text">
            <div class="status-label">${st.label}</div>
            <div class="status-sub">${st.sub}</div>
          </div>
          <div class="status-meter" id="micMeter" aria-hidden="true">
            <div class="status-meter-fill" style="width:0%"></div>
          </div>
        </div>`;
    },
    updateMeter(rms /* 0..1 */) {
      const el = document.getElementById("micMeter");
      if (!el) return;
      const fill = el.querySelector(".status-meter-fill");
      if (!fill) return;
      const pct = Math.max(0, Math.min(100, Math.round(rms * 100)));
      fill.style.width = pct + "%";
    }
  };

  global.StatusBar = StatusBar;
})(window);
