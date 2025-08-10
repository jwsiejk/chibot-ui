// chip-viseme.js – Handles viseme-to-mouth image swapping for Chip

// Viseme map: maps ElevenLabs viseme names to image filenames
const visemeMap = {
  "aa": "mouth_aa.png",
  "d": "mouth_d.png",
  "ee": "mouth_ee.png",
  "f": "mouth_f.png",
  "l": "mouth_l.png",
  "m": "mouth_m.png",
  "oh": "mouth_oh.png",
  "r": "mouth_r.png",
  "s": "mouth_s.png",
  "uh": "mouth_uh.png",
  "w-oo": "mouth_woo.png",
  "neutral": "mouth_neutral.png"
};

// Called after audio is returned and ready to play
export function syncVisemes(visemes, audioElement) {
  const mouthImg = document.getElementById("mouth");
  if (!mouthImg || !visemes || visemes.length === 0) return;

  // Preload viseme images
  Object.values(visemeMap).forEach(src => {
    const img = new Image();
    img.src = `/static/chip/img/visemes/${src}`;
  });

  // Schedule viseme swaps
  visemes.forEach(({ viseme, start }) => {
    const filename = visemeMap[viseme.toLowerCase()];
    if (!filename) return;

    setTimeout(() => {
      mouthImg.src = `/static/chip/img/visemes/${filename}`;
    }, start * 1000); // convert seconds to ms
  });

  // Reset to neutral after playback
  audioElement.addEventListener("ended", () => {
    mouthImg.src = "/static/chip/img/visemes/mouth_neutral.png";
  });
}
