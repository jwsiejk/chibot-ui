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
  const micPaths = new WeakMap();

  const getNodeName = (node) => {
    try {
      return node?.constructor?.name || "AudioNode";
    } catch (_) {
      return "AudioNode";
    }
  };

  const markMicFed = (node) => {
    try {
      if (node && typeof node === "object") {
        micFedNodes.add(node);
      }
    } catch (_) {}
  };

  const setMicPath = (node, path) => {
    try {
      if (!node || typeof node !== "object") {
        return;
      }
      if (!(node instanceof AudioNode)) {
        return;
      }

      if (Array.isArray(path) && path.length > 0) {
        micPaths.set(node, path);
      }
    } catch (_) {
      /* no-op */
    }
  };

  const getMicPath = (node) => {
    try {
      if (!node || typeof node !== "object") {
        return null;
      }
      if (isMediaStreamSource(node)) {
        if (!micPaths.has(node)) {
          micPaths.set(node, [getNodeName(node)]);
        }
        const basePath = micPaths.get(node) || [getNodeName(node)];
        return Array.isArray(basePath) ? [...basePath] : [getNodeName(node)];
      }
      const path = micPaths.get(node);
      return Array.isArray(path) ? [...path] : null;
    } catch (_) {
      return null;
    }
  };

  const originatesFromMic = (node) => {
    try {
      if (!node || typeof node !== "object") {
        return false;
      }
      if (isMediaStreamSource(node)) {
        return true;
      }
      if (micFedNodes.has(node)) {
        return true;
      }
      const path = getMicPath(node);
      return Array.isArray(path) && path.length > 0;
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
      const sourcePath = getMicPath(this);
      const sourceFromMic = originatesFromMic(this);

      if (sourceFromMic && destination instanceof AudioNode) {
        const destinationPath = Array.isArray(sourcePath)
          ? [...sourcePath, getNodeName(destination)]
          : [getNodeName(this), getNodeName(destination)];
        markMicFed(destination);
        setMicPath(destination, destinationPath);

        if (isAudioDestination(destination)) {
          try {
            console.warn("Blocked microphone signal from reaching AudioDestinationNode", {
              path: destinationPath.join(" -> "),
              nodes: destinationPath,
            });
          } catch (_) {}
          return destination;
        }
      }

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
