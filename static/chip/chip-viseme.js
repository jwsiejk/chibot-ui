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
    l:"mouth_l.png"
  };

  const anchor={x:.535,y:.525};
  const size={w:.20,h:.20*(76/219)};
  const offset={x:-28,y:16};

  let mouth=null, ctx=null, raf=0;

  function stage(){
    return document.getElementById("chipBox") ||
           document.querySelector(".chip-box") ||
           document.body;
  }
  function chip(){
    return document.getElementById("chipImage") ||
           document.querySelector("#chipImage,.chip-image");
  }

  function ensure(){
    if (!mouth) {
      mouth = document.getElementById("chipMouthImg") || document.createElement("img");
      mouth.id = "chipMouthImg";
      mouth.alt = "";
      mouth.style.position = "absolute";
      mouth.style.pointerEvents = "none";
      mouth.style.zIndex = "1000";
    }
    const st = stage();
    if (st && !mouth.isConnected) st.appendChild(mouth);
    return mouth;
  }

  function layout(){
    const img=chip(), st=stage(), el=ensure(); if(!img||!st) return;
    const ir=img.getBoundingClientRect(), sr=st.getBoundingClientRect();
    if(!ir.width||!ir.height){ requestAnimationFrame(layout); return; }

    let w=ir.width*size.w, h=ir.width*size.h;
    if(w<60){ const s=60/w; w=60; h*=s; }

    const cx=(ir.left-sr.left)+ir.width*anchor.x+offset.x;
    const cy=(ir.top -sr.top )+ir.height*anchor.y+offset.y;

    el.style.left=cx+"px";
    el.style.top =cy+"px";
    el.style.width = w+"px";
    el.style.height= h+"px";
  }

  function setMouth(k){
    ensure().src = PATH + (MAP[k] || MAP.neutral);
  }
  function reset(){
    setMouth("neutral");
    ensure().classList.remove("talking");
  }

  function schedule(timeline, audioEl){
    if (!Array.isArray(timeline) || !timeline.length || !audioEl) return;
    const timers=[];
    timeline.forEach(o=>{
      const key=((o && (o.viseme||o.value||o.v))||"neutral").toString().toLowerCase();
      const norm = (key==="oh"||key==="oo"||key==="w-oo"||key==="woo") ? "uh" : key;
      const file = MAP[norm] ? norm : "neutral";
      const at = Math.max(0, (o.start || o.t || 0) * 1000);
      timers.push(setTimeout(()=> setMouth(file), at));
    });
    audioEl.addEventListener("ended",()=> timers.forEach(clearTimeout), { once:true });
  }

  function rms(a){
    try{
      if(!ctx) ctx=new (window.AudioContext||window.webkitAudioContext)(); ctx.resume();
      const s=ctx.createMediaElementSource(a), an=ctx.createAnalyser(); an.fftSize=512; s.connect(an); an.connect(ctx.destination);
      const d=new Uint8Array(an.fftSize); let last="neutral";
      (function loop(){
        an.getByteTimeDomainData(d); let sum=0; for(let i=0;i<d.length;i++){ const v=(d[i]-128)/128; sum+=v*v; }
        const r=Math.sqrt(sum/d.length); const k=r<.03?"m":r<.06?"ee":"aa";
        if(MAP[k]&&k!==last){ setMouth(k); last=k; }
        raf=requestAnimationFrame(loop);
      })();
      a.addEventListener("ended",()=>cancelAnimationFrame(raf),{once:true});
    }catch(e){ console.warn("RMS failed",e); }
  }

  async function play(url,opt={}){
    ensure(); layout(); setMouth("neutral"); ensure().classList.add("talking");
    if(!url) return false;
    const a=new Audio(url); a.crossOrigin="anonymous";
    if (Array.isArray(opt.visemes) && opt.visemes.length) schedule(opt.visemes, a); else rms(a);
    a.addEventListener("ended",reset,{once:true}); a.addEventListener("error",reset,{once:true});
    await a.play(); return true;
  }

  function watch(){
    const img=chip(); if(!img) return;
    if("ResizeObserver" in window) new ResizeObserver(layout).observe(img);
    else{
      if(!img.complete) img.addEventListener("load",layout,{once:true});
      window.addEventListener("resize",layout);
    }
  }

  window.addEventListener("DOMContentLoaded",()=>{
    ["mouth_neutral.png","mouth_m.png","mouth_ee.png","mouth_aa.png","mouth_f.png","mouth_l.png","mouth_s.png","mouth_uh.png","mouth_d.png"]
      .forEach(f=>{ const i=new Image(); i.src=PATH+f; });
    ensure(); watch(); layout(); setMouth("neutral");
  });

  const api = {
    play, layout, schedule,
    setAnchor:(x,y)=>{ anchor.x=x; anchor.y=y; layout(); },
    setSize:(w,h)=>{ size.w=w; size.h=h; layout(); },
    setOffset:(x,y)=>{ offset.x=x|0; offset.y=y|0; layout(); },
    map:MAP, path:PATH
  };

  g.ChipViseme = api;
  g.chipViseme = api;
})(window);
