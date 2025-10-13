// static/js/askchip_client_hooks.js — control frame handler + minimal mic arming
(function () {
  const ADMIN_POST = "/api/v1/admin/log";

  async function postAdmin(evt) {
    try {
      await fetch(ADMIN_POST, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(evt),
      });
    } catch {}
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
})();
