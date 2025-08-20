const Viseme = (() => {
  let ctx = null, canvas = null;
  let schedule = [];
  let audio = null;
  let relative = true;
  let rafId = null;

  const MOUTHS = {
    NEUTRAL: (t) => drawMouth(0.1, 0.14),
    M:       (t) => drawMouth(0.02, 0.02),
    F:       (t) => drawMouth(0.03, 0.06),
    L:       (t) => drawMouth(0.06, 0.09),
    S:       (t) => drawMouth(0.04, 0.04),
    R:       (t) => drawMouth(0.05, 0.05),
    E:       (t) => drawMouth(0.08, 0.07),
    AI:      (t) => drawMouth(0.12, 0.09),
    O:       (t) => drawMouth(0.10, 0.06),
    U:       (t) => drawMouth(0.07, 0.04)
  };

  function init(cnv) {
    canvas = cnv;
    ctx = canvas.getContext('2d');
    resize();
    window.addEventListener('resize', resize);
  }

  function resize() {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.floor(rect.width * devicePixelRatio);
    canvas.height = Math.floor(rect.height * devicePixelRatio);
    if (ctx) ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    drawFrame("NEUTRAL", 0);
  }

  function drawMouth(widthFrac, heightFrac) {
    const w = canvas.width / devicePixelRatio;
    const h = canvas.height / devicePixelRatio;
    const cx = w * 0.5;
    const cy = h * 0.65;
    const mw = Math.max(6, w * widthFrac);
    const mh = Math.max(2, h * heightFrac);
    ctx.fillStyle = "#111";
    ctx.strokeStyle = "#ff6a00";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.ellipse(cx, cy, mw, mh, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  function drawFrame(v, t) {
    if (!ctx) return;
    ctx.clearRect(0,0,canvas.width,canvas.height);
    const w = canvas.width / devicePixelRatio, h = canvas.height / devicePixelRatio;
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(w*0.3, h*0.3, w*0.4, h*0.4);
    const fn = MOUTHS[v] || MOUTHS.NEUTRAL;
    fn(t);
  }

  function animate(_schedule, _audio, opts={}) {
    schedule = _schedule || [];
    audio = _audio;
    relative = !!opts.relative;
    cancelAnimationFrame(rafId);
    const loop = () => {
      rafId = requestAnimationFrame(loop);
      const cur = audio ? audio.currentTime : 0;
      const dur = audio && audio.duration ? audio.duration : 1;
      const t = relative ? (cur / Math.max(dur, 0.0001)) : cur;
      let v = "NEUTRAL";
      for (let i = 0; i < schedule.length; i++) {
        if (t >= schedule[i].t) v = schedule[i].v;
        else break;
      }
      drawFrame(v, t);
    };
    loop();
  }

  function stop() {
    cancelAnimationFrame(rafId);
    drawFrame("NEUTRAL", 0);
  }

  return { init, animate, stop };
})();
