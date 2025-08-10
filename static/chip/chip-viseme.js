// chip-viseme.js — persists your size/offset + neutral on load + blocks r/oh/woo

;(function (global) {
  const PATH = "/static/chip/img/visemes/";

  // Viseme map (r/oh/woo removed)
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

  // Anchor on chip image (percent of image)
  const anchor = { x: 0.535, y: 0.525 };

  // YOUR defaults: ~22% of chip width, keep PNG aspect (76/219 ≈ 0.347)
  const size   = { w: 0.22, h: 0.22 * (76 / 219) };

  // YOUR nudges: left & down (px)
  const offset = { x: -28, y: 16 };

  // Optional: don't let mouth get too tiny
  const MIN_W = 120; // px

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
    Object.values(MAP).forEach(f => { const i = new Image(); i.src = PATH + f; });
  }

  function ensureMouth() {
    if (!mouthImg) {
      mouthImg = document.createElement("img");
      mouthImg.id = "chipMouthImg";
      mouthImg.alt = "";
      mouthImg.style.pointerEvents = "none";
      mouthImg.style.position = "absolute";
      mouthImg.style.zIndex = "1000"; // CSS also sets this, belt & suspenders
    }
    if (!mouthImg.isConnected) getStage().appendChild(mouthImg);
    return mouthImg;
  }

  function layout() {
    const img = getChipImage();
    const stage = getStage();
    const el = ensureMouth();
    if (!img || !stage || !el) return;

    const rImg = img.getBoundingClientRect();
    const rStage = stage.getBoundingClientRect();
    if (!rImg.width || !rImg.height) { requestAnimationFrame(layout); return; }

    let w = rImg.width * size.w;
    let h = rImg.width * size.h;

    // Minimum size guard
    if (w < MIN_W) {
      const scale = MIN_W / w;
      w = MIN_W;
      h = h * scale;
    }

    const cx = (rImg.left - rStage.left) + rImg.width  * anchor.x + offset.x;
    const cy = (rImg.top  - rStage.top)  + rImg.height * anchor.y + offset.y;

    el.style.left = `${cx}px`;
    el.style.top  = `${cy}px`;
    el.style.width  = `${w}px`;
    el.style.height = `${h}px`; // preserves aspect from constants; change to 'auto' if you prefer
  }

  function setMouth(key) {
    ensureMouth().src = PATH + (MAP[key] || MAP.neutral);
  }

  function reset() {
    setMouth("neutral");
    ensureMouth().classList.remove("talking");
  }

  function scheduleVisemes(visemes, audioEl) {
    const timers = [];
    for (const item of visemes) {
      const k = (item?.viseme || "neutral").toLowerCase();
      if (k === "r" || k === "oh" || k === "w-oo" || k === "woo") continue;
      if (!MAP[k]) continue;
      const t = setTimeout(() => setMouth(k), Math.max(0, (item.start || 0) * 1000));
      timers.push(t);
    }
    audioEl.addEventListener("ended", () => timers.forEach(clearTimeout), { once: true });
