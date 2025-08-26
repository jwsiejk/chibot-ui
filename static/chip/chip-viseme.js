;(function(g){
  const PATH="/static/chip/img/visemes/";
  const MAP={
    neutral:"mouth_neutral.png",
    m:"mouth_m.png",
    ee:"mouth_ee.png",
    aa:"mouth_aa.png",
    f:"mouth_f.png",
    s:"mouth_s.png",
    uh:"mouth_uh.png",
    d:"mouth_d.png",
    l:"mouth_l.png",
    r:"mouth_r.png",
    oh:"mouth_oh.png",
    woo:"mouth_woo.png"
  };

  // Default anchor roughly around the mouth region on chip.png
  const anchor={x:.535,y:.525};

  let mounted=false;
  let rig, faceEl, mouthEl;

  function ensure(){
    if (mounted) return mouthEl;
    rig = document.getElementById("chipRig");
    if (!rig){
      // create if missing
      rig = document.createElement("div");
      rig.id="chipRig";
      rig.className="chip-rig";
      document.body.appendChild(rig);
    }
    faceEl = rig.querySelector("#chipFace");
    if (!faceEl){
      faceEl = document.createElement("img");
      faceEl.id="chipFace";
      faceEl.alt="Chip";
      faceEl.src="/static/chip/img/chip.png";
      rig.appendChild(faceEl);
    }
    mouthEl = rig.querySelector("#chipMouth");
    if (!mouthEl){
      mouthEl = document.createElement("img");
      mouthEl.id="chipMouth";
      mouthEl.className="mouth";
      mouthEl.alt="Mouth";
      mouthEl.src= PATH + MAP.neutral;
      rig.appendChild(mouthEl);
    }
    mounted=true;
    return mouthEl;
  }

  function setMouth(k){
    ensure().src = PATH + (MAP[k] || MAP.neutral);
  }

  function reset(){
    setMouth("neutral");
    if (rig) rig.classList.remove("talking");
  }

  function schedule(timeline, audioEl){
    if (!Array.isArray(timeline) || !timeline.length || !audioEl) return;
    const timers=[];
    const startAt = performance.now();
    if (rig) rig.hidden=false, rig.classList.add("talking");
    // Apply updates aligned to audio currentTime when possible
    const scheduleOne = (ms, key) => {
      const t = Math.max(0, ms);
      timers.push(setTimeout(() => setMouth(key), t));
    };
    timeline.forEach(step => scheduleOne(step.t, step.k));
    // Reset at end
    const endMs = Math.max(...timeline.map(s => s.t)) + 300;
    timers.push(setTimeout(() => {
      reset();
      if (rig) rig.hidden=true;
    }, endMs));

    const cancel = () => { timers.forEach(clearTimeout); reset(); if (rig) rig.hidden=true; };
    // Cancel on audio end/pause
    audioEl.addEventListener("ended", cancel, {once:true});
    audioEl.addEventListener("error", cancel, {once:true});
    return cancel;
  }

  // Public: play audio from base64 and drive visemes
  async function playBase64(audioB64, timeline){
    try {
      const blob = b64ToBlob(audioB64, "audio/mpeg");
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      const cancel = schedule(timeline, audio);
      await audio.play();
      await new Promise(r => { audio.onended = r; audio.onerror = r; });
      if (cancel) cancel();
      URL.revokeObjectURL(url);
    } catch(e){
      console.warn("ChipViseme.playBase64 error", e);
    }
  }

  function b64ToBlob(b64Data, contentType='audio/mpeg', sliceSize=512) {
    const byteCharacters = atob(b64Data);
    const byteArrays = [];
    for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
      const slice = byteCharacters.slice(offset, offset + sliceSize);
      const byteNumbers = new Array(slice.length);
      for (let i = 0; i < slice.length; i++) {
        byteNumbers[i] = slice.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      byteArrays.push(byteArray);
    }
    return new Blob(byteArrays, {type: contentType});
  }

  const api = { setMouth, reset, playBase64 };
  g.ChipViseme = api;
  g.chipViseme = api;
})(window);
