// chip-viseme.js – Viseme-driven mouth overlay for Chip
// Uses image layers in /static/chip/img/visemes/*
// If no viseme timeline is provided, falls back to RMS-based mouth swaps.

;(function (global) {
  const PATH = "/static/chip/img/visemes/";

  // Map viseme keys -> filenames (adjust to your asset names if needed)
  // NOTE: Removed r, oh, and woo so they never render.
  const MAP = {
    neutral: "mouth_neutral.png",
    m:       "mouth_m.png",       // closed
    ee:      "mouth_ee.png",
    aa:      "mouth_aa.png",
    f:       "mouth_f.png",
    l:       "mouth_l.png",
    s:       "mouth_s.png",
    uh:      "mouth_uh.png",
    d:       "mouth_d.png",
  };

  // Position and scale of the mouth overlay relative to chipImage rect
  // anchor = percentage position ON the image; size = box size as % of image width
  const anchor = { x: 0.535, y: 0.525 };
  const size   = { w: 0.16,  h: 0.11  };

  // Extra pixel offsets to fine-tune placement on screen
  // Negative x moves mouth to SCREEN LEFT (your left). Tweak here if needed.
  const offset = { x: -12, y: 0 }; // << moved left ~12px per your request

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

  // Position relative to chipBox, not viewport
  function layout() {
    const img = document.getElementById("chipImage");
    const box = document.getElementById("chipBox");
    const el  = ensureMouth();
    if (!img || !el || !box) return;

    const rImg = img.getBoundingClientRect();
    const rBox = box.getBoundingClientRect();
    if (!rImg.width || !rImg.height) return;

    const cx = (rImg.left - rBox.left) + rImg.width  * anchor.x + offset.x;
    const cy = (rImg.top  - rBox.top)  + rImg.height * anchor.y + offset.y;
    const w  = rImg.width * size.w;
    const h  = rImg.width * size.h;

    el.style.left   = `${cx}px`;
    el.style.top    = `${cy}px`;
    el.style.width  = `${w}px`;
    el.style.height = `${h}px`;
  }

  function setMouth(key) {
    const el = ensureMouth();
    const file = MAP[key] || MAP.neutral;
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
      const k = (viseme || "neutral").toLowerCase();

      // Block unwanted visemes explicitly
      if (k === "r" || k === "oh" || k === "w-oo" || k === "woo") return;

      // Only schedule if we have a mapped asset
      if (!MAP[k]) return;

      const t = setTimeout(() => setMouth(k), Math.max(0, (start || 0) * 1000));
      timers.push(t);
    });
    audioEl.addEventListener("ended", () => timers.forEach(clearTimeout), { once: true });
  }

  // Fallback: RMS analyzer → pick mouth by loudness
  // (No longer selects "oh" — routes highest band to "aa" instead.)
  function driveByRMS(audioEl) {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioCtx.resume();

      const source   = audioCtx.createMediaElementSource(audioEl);
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

        const key =
          rms < 0.03 ? "m"  :
          rms < 0.06 ? "ee" :
          rms < 0.10 ? "aa" :
                       "aa"; // previously "oh" — blocked per request

        if (MAP[key] && key !== lastKey) {
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

  // Public: play audio and animate mouth (visemes if provided; else RMS)
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

    audio.addEventListener("ended", reset, { once: true });
    audio.addEventListener("error", reset, { once: true });

    await audio.play();
    return true;
  }

  function setAnchor(x, y) { anchor.x = x; anchor.y = y; layout(); }
  function setSize(w, h)   { size.w = w;   size.h = h;   layout(); }
  function setOffset(dx, dy) { offset.x = dx|0; offset.y = dy|0; layout(); } // optional external tweak

  // Re-layout on resize / avatar resize
  if ("ResizeObserver" in window) {
    const img = document.getElementById("chipImage");
    if (img) new ResizeObserver(() => layout()).observe(img);
  }
  window.addEventListener("resize", layout);

  // Ensure neutral mouth is visible on page load (before any speech)
  window.addEventListener("DOMContentLoaded", () => {
    preload();
    layout();
    setMouth("neutral");
  });

  global.ChipViseme = { play, layout, setAnchor, setSize, setOffset, map: MAP, path: PATH };
})(window);
