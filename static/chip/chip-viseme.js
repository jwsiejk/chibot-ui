// chip-viseme.js — robust stage selection + neutral on load + blocks r/oh/woo

;(function (global) {
  const PATH = "/static/chip/img/visemes/";

  // Map viseme keys -> filenames (r/oh/woo removed)
  const MAP = {
    neutral: "mouth_neutral.png",
    m:       "mouth_m.png",
    ee:      "mouth_ee.png",
    aa:      "mouth_aa.png",
    f:       "mouth_f.png",
    l:       "mouth_l.png",
    s:       "mouth_s.png",
    uh:      "mouth_uh.png",
    d:       "mouth_d.png",
  };

  // Anchor on the chip image (percent), size as % of chip image width
  const anchor = { x: 0.535, y: 0.525 };
  const size   = { w: 0.16,  h: 0.11  };

  // Fine pixel offsets (screen coords) — nudge left/right/up/down
  const offset = { x: -12, y: 0 };

  let audioCtx = null;
  let rafId = null;
  let mouthImg = null;

  function getStage() {
    return (
      document.getElementById("chipBox") ||
      document.querySelector(".chip-box") ||
      document.getElementById("app") ||
      document.body
    );
  }

  function getChipImage() {
    return document.getElementById("chipImage") || document.querySelector("#chipImage, .chip-image");
  }

  function preload() {
    Object.values(MAP).forEach(file => { const i = new Image(); i.src = PATH + file; });
  }

  function ensureMouth() {
    if (!mouthImg) {
      mouthImg = document.createElement("img");
      mouthImg.id = "chipMouthImg";
      mouthImg.alt = "";
    }
    // Attach if not in DOM
    if (!mouthImg.isConnected) {
      const stage = getStage();
      stage.appendChild(mouthImg);
    }
    return mouthImg;
  }

  function layout() {
    const img = getChipImage();
    const stage = getStage();
    const el = ensureMouth();
    if (!img || !el || !stage) return;

    const rImg = img.getBoundingClientRect();
    const rStage = stage.getBoundingClientRect();

    // If image not laid out yet, try again soon
    if (!rImg.width || !rImg.height) {
      requestAnimationFrame(layout);
      return;
    }

    const cx = (rImg.left - rStage.left) + rImg.width  * anchor.x + offset.x;
    const cy = (rImg.top  - rStage.top)  + rImg.height * anchor.y + offset.y;
    const w  = rImg.width * size.w;
    const h  = rImg.width * size.h;

    // Absolutely position within stage; CSS gives z-index
    el.style.position = "absolute";
    el.style.left   = `${cx}px`;
    el.style.top    = `${cy}px`;
    el.style.width  = `${w}px`;
    el.style.height = `${h}px`;
    el.style.pointerEvents = "none";
  }

  function setMouth(key) {
    const el = ensureMouth();
    const file = MAP[key] || MAP.neutral;
    el.src = PATH + file;
  }

  function reset() {
    setMouth("neutral");
    ensureMouth().classList.remove("talking");
  }

  function scheduleVisemes(visemes, audioEl) {
    const timers = [];
    visemes.forEach(({ viseme, start }) => {
      const k = (viseme || "neutral").toLowerCase();
      if (k === "r" || k === "oh" || k === "w-oo" || k === "woo") return;
      if (!MAP[k]) return;

      const t = setTimeout(() => setMouth(k), Math.max(0, (start || 0) * 1000));
      timers.push(t);
    });
    audioEl.addEventListener("ended", () => timers.forEach(clearTimeout), { once: true });
  }

  function driveByRMS(audioEl) {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioCtx.resume();

      const src = audioCtx.createMediaElementSource(audioEl);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
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
                       "aa"; // previously "oh" — blocked

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

  async function play(url, opts = {}) {
    preload();
    ensureMouth();
    layout();

    const mouth = ensureMouth();
    mouth.classList.add("talking");
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
  function setOffset(dx, dy) { offset.x = dx|0; offset.y = dy|0; layout(); }

  // Re-layout on avatar/image size changes
  function watchImage() {
    const img = getChipImage();
    if (!img) return;
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => layout()).observe(img);
    } else {
      // Fallback: relayout when image loads
      if (!img.complete) img.addEventListener("load", () => layout(), { once: true });
      window.addEventListener("resize", layout);
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    preload();
    ensureMouth();
    watchImage();
    // Try a couple of times to catch late layout
    layout();
    setTimeout(layout, 50);
    setTimeout(layout, 150);
    // Show neutral immediately
    setMouth("neutral");
  });

  global.ChipViseme = { play, layout, setAnchor, setSize, setOffset, map: MAP, path: PATH };
})(window);
