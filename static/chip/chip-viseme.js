// chip-viseme.js — stable build (blocks r/oh/woo, neutral on load, anchors to chip image)

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

  // ---- Placement (percent of chip image) ----
  const anchor = { x: 0.535, y: 0.525 };               // where on the face the mouth centers
  const size   = { w: 0.22,  h: 0.22 * (76 / 219) };    // keep PNG aspect (76x219)
  const offset = { x: -28,   y: 16 };                   // pixel nudge (left, down)
  const MIN_W  = 120;                                   // px floor so it never gets too tiny

  let audioCtx = null;
  let rafId = null;
  let mouthImg = null;

  // ----- Helpers -----
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
    for (const f of Object.values(MAP)) { const i = new Image(); i.src = PATH + f; }
  }
  function ensureMouth() {
    if (!mouthImg) {
      mouthImg = document.createElement("img");
      mouthImg.id = "chipMouthImg";
      mouthImg.alt = "";
      mouthImg.style.position = "absolute";
      mouthImg.style.pointerEvents = "none";
      mouthImg.style.zIndex = "1000";
    }
    if (!mouthImg.isConnected) getStage().appendChild(mouthImg);
    return mouthImg;
  }

  // ----- Layout -----
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
    if (w < MIN_W) { const s = MIN_W / w; w = MIN_W; h *= s; }

    const cx = (rImg.left - rStage.left) + rImg.width  * anchor.x + offset.x;
    const cy = (rImg.top  - rStage.top)  + rImg.height * anchor.y + offset*
