// app/static/js/audio/pcm_recorder.js
// DEPRECATED: The legacy PcmRecorder worklet path has been removed in favor of
// the pcm_sender + ws_audio_runtime capture stack. This placeholder remains so
// that any unexpected imports fail loudly during development.

const DEPRECATION_MESSAGE = "PcmRecorder is deprecated; use pcm_sender + ws_audio_runtime instead.";

export class PcmRecorder {
  constructor() {
    throw new Error(DEPRECATION_MESSAGE);
  }
}

function throwDeprecated() {
  throw new Error(DEPRECATION_MESSAGE);
}

if (typeof window !== "undefined") {
  try {
    window.AudioRecorder = {
      listening: false,
      startListening: () => throwDeprecated(),
      stopListening: () => throwDeprecated(),
      pause: () => throwDeprecated(),
      resume: () => throwDeprecated(),
      setSocket: () => throwDeprecated(),
      setPolicy: () => throwDeprecated(),
      startMicCaptureIfIdle: async () => throwDeprecated(),
    };
  } catch {
    // ignore failures to assign to window
  }
}
