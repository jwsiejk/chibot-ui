const MIC_CONSTRAINTS = {
  audio: {
    channelCount: 1,
    sampleRate: 48000,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: false,
  },
};

let bars = null;
let audioCtx = null;
let analyser = null;
let source = null;
let dataArray = null;
let rafId = null;
let stream = null;

const MIN_SCALE = 0.2;
const MAX_SCALE = 1.8;

function ensureBars() {
  if (!bars || bars.length === 0) {
    bars = Array.from(document.querySelectorAll('.visualizer-bars span'));
  }
  return bars.length > 0;
}

function cleanupGraph() {
  if (source) {
    try { source.disconnect(); } catch (_) {}
  }
  if (analyser) {
    try { analyser.disconnect(); } catch (_) {}
  }
  source = null;
  analyser = null;
  dataArray = null;
}

function scaleForLevel(level) {
  const clamped = Math.min(1, Math.max(0, level));
  return MIN_SCALE + clamped * (MAX_SCALE - MIN_SCALE);
}

function opacityForLevel(level) {
  const clamped = Math.min(1, Math.max(0, level));
  return 0.35 + clamped * 0.55;
}

function renderFrame() {
  if (!analyser || !bars || bars.length === 0) {
    rafId = null;
    return;
  }

  analyser.getByteFrequencyData(dataArray);

  const barCount = bars.length;
  const binSize = Math.max(1, Math.floor(dataArray.length / barCount));

  for (let i = 0; i < barCount; i++) {
    const start = i * binSize;
    let sum = 0;
    let count = 0;

    for (let j = 0; j < binSize && start + j < dataArray.length; j++) {
      sum += dataArray[start + j];
      count++;
    }

    const avg = count ? sum / (count * 255) : 0;
    const scale = scaleForLevel(avg);
    const opacity = opacityForLevel(avg);
    const bar = bars[i];

    if (bar) {
      bar.style.transform = `scaleY(${scale})`;
      bar.style.opacity = opacity.toString();
    }
  }

  rafId = window.requestAnimationFrame(renderFrame);
}

export async function start() {
  if (rafId) return stream;
  if (!ensureBars()) return null;

  if (!stream || !stream.active) {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      throw new Error('getUserMedia is not supported in this browser');
    }
    stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  }

  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) {
      throw new Error('Web Audio API not supported');
    }
    audioCtx = new AC({ sampleRate: 48000 });
  }

  if (audioCtx.state === 'suspended') {
    try { await audioCtx.resume(); } catch (_) {}
  }

  cleanupGraph();

  source = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.6;
  analyser.minDecibels = -90;
  analyser.maxDecibels = -10;
  source.connect(analyser);
  dataArray = new Uint8Array(analyser.frequencyBinCount);

  renderFrame();
  return stream;
}

export function stop({ reset = true } = {}) {
  if (rafId) {
    window.cancelAnimationFrame(rafId);
    rafId = null;
  }

  if (reset && bars) {
    for (const bar of bars) {
      bar.style.transform = `scaleY(${MIN_SCALE})`;
      bar.style.opacity = '0.35';
    }
  }

  cleanupGraph();

  if (audioCtx && typeof audioCtx.suspend === 'function' && audioCtx.state === 'running') {
    audioCtx.suspend().catch(() => {});
  }

  return stream;
}
