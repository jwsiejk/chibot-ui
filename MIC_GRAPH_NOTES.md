# Mic graph notes

- Primary mic capture flows originate from `navigator.mediaDevices.getUserMedia` in:
  - `app/static/js/app.js` (`getMicOnce`, visualizer fallback `requestMicStreamForVisualizer`).
  - `app/static/js/audio/capture_runtime.js` (capture pipeline).
  - `app/static/js/audio/pcm_sender.js` (PCM sender setup).
  - `app/static/js/audio/ws_audio_runtime.js` (reacquire path).
- Mic streams are wrapped with `MediaStreamAudioSourceNode` and fan out through processing nodes.
  The guard in `app/static/js/audio/guard_mic_monitor.js` patches `AudioNode.prototype.connect`
  to track mic-origin paths and log `mic_guard.block` / `mic_guard.allow` with paths and stacks.
- MediaStream/Analyser usage along the mic path:
  - `app/static/js/app.js` waveform visualizer uses an `AnalyserNode` fed from the mic stream and
    sinks it into a `MediaStreamAudioDestinationNode` to stay silent.
  - `app/static/js/audio/pcm_sender.js` uses a `MediaStreamAudioDestinationNode` to sink PCM
    processing output derived from the mic.
- When reading logs, look for `mic_guard.block` (blocked mic → output connection) and
  `mic_guard.allow` (mic paths that currently reach output-type nodes but are allowed).
  Each log includes node names, path arrays, and JS stack traces to pinpoint call sites.
- OS-level "Listen to this device" / loopback settings are outside this graph and must be
  inspected in the operating system sound settings.
