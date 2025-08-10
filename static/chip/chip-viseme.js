// chip-viseme.js – Viseme-driven mouth overlay for Chip
// Uses image layers in /static/chip/img/visemes/*
// Works with a viseme timeline if provided; otherwise falls back to RMS-driven mouth swaps.

;(function (global) {
  const PATH = "/static/chip/img/visemes/";

  // Map viseme keys -> filenames (adjust to match your assets if needed)
  const MAP = {
    "neutral": "mouth_neutral.png",
    "m":       "mouth_m.png",       // closed
    "ee":      "mouth_ee.png",
    "aa":      "mouth_aa.png",
    "oh":      "mouth_oh.png",
    "f":       "mouth_f.png",
    "l":       "mouth_l.png",
    "r":       "mouth_r.png",
    "s":       "mouth_s.png",
    "uh":      "mouth_uh.png",
    "d":       "mouth_d.png",
    "w-oo":    "mouth_woo.png"
  };

  // Position and scale of the mouth overlay relative to chipImage rect
  const anchor = { x: 0.535, y: 0.525 }; // center of mouth as % of image
  const size   = { w: 0.16,  h: 0.11  }; // box size as % of image width

  let audioCtx = null;
  let rafId = null;
  let mouthImg = null;

  function preload() {
    Object.values(MAP).forEach(file => { const i = new Image(); i.src = PATH + file; });
  }

  function ensureMouth() {
    if (!mouthImg) {
      const box = document.getElementById("chipBox");
      mouthImg = document.createElement("img");
      mouthImg.id = "chipMouthImg";
      mouthImg.alt = "";
      if (box) box.appendChild(mouthImg);
    }
    return mouthImg;
  }

  function layout() {
    const img = document.getElementById("chipImage");
    const el = ensureMouth();
    if (!img || !el) return;

    const r = img.getBoundingClientRect();
    if (!r.width || !r.height) return;

    const cx = r.left + r.width  * anchor.x;
    const cy = r.top  + r.height * anchor.y;
    const w  = r.width * size.w;
    const h  = r.width * size.h;

    el.style.left = `${cx}px`;
    el.style.top = `${cy}px`;
    el.style.width = `${w}px`;
    el.style.height = `${h}px`;
    el.classList.remove("talking");
  }

  function setMouth(key) {
    const el = ensureMouth();
    const file = MAP[key] || MAP["neutral"];
    el.src = PATH + file;
  }

  function reset() {
    const el = ensureMouth();
    setMouth("neutral");
    el.classList.remove("talking");
  }

  // Drive with a viseme timeline: [{ viseme: "aa", start: <seconds> }, ...]
  function scheduleVisemes(visemes, audioEl) {
    const timers = [];
    visemes.forEach(({ viseme, start }) => {
      const key = (viseme || "neutral").toLowerCase();
      const file = MAP[key];
      if (!file) return;
      const t = setTimeout(() => setMouth(key), Math.max(0, (start || 0) * 1000));
      timers.push(t);
    });
    audioEl.addEventListener("ended", () => timers.forEach(clearTimeout), { once: true });
  }

  // Fallback when we don't have a timeline: simple RMS analyzer → swap images by loudness
  function driveByRMS(audioEl) {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioCtx.resume();

      const source = audioCtx.createMediaElementSource(audioEl);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      analyser.connect(audioCtx.destination);

      const data = new Uint8Array(analyser.fftSize);
      let lastKey = "neutral";

      const loop = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);

        // thresholds → choose a mouth shape
        const key =
          rms < 0.03 ? "m" :         // mostly closed
          rms < 0.06 ? "ee" :
          rms < 0.10 ? "aa" :
                       "oh";         // wide

        if (key !== lastKey) {
          setMouth(key);
          lastKey = key;
        }
        rafId = requestAnimationFrame(loop);
      };
      loop();

      audioEl.addEventListener("ended", () => cancelAnimationFrame(rafId), { once: true });
    } catch (e) {
      console.warn("RMS fallback failed:", e);
    }
  }

  // Public: play audio and animate mouth (visemes if provided; otherwise RMS)
  async function play(url, opts = {}) {
    preload();
    layout();

    const el = ensureMouth();
    el.classList.add("talking");
    setMouth("neutral");

    const audio = new Audio(url);
    audio.crossOrigin = "anonymous";

    if (opts.visemes && Array.isArray(opts.visemes) && opts.visemes.length) {
      scheduleVisemes(opts.visemes, audio);
    } else {
      driveByRMS(audio);
    }

    // Cleanup on end
    audio.addEventListener("ended", () => reset(), { once: true });
    audio.addEventListener("error", () => reset(), { once: true });

    await audio.play();
    return true;
  }

  function setAnchor(x, y) { anchor.x = x; anchor.y = y; layout(); }
  function setSize(w, h)   { size.w = w;   size.h = h;   layout(); }

  // Re-layout on resize / avatar resize
  if ("ResizeObserver" in window) {
    const img = document.getElementById("chipImage");
    if (img) new ResizeObserver(() => layout()).observe(img);
  }
  window.addEventListener("resize", layout);

  // Expose globally (works whether or not scripts use modules)
  global.ChipViseme = { play, layout, setAnchor, setSize, map: MAP, path: PATH };
})(window);
