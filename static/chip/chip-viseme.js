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
