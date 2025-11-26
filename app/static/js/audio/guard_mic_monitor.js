// app/static/js/audio/guard_mic_monitor.js
// Safety net to prevent routing microphone sources to the speakers.

(function guardMicMonitor() {
  if (typeof AudioNode === "undefined" || !AudioNode.prototype?.connect) {
    return;
  }
  if (AudioNode.prototype.__askchip_guarded_connect) {
    return;
  }

  const originalConnect = AudioNode.prototype.connect;
  const isMediaStreamSource = (node) =>
    typeof MediaStreamAudioSourceNode !== "undefined" && node instanceof MediaStreamAudioSourceNode;
  const isAudioDestination = (node) =>
    typeof AudioDestinationNode !== "undefined" && node instanceof AudioDestinationNode;

  AudioNode.prototype.connect = function guardedConnect(...args) {
    try {
      const destination = args[0];
      if (isMediaStreamSource(this) && isAudioDestination(destination)) {
        try {
          console.warn("Blocked microphone source from connecting to AudioDestinationNode", {
            source: this?.constructor?.name || "MediaStreamAudioSourceNode",
            destination: destination?.constructor?.name || "AudioDestinationNode",
          });
        } catch (_) {}
        return destination;
      }
    } catch (err) {
      try {
        console.warn("Mic monitor guard failed to evaluate connection", err);
      } catch (_) {}
    }

    return originalConnect.apply(this, args);
  };

  Object.defineProperty(AudioNode.prototype, "__askchip_guarded_connect", {
    configurable: false,
    enumerable: false,
    writable: false,
    value: true,
  });
})();
