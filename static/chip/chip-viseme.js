;(function(g){
  const PATH="/static/chip/img/visemes/";
  const MAP={neutral:"mouth_neutral.png",m:"mouth_m.png",ee:"mouth_ee.png",aa:"mouth_aa.png",f:"mouth_f.png",l:"mouth_l.png",s:"mouth_s.png",uh:"mouth_uh.png",d:"mouth_d.png"};
  const anchor={x:.535,y:.525};
  const size={w:.18,h:.18*(76/219)};     // keeps your PNG aspect
  const offset={x:-28,y:16};              // left & down
  let mouth=null,ctx=null,raf=0;

  function stage(){return document.getElementById("chipBox")||document.querySelector(".chip-box")||document.body}
  function chip(){return document.getElementById("chipImage")||document.querySelector("#chipImage,.chip-image")}

  function ensure(){
    if(!mouth){
      mouth=document.createElement("img");
      mouth.id="chipMouthImg"; mouth.alt="";
      mouth.style.position="absolute"; mouth.style.pointerEvents="none"; mouth.style.zIndex="1000";
      stage().appendChild(mouth);
    }
    if(!mouth.isConnected) stage().appendChild(mouth);
    return mouth;
  }

  function layout(){
    const img=chip(), st=stage(), el=ensure(); if(!img||!st) return;
    const ir=img.getBoundingClientRect(), sr=st.getBoundingClientRect();
    if(!ir.width||!ir.height){ requestAnimationFrame(layout); return; }
    let w=ir.width*size.w, h=ir.width*size.h; if(w<120){const s=60/w; w=60; h*=s;}
    const cx=(ir.left-sr.left)+ir.width*anchor.x+offset.x, cy=(ir.top-sr.top)+ir.height*anchor.y+offset.y;
    el.style.left=cx+"px"; el.style.top=cy+"px"; el.style.width=w+"px"; el.style.height=h+"px";
  }

  function setMouth(k){ ensure().src=PATH+(MAP[k]||MAP.neutral); }
  function reset(){ setMouth("neutral"); ensure().classList.remove("talking"); }

  function visemes(v,a){
    const t=[]; v.forEach(o=>{
      const k=(o?.viseme||"neutral").toLowerCase();
      if(k==="r"||k==="oh"||k==="w-oo"||k==="woo"||!MAP[k]) return;
      t.push(setTimeout(()=>setMouth(k), Math.max(0,(o.start||0)*1000)));
    });
    a.addEventListener("ended",()=>t.forEach(clearTimeout),{once:true});
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
    (Array.isArray(opt.visemes)&&opt.visemes.length)?visemes(opt.visemes,a):rms(a);
    a.addEventListener("ended",reset,{once:true}); a.addEventListener("error",reset,{once:true});
    await a.play(); return true;
  }

  function watch(){
    const img=chip(); if(!img) return;
    if("ResizeObserver" in window) new ResizeObserver(layout).observe(img);
    else{ if(!img.complete) img.addEventListener("load",layout,{once:true}); window.addEventListener("resize",layout); }
  }

  window.addEventListener("DOMContentLoaded",()=>{
    ["mouth_neutral.png","mouth_m.png","mouth_ee.png","mouth_aa.png","mouth_f.png","mouth_l.png","mouth_s.png","mouth_uh.png","mouth_d.png"].forEach(f=>{const i=new Image(); i.src=PATH+f;});
    ensure(); watch(); layout(); setMouth("neutral");
  });

  g.ChipViseme={play,layout,setAnchor:(x,y)=>{anchor.x=x;anchor.y=y;layout();},setSize:(w,h)=>{size.w=w;size.h=h;layout();},setOffset:(x,y)=>{offset.x=x|0;offset.y=y|0;layout();},map:MAP,path:PATH};
})(window);
