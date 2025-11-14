// app/static/js/audio/ws_audio_runtime.js
// Encapsulates PCM ring buffer, PCM sender, and ASR priming helpers.

export function createWsAudioRuntime({ AppState, initPcmSender, hubLog }) {
  // Private module-level state for PCM sender + ring buffer will live here.
  // For now, just stub out the helpers.

  function ensurePcmSender() {
    // stub – real implementation will be moved from ws_client.js
  }

  function handlePcmFrame(chunk, meta) {
    // stub – real implementation will be moved from ws_client.js
  }

  function handlePcmSend(chunk, meta) {
    // stub
  }

  function handleSampleRate(sampleRate, meta) {
    // stub
  }

  function primeAsrStreamFromRing(sid) {
    // stub
  }

  function recordRecorderChunk(tsMs) {
    // stub
  }

  function getPcmRing() {
    // stub – will return ring buffer instance if needed
    return null;
  }

  return {
    ensurePcmSender,
    handlePcmFrame,
    handlePcmSend,
    handleSampleRate,
    primeAsrStreamFromRing,
    recordRecorderChunk,
    getPcmRing,
  };
}
