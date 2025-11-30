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
  const downstreamConnections = new WeakMap();
  const blockedConnections = new Set();

  const getNodeName = (node) => {
    try {
      return node?.constructor?.name || "AudioNode";
    } catch (_) {
      return "AudioNode";
    }
  };

  function logMicBlock(source, dest, path, reason) {
    try {
      console.error("[MIC_ECHO_GUARD] blocked mic node connecting to destination", {
        sourceName: getNodeName(source),
        destName: getNodeName(dest),
        path,
        reason,
        stack: new Error().stack,
      });
    } catch (_) {
      // best-effort logging only
    }
  }

  const markMicFed = (node) => {
    try {
      if (node && typeof node === "object") {
        micFedNodes.add(node);
      }
    } catch (_) {}
  };

  const addDownstream = (source, destination) => {
    try {
      if (!source || !destination) {
        return;
      }
      if (!(source instanceof AudioNode) || !(destination instanceof AudioNode)) {
        return;
      }

      const current = downstreamConnections.get(source) || new Set();
      current.add(destination);
      downstreamConnections.set(source, current);
    } catch (_) {
      /* no-op */
    }
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

  const propagateMicPath = (startNode, basePath) => {
    try {
      if (!startNode || !Array.isArray(basePath) || basePath.length === 0) {
        return;
      }

      const queue = [{ node: startNode, path: basePath }];
      const visited = new WeakSet();

      while (queue.length > 0) {
        const current = queue.shift();
        const node = current?.node;
        const path = current?.path;

        if (!node || visited.has(node)) {
          continue;
        }
        visited.add(node);

        markMicFed(node);
        setMicPath(node, path);

        const downstream = downstreamConnections.get(node);
        if (downstream && downstream.size > 0) {
          downstream.forEach((child) => {
            const childPath = [...path, getNodeName(child)];
            queue.push({ node: child, path: childPath });
          });
        }
      }
    } catch (_) {
      /* no-op */
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
    if (isAudioDestination(node)) {
      return true;
    }
    if (isMediaStreamDestination(node)) {
      return false;
    }
    try {
      const name = node?.constructor?.name || "";
      return /Destination/i.test(name);
    } catch (_) {
      return false;
    }
  };

  const leadsToOutput = (node) => {
    try {
      if (!node || typeof node !== "object") {
        return false;
      }
      if (isMediaStreamDestination(node)) {
        return false;
      }
      if (isAudioDestination(node)) {
        return true;
      }
      const name = node?.constructor?.name || "";
      if (/Destination/i.test(name)) {
        return true;
      }
      return false;
    } catch (_) {
      return false;
    }
  };

  AudioNode.prototype.connect = function guardedConnect(...args) {
    try {
      const destination = args[0];
      if (destination instanceof AudioNode) {
        addDownstream(this, destination);
      }
      const sourcePath = getMicPath(this);
      const sourceFromMic = originatesFromMic(this);

      const destinationPath = Array.isArray(sourcePath)
        ? [...sourcePath, getNodeName(destination)]
        : [getNodeName(this), getNodeName(destination)];

      const logBlockOnce = (reason) => {
        const sourceName = getNodeName(this);
        const destName = getNodeName(destination);
        const key = `${sourceName}→${destName}`;
        if (!blockedConnections.has(key)) {
          blockedConnections.add(key);
          logMicBlock(this, destination, destinationPath, reason);
          try {
            if (typeof window.emitClientLog === "function") {
              window.emitClientLog("client.mic_monitor_blocked", {
                source: sourceName,
                destination: destName,
                destinationPath,
                reason,
              });
            }
          } catch (_) {}
        }
      };

      if (sourceFromMic && destination instanceof AudioNode) {
        const routesToOutput = isAudioDestination(destination) || leadsToOutput(destination) || isAudibleSink(destination);
        if (routesToOutput) {
          logBlockOnce("upstream microphone input");
          return destination;
        }
        propagateMicPath(destination, destinationPath);
      }

      if (sourceFromMic && destination && typeof destination === "object") {
        if (destination instanceof AudioNode) {
          markMicFed(destination);
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

// ---------------------------------------------------------------------------
// HTMLMediaElement.srcObject guard: auto-mute mic streams
// ---------------------------------------------------------------------------
(function guardMediaElementSrcObject() {
  try {
    if (typeof window === "undefined") return;
    const HME = window.HTMLMediaElement;
    if (!HME || HME.prototype.__askchip_guarded_srcObject) {
      return;
    }

    const proto = HME.prototype;

    // Reuse the existing srcObject descriptor if present
    const desc =
      Object.getOwnPropertyDescriptor(proto, "srcObject") ||
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(proto) || {}, "srcObject");

    if (!desc || typeof desc.set !== "function") {
      return;
    }

    const originalSet = desc.set;
    const originalGet = desc.get;

    function looksLikeMicStream(stream) {
      try {
        return (
          stream &&
          typeof stream.getAudioTracks === "function" &&
          stream.getAudioTracks().length > 0
        );
      } catch (_) {
        return false;
      }
    }

    Object.defineProperty(proto, "srcObject", {
      configurable: true,
      enumerable: desc.enumerable,
      get: function () {
        return originalGet ? originalGet.call(this) : undefined;
      },
      set: function (stream) {
        try {
          if (looksLikeMicStream(stream)) {
            // Any media element fed a mic stream is forcibly silenced.
            this.muted = true;
            this.volume = 0;
            try {
              console.warn("[guard_mic_monitor] muted media element assigned mic stream");
            } catch (_) {}
          }
        } catch (_) {
          // ignore guard errors and still call original setter
        }
        return originalSet.call(this, stream);
      },
    });

    HME.prototype.__askchip_guarded_srcObject = true;
  } catch (_) {
    // Fail-closed: if we can't patch srcObject, just skip without breaking the app.
  }
})();
