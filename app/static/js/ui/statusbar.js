// UMD-style module: attaches to window.StatusBar
(function (global) {
  const StatusBar = {
    mountId: "statusbar-root",
    render(ui = {}) {
      const host = document.getElementById(this.mountId);
      if (!host) return;

      const f = {
        connected:    !!(ui.wsConn || ui.wsConning),
        asrReady:     !!ui.asrReady,
        micOpen:      !!ui.micLive,
        ttsActive:    !!ui.tts,
        senderPaused: !!ui.senderPaused,
      };
      const canCapture = f.asrReady && f.micOpen && !f.ttsActive && !f.senderPaused;

      let st;
      if (f.ttsActive)        st = { label: "Speaking…",      sub: "Say “Hold on” to interrupt.", tone: "blue"  };
      else if (!f.connected)  st = { label: "Ready",          sub: "Press Start to begin.",       tone: "gray"  };
      else if (canCapture)    st = { label: "Listening",      sub: "You can speak now.",          tone: "green" };
      else if (f.asrReady)    st = { label: "Preparing mic…", sub: "Getting ready to listen.",    tone: "amber" };
      else                    st = { label: "Ready",          sub: "",                            tone: "gray"  };

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
