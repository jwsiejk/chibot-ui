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
  const isMediaStreamDestination = (node) =>
    typeof MediaStreamAudioDestinationNode !== "undefined" && node instanceof MediaStreamAudioDestinationNode;

  const micFedNodes = new WeakSet();

  const markMicFed = (node) => {
    try {
      if (node && typeof node === "object") {
        micFedNodes.add(node);
      }
    } catch (_) {}
  };

  const originatesFromMic = (node) => {
    try {
      if (!node || typeof node !== "object") {
        return false;
      }
      if (isMediaStreamSource(node)) {
        return true;
      }
      return micFedNodes.has(node);
    } catch (_) {
      return false;
    }
  };

  const isAudibleSink = (node) => {
    if (!node || typeof node !== "object") {
      return false;
    }
    if (isAudioDestination(node) || isMediaStreamDestination(node)) {
      return true;
    }
    try {
      const name = node?.constructor?.name || "";
      return /Destination/i.test(name);
    } catch (_) {
      return false;
    }
  };

  AudioNode.prototype.connect = function guardedConnect(...args) {
    try {
      const destination = args[0];
      const sourceFromMic = originatesFromMic(this);

      if (sourceFromMic && destination && typeof destination === "object") {
        if (destination instanceof AudioNode) {
          markMicFed(destination);
        }

        if (isAudibleSink(destination)) {
          try {
            const directSource = isMediaStreamSource(this);
            const detail = {
              source: this?.constructor?.name || "AudioNode",
              destination: destination?.constructor?.name || "AudioDestinationNode",
            };
            if (directSource) {
              console.warn("Blocked microphone source from connecting to audible sink", detail);
            } else {
              console.warn("Blocked microphone-fed signal from reaching audible sink", {
                ...detail,
                reason: "upstream microphone input",
              });
            }
          } catch (_) {}
          return destination;
        }
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
