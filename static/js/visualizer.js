const MIC_CONSTRAINTS = {
  audio: {
    channelCount: 1,
    sampleRate: 48000,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: false,
  },
};

let canvas = null;
let ctx = null;
let audioCtx = null;
let analyser = null;
let source = null;
let stream = null;
let dataArray = null;
let rafId = null;
let resizeObserver = null;
let resizeListener = null;
let canvasWidth = 0;
let canvasHeight = 0;
let strokeGradient = null;
let fillGradient = null;

function clearCanvas() {
  if (!ctx || !canvasWidth || !canvasHeight) return;
  ctx.clearRect(0, 0, canvasWidth, canvasHeight);
}

function traceWave(points) {
  if (!ctx || points.length < 2) return;
  ctx.moveTo(points[0].x, points[0].y);

  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const xc = (prev.x + curr.x) / 2;
    const yc = (prev.y + curr.y) / 2;
    ctx.quadraticCurveTo(prev.x, prev.y, xc, yc);
  }

  const last = points[points.length - 1];
  ctx.lineTo(last.x, last.y);
}

function drawIdleWave() {
  if (!ctx || !canvasWidth || !canvasHeight) return;
  clearCanvas();
  const midY = canvasHeight / 2;
  ctx.beginPath();
  ctx.moveTo(0, midY);
  ctx.lineTo(canvasWidth, midY);
  ctx.strokeStyle = 'rgba(120, 150, 220, 0.28)';
  ctx.lineWidth = 1.2;
  ctx.stroke();
}

function drawWaveform(values) {
  if (!ctx || !values || values.length === 0 || !canvasWidth || !canvasHeight) {
    return;
  }

  clearCanvas();

  const midY = canvasHeight / 2;
  const amplitude = canvasHeight * 0.62;
  const sampleCount = Math.max(2, Math.min(220, values.length));
  const step = (values.length - 1) / (sampleCount - 1);
  const slice = canvasWidth / (sampleCount - 1);
  const smoothing = 0.3;
  const points = new Array(sampleCount);

  let lastY = midY;
  for (let i = 0; i < sampleCount; i++) {
    const dataIndex = Math.round(i * step);
    const value = (values[dataIndex] - 128) / 128;
    const targetY = midY + value * amplitude;
    const y = lastY + (targetY - lastY) * smoothing;
    const x = slice * i;
    points[i] = { x, y };
    lastY = y;
  }

  ctx.beginPath();
  traceWave(points);
  ctx.lineTo(canvasWidth, midY);
  ctx.lineTo(0, midY);
  ctx.closePath();

  if (fillGradient) {
    ctx.fillStyle = fillGradient;
    ctx.fill();
  }

  ctx.beginPath();
  traceWave(points);
  ctx.strokeStyle = strokeGradient || 'rgba(148, 200, 255, 0.92)';
  ctx.lineWidth = 2.6;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.shadowColor = 'rgba(116, 160, 255, 0.32)';
  ctx.shadowBlur = 18;
  ctx.stroke();
  ctx.shadowBlur = 0;

  ctx.beginPath();
  ctx.moveTo(0, midY);
  ctx.lineTo(canvasWidth, midY);
  ctx.strokeStyle = 'rgba(120, 150, 220, 0.18)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

function updateGradients() {
  if (!ctx || !canvasWidth || !canvasHeight) return;
  strokeGradient = ctx.createLinearGradient(0, canvasHeight, canvasWidth, 0);
  strokeGradient.addColorStop(0, 'rgba(148, 215, 255, 0.95)');
  strokeGradient.addColorStop(0.55, 'rgba(124, 110, 255, 0.95)');
  strokeGradient.addColorStop(1, 'rgba(255, 143, 184, 0.95)');

  fillGradient = ctx.createLinearGradient(canvasWidth / 2, 0, canvasWidth / 2, canvasHeight);
  fillGradient.addColorStop(0, 'rgba(126, 118, 255, 0.32)');
  fillGradient.addColorStop(1, 'rgba(18, 22, 42, 0)');
}

function resizeCanvas() {
  if (!canvas || !ctx) return;

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const displayWidth = Math.max(1, Math.round(rect.width * dpr));
  const displayHeight = Math.max(1, Math.round(rect.height * dpr));

  if (canvas.width !== displayWidth || canvas.height !== displayHeight) {
    canvas.width = displayWidth;
    canvas.height = displayHeight;
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);

  canvasWidth = rect.width;
  canvasHeight = rect.height;

  updateGradients();
  drawIdleWave();
}

function setupResizeHandling() {
  if (!canvas || typeof window === 'undefined') return;

  resizeCanvas();

  if ('ResizeObserver' in window) {
    resizeObserver = new ResizeObserver(() => resizeCanvas());
    resizeObserver.observe(canvas);
  } else {
    resizeListener = () => resizeCanvas();
    window.addEventListener('resize', resizeListener, { passive: true });
  }
}

function teardownResizeHandling() {
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }

  if (resizeListener && typeof window !== 'undefined') {
    window.removeEventListener('resize', resizeListener);
    resizeListener = null;
  }
}

function ensureCanvas() {
  if (canvas && ctx) return true;

  canvas = document.querySelector('.visualizer-waveform');
  if (!canvas) return false;

  ctx = canvas.getContext('2d');
  if (!ctx) {
    canvas = null;
    return false;
  }

  setupResizeHandling();
  return true;
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

function renderFrame() {
  if (!analyser || !ctx) {
    rafId = null;
    return;
  }

  analyser.getByteTimeDomainData(dataArray);
  drawWaveform(dataArray);
  rafId = window.requestAnimationFrame(renderFrame);
}

export async function start() {
  if (rafId) return stream;
  if (!ensureCanvas()) return null;

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
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.55;
  analyser.minDecibels = -90;
  analyser.maxDecibels = -10;
  source.connect(analyser);

  dataArray = new Uint8Array(analyser.fftSize);
  resizeCanvas();
  renderFrame();
  return stream;
}

export function stop({ reset = true } = {}) {
  if (rafId) {
    window.cancelAnimationFrame(rafId);
    rafId = null;
  }

  cleanupGraph();

  if (reset) {
    drawIdleWave();
  }

  if (audioCtx && typeof audioCtx.suspend === 'function' && audioCtx.state === 'running') {
    audioCtx.suspend().catch(() => {});
  }

  return stream;
}

export function destroy() {
  stop({ reset: false });

  if (stream) {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  }

  teardownResizeHandling();
  clearCanvas();

  canvas = null;
  ctx = null;
  audioCtx = null;
  stream = null;
  canvasWidth = 0;
  canvasHeight = 0;
  strokeGradient = null;
  fillGradient = null;
}
