const AudioContextCtor = typeof window !== "undefined"
  ? window.AudioContext || window.webkitAudioContext
  : null;

let micAudioCtx = null;
let playbackAudioCtx = null;

function createAudioContext() {
  if (!AudioContextCtor) {
    throw new Error("Web Audio API is not supported in this browser");
  }
  return new AudioContextCtor({ latencyHint: "interactive" });
}

function ensureContext(instance) {
  if (instance && instance.state === "closed") {
    return null;
  }
  return instance || null;
}

export function getMicAudioContext() {
  micAudioCtx = ensureContext(micAudioCtx);
  if (!micAudioCtx) {
    micAudioCtx = createAudioContext();
  }
  return micAudioCtx;
}

export function getPlaybackAudioContext() {
  playbackAudioCtx = ensureContext(playbackAudioCtx);
  if (!playbackAudioCtx) {
    playbackAudioCtx = createAudioContext();
  }
  return playbackAudioCtx;
}
